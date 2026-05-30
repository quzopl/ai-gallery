"""FastAPI backend dla AI Gallery."""
from __future__ import annotations

import asyncio
import base64
import os
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, scanner, thumbs

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

    def shutdown(self) -> None:
        for h in self.observers.values():
            scanner.stop_watchdog(h)
        db.stop_writer(self.writer)


state = AppState()


def _broadcast(msg: dict) -> None:
    """Sync broadcast — wywołuje async send w event loopie głównym."""
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

    direction = "DESC" if sort == "mtime_desc" else "ASC"
    op = "<" if direction == "DESC" else ">"
    if cursor:
        sort_val, last_id = _decode_cursor(cursor)
        where.append(f"(i.mtime, i.id) {op} (?, ?)")
        params.extend([int(sort_val), last_id])

    sql = (
        "SELECT DISTINCT i.id, i.library_id, i.rel_path, i.sha1, i.mtime, "
        "i.width, i.height, i.source_kind, i.model_name "
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
    con.close()
    out = dict(row)
    out["loras"] = loras
    return out


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
