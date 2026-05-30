"""FastAPI backend dla AI Gallery."""
from __future__ import annotations

import asyncio
import base64
import os
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fastapi import WebSocket, WebSocketDisconnect

from . import db, fileops, scanner, thumbs

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
WORK = Path(os.environ.get("AI_GALLERY_WORK_DIR", str(ROOT / ".work")))
WORK.mkdir(parents=True, exist_ok=True)
DB_PATH = WORK / "gallery.db"
THUMBS_DIR = WORK / "thumbs"
THUMBS_DIR.mkdir(parents=True, exist_ok=True)


class AppState:
    def __init__(self) -> None:
        db.init_schema(DB_PATH)
        self.writer = db.start_writer(DB_PATH)
        self.observers: dict[int, scanner.WatchHandle] = {}
        self.scan_cancel: dict[int, threading.Event] = {}
        self.ws_clients: set = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def shutdown(self) -> None:
        for h in self.observers.values():
            scanner.stop_watchdog(h)
        db.stop_writer(self.writer)


state = AppState()


def _broadcast(msg: dict) -> None:
    """Sync broadcast — wywołuje async send w event loopie głównym."""
    loop = state._loop
    if loop is None or not loop.is_running():
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
        except Exception:  # noqa: BLE001
            return
    for ws in list(state.ws_clients):
        try:
            asyncio.run_coroutine_threadsafe(ws.send_json(msg), loop)
        except Exception:  # noqa: BLE001
            pass


app = FastAPI(title="AI Gallery")


@app.on_event("shutdown")
def _on_shutdown() -> None:
    state.shutdown()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------- libraries ----------

class AddLibraryRequest(BaseModel):
    path: str
    name: str | None = None


