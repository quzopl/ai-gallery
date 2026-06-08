"""Skaner: initial recursive scan + watchdog observer + debouncer.

Watchdog dorzucany w Task 8.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Callable

from . import db, metadata

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
PROGRESS_EVERY = 50


def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk_images(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if Path(name).suffix.lower() in IMAGE_EXTS:
                found.append(Path(dirpath) / name)
    return found


def _existing_index(db_path: Path, library_id: int) -> dict[str, tuple[int, int]]:
    con = db.readonly(db_path)
    rows = con.execute(
        "SELECT rel_path, mtime, size_bytes FROM images WHERE library_id=?",
        (library_id,),
    ).fetchall()
    con.close()
    return {r["rel_path"]: (r["mtime"], r["size_bytes"]) for r in rows}


def _full_parse_and_upsert(
    *, writer: db.Writer, library_id: int, library_root: Path, file: Path,
) -> None:
    stat = file.stat()
    sha1 = _sha1_file(file)
    md = metadata.extract(file)
    rel = file.relative_to(library_root).as_posix()
    db.upsert_image(
        writer,
        library_id=library_id,
        rel_path=rel,
        sha1=sha1,
        mtime=int(stat.st_mtime),
        size_bytes=stat.st_size,
        width=md["width"],
        height=md["height"],
        source_kind=md["source_kind"],
        prompt=md["prompt"],
        prompt_json=md["prompt_json"],
        negative=md["negative"],
        model_name=md["model_name"],
        sampler=md["sampler"],
        steps=md["steps"],
        cfg=md["cfg"],
        seed=md["seed"],
        raw_metadata=md["raw_metadata"],
        loras=md["loras"],
    )


def scan_library(
    *,
    library_id: int,
    library_root: Path,
    writer: db.Writer,
    db_path: Path,
    on_progress: Callable[[int, int], None] | None = None,
    on_image_added: Callable[[int], None] | None = None,
    on_image_removed: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, int]:
    """Pełny skan biblioteki. Zwraca summary {added, updated, removed, total}."""
    library_root = Path(library_root).resolve()
    files = _walk_images(library_root)
    total = len(files)
    existing = _existing_index(db_path, library_id)
    rel_paths_seen: set[str] = set()
    added = updated = 0

    for i, file in enumerate(files):
        if cancel_event is not None and cancel_event.is_set():
            break
        rel = file.relative_to(library_root).as_posix()
        rel_paths_seen.add(rel)
        try:
            stat = file.stat()
        except OSError:
            continue
        prev = existing.get(rel)
        if prev is not None and prev[0] == int(stat.st_mtime) and prev[1] == stat.st_size:
            pass
        else:
            try:
                _full_parse_and_upsert(
                    writer=writer, library_id=library_id,
                    library_root=library_root, file=file,
                )
                if prev is None:
                    added += 1
                else:
                    updated += 1
            except Exception:  # noqa: BLE001
                pass
        if on_progress and (i % PROGRESS_EVERY == 0 or i == total - 1):
            on_progress(i + 1, total)

    removed = 0
    for rel in set(existing) - rel_paths_seen:
        con = db.readonly(db_path)
        row = con.execute(
            "SELECT id FROM images WHERE library_id=? AND rel_path=?",
            (library_id, rel),
        ).fetchone()
        con.close()
        if row:
            db.delete_image(writer, image_id=row["id"])
            removed += 1
            if on_image_removed:
                on_image_removed(row["id"])

    _set_last_scan(writer, library_id=library_id)

    return {"added": added, "updated": updated, "removed": removed, "total": total}


def _set_last_scan_op(con, *, library_id: int) -> None:
    con.execute("UPDATE libraries SET last_scan_at=? WHERE id=?",
                (int(time.time()), library_id))


def _set_last_scan(writer: db.Writer, *, library_id: int) -> None:
    db._submit(writer, _set_last_scan_op, library_id=library_id)  # type: ignore[arg-type]


# ---------- watchdog observer ----------

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


class _DebouncedHandler(FileSystemEventHandler):
    """Zbiera eventy do bucketów per ścieżka, flush co debounce_ms."""

    def __init__(
        self, *,
        library_id: int, library_root: Path,
        writer: db.Writer, db_path: Path,
        on_image_added: Callable[[int], None],
        on_image_removed: Callable[[int], None],
        debounce_ms: int,
    ) -> None:
        self.library_id = library_id
        self.library_root = library_root.resolve()
        self.writer = writer
        self.db_path = db_path
        self.on_added = on_image_added
        self.on_removed = on_image_removed
        self.debounce_s = debounce_ms / 1000.0
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, float]] = {}
        self._stop = threading.Event()
        self._flusher = threading.Thread(target=self._flush_loop, daemon=True,
                                         name=f"watchdog-flush-{library_id}")
        self._flusher.start()

    def _enqueue(self, kind: str, src_path: str) -> None:
        p = Path(src_path)
        if p.suffix.lower() not in IMAGE_EXTS:
            return
        with self._lock:
            self._pending[str(p)] = (kind, time.monotonic())

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue("upsert", event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue("upsert", event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue("delete", event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._enqueue("delete", event.src_path)
        self._enqueue("upsert", event.dest_path)

    def _flush_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.debounce_s)
            now = time.monotonic()
            to_process: list[tuple[str, str]] = []
            with self._lock:
                ready = [(p, kind) for p, (kind, ts) in self._pending.items()
                         if now - ts >= self.debounce_s]
                for p, _ in ready:
                    del self._pending[p]
                to_process = ready
            for path_str, kind in to_process:
                self._process(Path(path_str), kind)

    def _process(self, path: Path, kind: str) -> None:
        try:
            rel = path.resolve().relative_to(self.library_root).as_posix()
        except ValueError:
            return
        if kind == "delete":
            con = db.readonly(self.db_path)
            row = con.execute(
                "SELECT id FROM images WHERE library_id=? AND rel_path=?",
                (self.library_id, rel),
            ).fetchone()
            con.close()
            if row:
                db.delete_image(self.writer, image_id=row["id"])
                self.on_removed(row["id"])
        else:
            if not path.is_file():
                return
            try:
                stat = path.stat()
            except OSError:
                return
            con = db.readonly(self.db_path)
            row = con.execute(
                "SELECT id, mtime, size_bytes FROM images "
                "WHERE library_id=? AND rel_path=?",
                (self.library_id, rel),
            ).fetchone()
            con.close()
            if row and row["mtime"] == int(stat.st_mtime) and row["size_bytes"] == stat.st_size:
                return
            try:
                _full_parse_and_upsert(
                    writer=self.writer,
                    library_id=self.library_id,
                    library_root=self.library_root,
                    file=path,
                )
            except Exception:  # noqa: BLE001
                return
            con = db.readonly(self.db_path)
            new_row = con.execute(
                "SELECT id FROM images WHERE library_id=? AND rel_path=?",
                (self.library_id, rel),
            ).fetchone()
            con.close()
            if new_row:
                self.on_added(new_row["id"])

    def stop(self) -> None:
        self._stop.set()
        self._flusher.join(timeout=2)


class WatchHandle:
    def __init__(self, observer: Observer, handler: _DebouncedHandler) -> None:
        self.observer = observer
        self.handler = handler


def start_watchdog(
    *,
    library_id: int,
    library_root: Path,
    writer: db.Writer,
    db_path: Path,
    on_image_added: Callable[[int], None],
    on_image_removed: Callable[[int], None],
    debounce_ms: int = 500,
) -> WatchHandle:
    handler = _DebouncedHandler(
        library_id=library_id, library_root=Path(library_root),
        writer=writer, db_path=db_path,
        on_image_added=on_image_added, on_image_removed=on_image_removed,
        debounce_ms=debounce_ms,
    )
    observer = Observer()
    observer.schedule(handler, str(library_root), recursive=True)
    observer.start()
    return WatchHandle(observer=observer, handler=handler)


def stop_watchdog(h: WatchHandle) -> None:
    h.handler.stop()
    h.observer.stop()
    h.observer.join(timeout=2)
