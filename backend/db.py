"""SQLite: schema, connection, single writer thread.

Wszystkie zapisy idą przez pojedynczy writer thread (queue.Queue),
żeby uniknąć write-lock contention między watchdog observerami
a HTTP/WS handlerami. Zapytania read-only mogą iść w dowolnym wątku
przez konstrukcję connect-per-query (sqlite3 jest thread-safe dla
różnych połączeń).
"""
from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS libraries (
    id           INTEGER PRIMARY KEY,
    path         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    added_at     INTEGER NOT NULL,
    last_scan_at INTEGER
);

CREATE TABLE IF NOT EXISTS images (
    id            INTEGER PRIMARY KEY,
    library_id    INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    rel_path      TEXT NOT NULL,
    sha1          TEXT,
    mtime         INTEGER NOT NULL,
    size_bytes    INTEGER NOT NULL,
    width         INTEGER,
    height        INTEGER,
    source_kind   TEXT,
    prompt        TEXT,
    negative      TEXT,
    model_name    TEXT,
    sampler       TEXT,
    steps         INTEGER,
    cfg           REAL,
    seed          INTEGER,
    raw_metadata  TEXT,
    indexed_at    INTEGER NOT NULL,
    UNIQUE(library_id, rel_path)
);
CREATE INDEX IF NOT EXISTS idx_images_library ON images(library_id);
CREATE INDEX IF NOT EXISTS idx_images_mtime   ON images(mtime DESC);
CREATE INDEX IF NOT EXISTS idx_images_model   ON images(model_name);
CREATE INDEX IF NOT EXISTS idx_images_sha1    ON images(sha1);