@app.get("/api/libraries")
def list_libraries() -> list[dict]:
    con = db.readonly(DB_PATH)
    rows = con.execute(
        """SELECT l.id, l.path, l.name, l.added_at, l.last_scan_at,
                  (SELECT COUNT(*) FROM images i WHERE i.library_id=l.id) AS image_count
           FROM libraries l ORDER BY l.added_at"""
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.post("/api/libraries")
def add_library(req: AddLibraryRequest) -> dict:
    p = Path(req.path).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise HTTPException(400, f"Ścieżka nie istnieje lub nie jest katalogiem: {p}")
    if not os.access(p, os.R_OK):
        raise HTTPException(400, f"Brak prawa odczytu: {p}")
    con = db.readonly(DB_PATH)
    existing = con.execute("SELECT id FROM libraries WHERE path=?", (str(p),)).fetchone()
    con.close()
    if existing:
        raise HTTPException(409, f"Biblioteka już dodana: {p}")
    name = req.name or p.name
    lib_id = db.add_library(state.writer, path=str(p), name=name)
    _start_initial_scan(lib_id, p)
    _start_observer(lib_id, p)
    return {"id": lib_id, "name": name, "path": str(p)}


def _start_initial_scan(library_id: int, root: Path) -> None:
    cancel = threading.Event()
    state.scan_cancel[library_id] = cancel

    def run() -> None:
        def on_progress(scanned: int, total: int) -> None:
            _broadcast({"type": "scan_progress",
                        "library_id": library_id,
                        "scanned": scanned, "total": total})

        def on_added(img_id: int) -> None:
            _broadcast({"type": "image_added", "image_id": img_id})

        def on_removed(img_id: int) -> None:
            _broadcast({"type": "image_removed", "image_id": img_id})

        result = scanner.scan_library(
            library_id=library_id, library_root=root,
            writer=state.writer, db_path=DB_PATH,
            on_progress=on_progress,
            on_image_added=on_added,
            on_image_removed=on_removed,
            cancel_event=cancel,
        )
        _broadcast({"type": "scan_done", "library_id": library_id, **result})

    threading.Thread(target=run, daemon=True, name=f"scan-{library_id}").start()


def _start_observer(library_id: int, root: Path) -> None:
    if library_id in state.observers:
        scanner.stop_watchdog(state.observers.pop(library_id))
    handle = scanner.start_watchdog(
        library_id=library_id, library_root=root,
        writer=state.writer, db_path=DB_PATH,
        on_image_added=lambda iid: _broadcast({"type": "image_added", "image_id": iid}),
        on_image_removed=lambda iid: _broadcast({"type": "image_removed", "image_id": iid}),
    )
    state.observers[library_id] = handle


# ---------- images ----------

def _encode_cursor(sort_val: str, image_id: int) -> str:
    raw = f"{sort_val}|{image_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cur: str) -> tuple[str, int]:
    raw = base64.urlsafe_b64decode(cur.encode("ascii")).decode("utf-8")
    sort_val, image_id = raw.rsplit("|", 1)
    return sort_val, int(image_id)


@app.get("/api/images")
def list_images(
    library_id: int | None = None,
    model: str | None = None,
    lora: str | None = None,
    q: str | None = None,
    favorite: bool = False,
    tag: list[str] = Query(default=[]),  # type: ignore[assignment]
    sort: Literal["mtime_desc", "mtime_asc"] = "mtime_desc",
    cursor: str | None = None,
    limit: int = 200,
) -> dict:
    limit = max(1, min(limit, 500))
    where: list[str] = []
    params: list = []
    join = ""
    if library_id is not None:
        where.append("i.library_id = ?"); params.append(library_id)
    if model:
        where.append("i.model_name = ?"); params.append(model)
    if lora:
        join += " JOIN image_loras il ON il.image_id = i.id JOIN loras lo ON lo.id = il.lora_id"
        where.append("lo.name = ?"); params.append(lora)
    if q:
        join += " JOIN images_fts fts ON fts.rowid = i.id"
        where.append("images_fts MATCH ?"); params.append(q)
    if favorite:
        where.append("i.is_favorite = 1")

    # Tag filter: obraz musi mieć WSZYSTKIE wybrane tagi (AND).
    # Robimy to przez podzapytanie z GROUP BY HAVING COUNT(DISTINCT).
    tag_names = [t for t in (tag or []) if t]
    if tag_names:
        placeholders = ",".join("?" * len(tag_names))
        where.append(
            f"i.id IN (SELECT it.image_id FROM image_tags it "
            f"JOIN tags t ON t.id = it.tag_id "
            f"WHERE t.name IN ({placeholders}) "
            f"GROUP BY it.image_id HAVING COUNT(DISTINCT t.id) = ?)"
        )
        params.extend(tag_names)
        params.append(len(tag_names))

    direction = "DESC" if sort == "mtime_desc" else "ASC"
    op = "<" if direction == "DESC" else ">"
    if cursor:
        sort_val, last_id = _decode_cursor(cursor)
        where.append(f"(i.mtime, i.id) {op} (?, ?)")
        params.extend([int(sort_val), last_id])

    sql = (
        "SELECT DISTINCT i.id, i.library_id, i.rel_path, i.sha1, i.mtime, "
        "i.width, i.height, i.source_kind, i.model_name, i.is_favorite "
        f"FROM images i{join}"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY i.mtime {direction}, i.id {direction} LIMIT ?"
    params.append(limit + 1)

    con = db.readonly(DB_PATH)
    rows = con.execute(sql, params).fetchall()
    con.close()
    items = [dict(r) for r in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = _encode_cursor(str(last["mtime"]), last["id"])
    return {"items": items, "next_cursor": next_cursor}


@app.get("/api/images/{image_id}")
def get_image(image_id: int) -> dict:
    con = db.readonly(DB_PATH)
    row = con.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404)
    loras = [dict(r) for r in con.execute(
        "SELECT lo.name, il.strength FROM image_loras il "
        "JOIN loras lo ON lo.id = il.lora_id WHERE il.image_id=?",
        (image_id,),
    ).fetchall()]
    tags = [r[0] for r in con.execute(
        "SELECT t.name FROM image_tags it JOIN tags t ON t.id = it.tag_id "
        "WHERE it.image_id=? ORDER BY t.name",
        (image_id,),
    ).fetchall()]
    con.close()
    out = dict(row)
    out["loras"] = loras
    out["tags"] = tags
    return out


from fastapi.responses import FileResponse, Response


@app.get("/api/images/{image_id}/thumb")
def get_thumb(image_id: int) -> Response:
    con = db.readonly(DB_PATH)
    row = con.execute(
        "SELECT i.sha1, i.rel_path, l.path AS lib_path FROM images i "
        "JOIN libraries l ON l.id = i.library_id WHERE i.id=?",
        (image_id,),
    ).fetchone()
    con.close()
    if not row:
        raise HTTPException(404)
    src = Path(row["lib_path"]) / row["rel_path"]
    if not src.exists():
        raise HTTPException(404, "źródło nie istnieje")
    out = thumbs.get_or_generate(src, sha1=row["sha1"], cache_dir=THUMBS_DIR)
    return FileResponse(out, media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/images/{image_id}/file")
def get_file(image_id: int) -> FileResponse:
    con = db.readonly(DB_PATH)
    row = con.execute(
        "SELECT i.rel_path, l.path AS lib_path FROM images i "
        "JOIN libraries l ON l.id = i.library_id WHERE i.id=?",
        (image_id,),
    ).fetchone()
    con.close()
    if not row:
        raise HTTPException(404)
    src = Path(row["lib_path"]) / row["rel_path"]
    if not src.exists():
        raise HTTPException(404)
    return FileResponse(src)


@app.get("/api/facets")
def facets() -> dict:
    con = db.readonly(DB_PATH)
    models = [dict(r) for r in con.execute(
        "SELECT model_name AS name, COUNT(*) AS count FROM images "
        "WHERE model_name IS NOT NULL GROUP BY model_name ORDER BY count DESC"
    ).fetchall()]
    loras = [dict(r) for r in con.execute(
        "SELECT lo.name, COUNT(*) AS count FROM image_loras il "
        "JOIN loras lo ON lo.id = il.lora_id "
        "GROUP BY lo.name ORDER BY count DESC"
    ).fetchall()]
    con.close()
    return {"models": models, "loras": loras}


@app.post("/api/libraries/{library_id}/rescan")
def rescan_library(library_id: int) -> dict:
    con = db.readonly(DB_PATH)
    row = con.execute("SELECT path FROM libraries WHERE id=?", (library_id,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(404)
    _start_initial_scan(library_id, Path(row["path"]))
    _sweep_thumbs()
    return {"status": "scan_started"}


@app.delete("/api/libraries/{library_id}")
def delete_library(library_id: int) -> dict:
    con = db.readonly(DB_PATH)
    exists = con.execute("SELECT 1 FROM libraries WHERE id=?", (library_id,)).fetchone()
    con.close()
    if not exists:
        raise HTTPException(404)
    h = state.observers.pop(library_id, None)
    if h:
        scanner.stop_watchdog(h)
    def _del(con):
        con.execute("DELETE FROM libraries WHERE id=?", (library_id,))
    db._submit(state.writer, _del)  # type: ignore[arg-type]
    return {"status": "deleted"}


def _sweep_thumbs() -> int:
    """Usuń sieroty z cache miniatur."""
    con = db.readonly(DB_PATH)
    known = {r["sha1"] for r in con.execute(
        "SELECT DISTINCT sha1 FROM images WHERE sha1 IS NOT NULL"
    )}
    con.close()
    return thumbs.sweep(THUMBS_DIR, known_sha1s=known)


@app.on_event("startup")
def _on_startup() -> None:
    """Po starcie: re-attach observery dla istniejących bibliotek + sweep."""
    con = db.readonly(DB_PATH)
    libs = [dict(r) for r in con.execute("SELECT id, path FROM libraries")]
    con.close()
    for lib in libs:
        p = Path(lib["path"])
        if p.exists() and p.is_dir():
            _start_observer(lib["id"], p)
    _sweep_thumbs()


class RenameRequest(BaseModel):
    new_name: str


class MoveRequest(BaseModel):
    to_library_id: int
    to_rel_path: str


class FavoriteRequest(BaseModel):
    value: bool


class TagsRequest(BaseModel):
    tags: list[str]


@app.get("/api/tags")
def list_tags() -> list[dict]:
    """Wszystkie tagi z licznością obrazów."""
    con = db.readonly(DB_PATH)
    rows = con.execute(
        "SELECT t.id, t.name, COUNT(it.image_id) AS count "
        "FROM tags t LEFT JOIN image_tags it ON it.tag_id = t.id "
        "GROUP BY t.id ORDER BY count DESC, t.name"
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.delete("/api/tags/{tag_id}")
def delete_tag(tag_id: int) -> dict:
    con = db.readonly(DB_PATH)
    exists = con.execute("SELECT 1 FROM tags WHERE id=?", (tag_id,)).fetchone()
    con.close()
    if not exists:
        raise HTTPException(404)
    db.delete_tag(state.writer, tag_id=tag_id)
    return {"status": "deleted"}


@app.post("/api/images/{image_id}/tags")
def set_image_tags(image_id: int, req: TagsRequest) -> dict:
    """Zastąp WSZYSTKIE tagi obrazu listą `tags`."""
    con = db.readonly(DB_PATH)
    row = con.execute("SELECT 1 FROM images WHERE id=?", (image_id,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(404)
    db.set_image_tags(state.writer, image_id=image_id, tag_names=req.tags)
    _broadcast({"type": "image_changed", "image_id": image_id})
    return {"status": "ok", "tags": [t.strip() for t in req.tags if t.strip()]}


@app.post("/api/images/{image_id}/favorite")
def set_favorite(image_id: int, req: FavoriteRequest) -> dict:
    con = db.readonly(DB_PATH)
    row = con.execute("SELECT 1 FROM images WHERE id=?", (image_id,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(404)
    db.set_favorite(state.writer, image_id=image_id, value=req.value)
    _broadcast({"type": "image_changed", "image_id": image_id})
    return {"status": "ok", "is_favorite": req.value}


def _image_source_path(image_id: int) -> tuple[Path, int, str]:
    con = db.readonly(DB_PATH)
    row = con.execute(
        "SELECT i.library_id, i.rel_path, l.path AS lib_path "
        "FROM images i JOIN libraries l ON l.id=i.library_id WHERE i.id=?",
        (image_id,),
    ).fetchone()
    con.close()
    if not row:
        raise HTTPException(404)
    return Path(row["lib_path"]) / row["rel_path"], row["library_id"], row["rel_path"]


@app.delete("/api/images/{image_id}")
def delete_image(image_id: int) -> dict:
    src, lib_id, rel = _image_source_path(image_id)
    error: str | None = None
    success = False
    try:
        fileops.move_to_trash(src)
        db.delete_image(state.writer, image_id=image_id)
        _broadcast({"type": "image_removed", "image_id": image_id})
        success = True
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    finally:
        db.log_file_op(state.writer, op="delete", library_id=lib_id,
                       from_path=str(src), to_path=None,
                       success=success, error=error)
    if not success:
        raise HTTPException(500, error or "delete failed")
    return {"status": "deleted"}


@app.post("/api/images/{image_id}/rename")
def rename_image(image_id: int, req: RenameRequest) -> dict:
    src, lib_id, rel = _image_source_path(image_id)
    error: str | None = None; success = False; dst: Path | None = None; new_rel = rel
    try:
        dst = fileops.rename(src, new_name=req.new_name)
        con = db.readonly(DB_PATH)
        lib_path = con.execute("SELECT path FROM libraries WHERE id=?", (lib_id,)).fetchone()["path"]
        con.close()
        new_rel = dst.resolve().relative_to(Path(lib_path).resolve()).as_posix()

        def _upd(con):
            con.execute("UPDATE images SET rel_path=? WHERE id=?", (new_rel, image_id))
        db._submit(state.writer, _upd)  # type: ignore[arg-type]
        _broadcast({"type": "image_changed", "image_id": image_id})
        success = True
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        error = str(exc)
        raise HTTPException(400, error)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        raise HTTPException(500, error)
    finally:
        db.log_file_op(state.writer, op="rename", library_id=lib_id,
                       from_path=str(src), to_path=str(dst) if dst else None,
                       success=success, error=error)
    return {"status": "renamed", "new_rel_path": new_rel}


@app.post("/api/images/{image_id}/move")
def move_image(image_id: int, req: MoveRequest) -> dict:
    src, src_lib_id, _ = _image_source_path(image_id)
    con = db.readonly(DB_PATH)
    dst_lib = con.execute("SELECT path FROM libraries WHERE id=?", (req.to_library_id,)).fetchone()
    con.close()
    if not dst_lib:
        raise HTTPException(404, "docelowa biblioteka nie istnieje")
    error: str | None = None; success = False; dst: Path | None = None
    try:
        dst = fileops.move(src, dst_library_root=Path(dst_lib["path"]),
                           dst_rel_path=req.to_rel_path)

        def _upd(con):
            con.execute(
                "UPDATE images SET library_id=?, rel_path=? WHERE id=?",
                (req.to_library_id, req.to_rel_path, image_id),
            )
        db._submit(state.writer, _upd)  # type: ignore[arg-type]
        _broadcast({"type": "image_changed", "image_id": image_id})
        success = True
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc))
    finally:
        db.log_file_op(state.writer, op="move", library_id=src_lib_id,
                       from_path=str(src), to_path=str(dst) if dst else None,
                       success=success, error=error)
    return {"status": "moved"}


@app.get("/api/audit")
def audit(limit: int = 100) -> list[dict]:
    con = db.readonly(DB_PATH)
    rows = con.execute(
        "SELECT * FROM file_ops ORDER BY id DESC LIMIT ?", (min(limit, 1000),)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    state._loop = asyncio.get_event_loop()
    state.ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.ws_clients.discard(ws)


@app.get("/api/browse")
def browse(path: str | None = None) -> dict:
    """Lista podkatalogów dla wybranej ścieżki. Domyślnie HOME."""
    p = Path(path).expanduser().resolve() if path else Path.home()
    if not p.exists() or not p.is_dir():
        raise HTTPException(400, f"Nie istnieje lub nie jest katalogiem: {p}")
    if not os.access(p, os.R_OK):
        raise HTTPException(403, f"Brak prawa odczytu: {p}")
    dirs = []
    try:
        for entry in sorted(p.iterdir(), key=lambda e: e.name.lower()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            try:
                readable = os.access(entry, os.R_OK)
            except OSError:
                readable = False
            dirs.append({"name": entry.name, "readable": readable})
    except PermissionError:
        pass
    parent = str(p.parent) if p != p.parent else None
    return {"path": str(p), "parent": parent, "dirs": dirs}


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