CREATE TABLE IF NOT EXISTS loras (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS image_loras (
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    lora_id  INTEGER NOT NULL REFERENCES loras(id),
    strength REAL,
    PRIMARY KEY (image_id, lora_id)
);
CREATE INDEX IF NOT EXISTS idx_image_loras_lora ON image_loras(lora_id);

CREATE TABLE IF NOT EXISTS file_ops (
    id         INTEGER PRIMARY KEY,
    ts         INTEGER NOT NULL,
    op         TEXT NOT NULL,
    library_id INTEGER,
    from_path  TEXT,
    to_path    TEXT,
    success    INTEGER NOT NULL,
    error      TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS images_fts USING fts5(
    prompt, negative, model_name,
    content='images', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS images_ai AFTER INSERT ON images BEGIN
    INSERT INTO images_fts(rowid, prompt, negative, model_name)
    VALUES (new.id, new.prompt, new.negative, new.model_name);
END;
CREATE TRIGGER IF NOT EXISTS images_ad AFTER DELETE ON images BEGIN
    INSERT INTO images_fts(images_fts, rowid, prompt, negative, model_name)
    VALUES ('delete', old.id, old.prompt, old.negative, old.model_name);
END;
CREATE TRIGGER IF NOT EXISTS images_au AFTER UPDATE ON images BEGIN
    INSERT INTO images_fts(images_fts, rowid, prompt, negative, model_name)
    VALUES ('delete', old.id, old.prompt, old.negative, old.model_name);
    INSERT INTO images_fts(rowid, prompt, negative, model_name)
    VALUES (new.id, new.prompt, new.negative, new.model_name);
END;
"""


def init_schema(db_path: Path) -> None:
    """Utwórz tabele/indeksy/triggery jeśli ich nie ma."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript("PRAGMA journal_mode=WAL;")
    con.executescript(SCHEMA)
    con.commit()
    con.close()


@dataclass
class Writer:
    """Handle do writer threada. Zwracany przez start_writer()."""
    queue: queue.Queue
    thread: threading.Thread
    db_path: Path


_SENTINEL = object()


def start_writer(db_path: Path) -> Writer:
    """Wystartuj wątek pisarza. Zlecenia idą przez queue.put((fn, args, result_q))."""
    q: queue.Queue = queue.Queue()

    def loop() -> None:
        con = sqlite3.connect(db_path)
        con.execute("PRAGMA foreign_keys=ON;")
        try:
            while True:
                item = q.get()
                if item is _SENTINEL:
                    return
                fn, args, kwargs, result_q = item
                try:
                    result = fn(con, *args, **kwargs)
                    con.commit()
                    result_q.put(("ok", result))
                except Exception as exc:  # noqa: BLE001
                    con.rollback()
                    result_q.put(("err", exc))
        finally:
            con.close()

    t = threading.Thread(target=loop, daemon=True, name="db-writer")
    t.start()
    return Writer(queue=q, thread=t, db_path=db_path)


def stop_writer(w: Writer) -> None:
    w.queue.put(_SENTINEL)
    w.thread.join(timeout=5)


def _submit(w: Writer, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Wyślij operację do writer threada i poczekaj na wynik."""
    result_q: queue.Queue = queue.Queue(maxsize=1)
    w.queue.put((fn, args, kwargs, result_q))
    status, value = result_q.get()
    if status == "err":
        raise value
    return value


# ---------- writer-side operacje ----------

def _add_library(con: sqlite3.Connection, *, path: str, name: str) -> int:
    cur = con.execute(
        "INSERT INTO libraries (path, name, added_at) VALUES (?, ?, ?)",
        (path, name, int(time.time())),
    )
    return cur.lastrowid


def add_library(w: Writer, *, path: str, name: str) -> int:
    return _submit(w, _add_library, path=path, name=name)


def _upsert_image(
    con: sqlite3.Connection, *,
    library_id: int,
    rel_path: str,
    sha1: str | None,
    mtime: int,
    size_bytes: int,
    width: int | None,
    height: int | None,
    source_kind: str | None,
    prompt: str | None,
    negative: str | None,
    model_name: str | None,
    sampler: str | None,
    steps: int | None,
    cfg: float | None,
    seed: int | None,
    raw_metadata: str | None,
    loras: list[tuple[str, float | None]],
) -> int:
    """Insert lub update obrazu. Rebuilduje image_loras junction."""
    now = int(time.time())
    con.execute(
        """
        INSERT INTO images (
            library_id, rel_path, sha1, mtime, size_bytes,
            width, height, source_kind, prompt, negative,
            model_name, sampler, steps, cfg, seed, raw_metadata, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(library_id, rel_path) DO UPDATE SET
            sha1=excluded.sha1, mtime=excluded.mtime, size_bytes=excluded.size_bytes,
            width=excluded.width, height=excluded.height,
            source_kind=excluded.source_kind, prompt=excluded.prompt, negative=excluded.negative,
            model_name=excluded.model_name, sampler=excluded.sampler, steps=excluded.steps,
            cfg=excluded.cfg, seed=excluded.seed, raw_metadata=excluded.raw_metadata,
            indexed_at=excluded.indexed_at
        """,
        (library_id, rel_path, sha1, mtime, size_bytes,
         width, height, source_kind, prompt, negative,
         model_name, sampler, steps, cfg, seed, raw_metadata, now),
    )
    img_id = con.execute(
        "SELECT id FROM images WHERE library_id=? AND rel_path=?",
        (library_id, rel_path),
    ).fetchone()[0]
    # rebuild junction
    con.execute("DELETE FROM image_loras WHERE image_id=?", (img_id,))
    for lora_name, strength in loras:
        con.execute("INSERT OR IGNORE INTO loras (name) VALUES (?)", (lora_name,))
        lora_id = con.execute("SELECT id FROM loras WHERE name=?", (lora_name,)).fetchone()[0]
        con.execute(
            "INSERT INTO image_loras (image_id, lora_id, strength) VALUES (?, ?, ?)",
            (img_id, lora_id, strength),
        )
    return img_id


def upsert_image(w: Writer, **kwargs: Any) -> int:
    return _submit(w, _upsert_image, **kwargs)


def _delete_image(con: sqlite3.Connection, *, image_id: int) -> None:
    con.execute("DELETE FROM images WHERE id=?", (image_id,))


def delete_image(w: Writer, *, image_id: int) -> None:
    _submit(w, _delete_image, image_id=image_id)


def _log_file_op(
    con: sqlite3.Connection, *,
    op: str,
    library_id: int | None,
    from_path: str | None,
    to_path: str | None,
    success: bool,
    error: str | None,
) -> None:
    con.execute(
        """INSERT INTO file_ops (ts, op, library_id, from_path, to_path, success, error)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (int(time.time()), op, library_id, from_path, to_path, int(success), error),
    )


def log_file_op(w: Writer, **kwargs: Any) -> None:
    _submit(w, _log_file_op, **kwargs)


# ---------- read-only helpery (mogą działać w dowolnym wątku) ----------

def readonly(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con
