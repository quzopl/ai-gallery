# AI Gallery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lokalna FastAPI + vanilla JS aplikacja do przeglądania bibliotek AI-generated images (ComfyUI/A1111/SD): rekurencyjny skan, ekstrakcja promptów/parametrów do SQLite (z FTS5), live updates przez watchdog+WebSocket, operacje plikowe z XDG Trash.

**Architecture:** Jeden uvicorn proces. Główny event loop FastAPI obsługuje HTTP + WebSocket. Per-library `watchdog.Observer` w wątku daemon. Pojedynczy SQLite writer thread przyjmuje zlecenia z `queue.Queue` żeby uniknąć write-lock contention. Frontend: vanilla JS, wirtualna siatka + lazy thumbs.

**Tech Stack:** Python 3.12 (przez uv), FastAPI, uvicorn, Pillow, watchdog, sqlite3 (stdlib); vanilla HTML/JS/CSS bez build step.

**Spec:** [`/home/bart/wsl/ai-gallery/docs/superpowers/specs/2026-05-30-ai-gallery-design.md`](../specs/2026-05-30-ai-gallery-design.md)

**Pre-flight:**
- Project root: `/home/bart/wsl/ai-gallery`
- Wszystkie ścieżki w tym planie są względne do project root, chyba że oznaczone jako absolutne.
- Backend testy: `pytest`, uruchamiane z project root jako `./.venv/bin/pytest tests/...`
- Frontend testowany manualnie (vanilla JS, brak build step → brak unit testów JS).
- Po Task 1 możesz uruchamiać `./run.sh` i otwierać `http://127.0.0.1:8000` żeby zobaczyć stan na bieżąco.

---

## File Structure

Po wykonaniu całego planu drzewo wygląda tak:

```
/home/bart/wsl/ai-gallery/
├── backend/
│   ├── __init__.py        # pusty marker pakietu
│   ├── server.py          # FastAPI app, routes REST, /ws, mount frontend
│   ├── db.py              # SQLite connection, init_schema, writer thread
│   ├── metadata.py        # ComfyUI/A1111 metadata extraction + LoRA parsing
│   ├── thumbs.py          # WebP thumbnail generation + cache + sweep
│   ├── fileops.py         # XDG Trash delete, move, rename, walidacja, audit
│   └── scanner.py         # initial scan + watchdog observer + debouncer
├── frontend/
│   ├── index.html         # 3-kolumnowy layout, topbar, modale
│   ├── app.js             # virtual grid, WebSocket, filtry, hotkeys
│   └── style.css          # layout, kafelki, panele, modale, lightbox
├── tests/
│   ├── __init__.py
│   ├── conftest.py        # fixtures: tmp DB, sample PNG-i z metadata
│   ├── fixtures/images/   # sample .png/.jpg z ComfyUI/A1111 metadata
│   ├── test_metadata.py
│   ├── test_thumbs.py
│   ├── test_fileops.py
│   ├── test_scanner.py
│   └── test_server.py     # smoke E2E z TestClient
├── .work/                 # gitignored (runtime: gallery.db, thumbs/)
├── docs/superpowers/      # spec + plan (już istnieją)
├── .gitignore
├── pyproject.toml         # optional, dla pytest config
├── requirements.txt
├── run.sh
└── README.md
```

Każdy plik backendowy ma jedną odpowiedzialność. `db.py` jest jedynym miejscem które dotyka SQLite bezpośrednio — inne moduły dostają funkcje wysokopoziomowe (`db.upsert_image(...)`, `db.list_images(...)`). To pozwala trzymać schemat i writer thread w jednym miejscu.

---

## Task 1: Scaffolding (project skeleton + venv + hello server)

**Files:**
- Create: `requirements.txt`
- Create: `run.sh`
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `backend/__init__.py`
- Create: `backend/server.py`
- Create: `frontend/index.html`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `README.md`

- [ ] **Step 1: Init git repo i utworzenie struktury katalogów**

```bash
cd /home/bart/wsl/ai-gallery
git init -b main
mkdir -p backend frontend tests/fixtures/images .work
```

- [ ] **Step 2: Utwórz `requirements.txt`**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
pillow>=10.4.0
watchdog>=5.0.0
pytest>=8.0.0
httpx>=0.27.0
```

(`httpx` potrzebne dla `fastapi.testclient.TestClient`.)

- [ ] **Step 3: Utwórz `run.sh` (executable)**

```bash
#!/usr/bin/env bash
# AI Gallery — uruchom FastAPI + WebSocket
set -e
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "Brak 'uv'. Zainstaluj: https://docs.astral.sh/uv/"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo ">> Tworzę venv (Python 3.12)…"
  uv venv --python 3.12 .venv
fi

echo ">> Synchronizuję zależności…"
uv pip install -r requirements.txt

PORT="${PORT:-8000}"
echo ""
echo ">> http://127.0.0.1:${PORT}"
echo ""
exec .venv/bin/python -m uvicorn backend.server:app --host 127.0.0.1 --port "${PORT}" --reload
```

```bash
chmod +x run.sh
```

- [ ] **Step 4: Utwórz `.gitignore`**

```
.venv/
.work/
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
```

- [ ] **Step 5: Utwórz `pyproject.toml`**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "-q"
```

- [ ] **Step 6: Utwórz pliki Python pakietu**

`backend/__init__.py` — pusty.

`tests/__init__.py` — pusty.

`tests/conftest.py`:

```python
"""Wspólne fixtures dla testów."""
from __future__ import annotations
import pytest
```

- [ ] **Step 7: Utwórz `backend/server.py` (minimal hello)**

```python
"""FastAPI backend dla AI Gallery."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

app = FastAPI(title="AI Gallery")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Mount frontend na końcu, html=True serwuje index.html dla "/"
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
```

- [ ] **Step 8: Utwórz `frontend/index.html` (minimal)**

```html
<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <title>AI Gallery</title>
</head>
<body>
  <h1>AI Gallery</h1>
  <p>Scaffolding works.</p>
</body>
</html>
```

- [ ] **Step 9: Utwórz `README.md`**

```markdown
# AI Gallery

Lokalna apka do przeglądania bibliotek AI-generated images
(ComfyUI / A1111 / SD).

## Uruchomienie

```bash
./run.sh
```

Otwórz http://127.0.0.1:8000.

## Dokumentacja

- Spec: `docs/superpowers/specs/2026-05-30-ai-gallery-design.md`
- Plan implementacji: `docs/superpowers/plans/2026-05-30-ai-gallery.md`
```

- [ ] **Step 10: Uruchom i sprawdź że serwer odpowiada**

```bash
./run.sh &
sleep 8
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/ | head -3
pkill -f "uvicorn backend.server"
```

Expected output zawiera `{"status":"ok"}` i `<!DOCTYPE html>`.

- [ ] **Step 11: Commit**

```bash
git add .
git commit -m "scaffold: FastAPI + uv + minimal hello page"
```

---

## Task 2: DB module (schema, connection, writer thread)

**Files:**
- Create: `backend/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Napisz failing test**

`tests/test_db.py`:

```python
"""Testy backend/db.py — schema + writer thread."""
from __future__ import annotations
import sqlite3
import time
from pathlib import Path

import pytest

from backend import db


@pytest.fixture
def tmpdb(tmp_path: Path) -> Path:
    return tmp_path / "gallery.db"


def test_init_schema_creates_tables(tmpdb: Path) -> None:
    db.init_schema(tmpdb)
    con = sqlite3.connect(tmpdb)
    names = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    )}
    con.close()
    expected = {"libraries", "images", "loras", "image_loras", "file_ops",
                "images_fts", "images_fts_config", "images_fts_data",
                "images_fts_docsize", "images_fts_idx"}
    assert expected.issubset(names), f"Missing: {expected - names}"


def test_writer_thread_inserts_library(tmpdb: Path) -> None:
    db.init_schema(tmpdb)
    writer = db.start_writer(tmpdb)
    try:
        lib_id = db.add_library(writer, path="/tmp/x", name="X")
        assert lib_id > 0
        # readonly query (główny wątek):
        con = sqlite3.connect(tmpdb)
        row = con.execute("SELECT path, name FROM libraries WHERE id=?", (lib_id,)).fetchone()
        con.close()
        assert row == ("/tmp/x", "X")
    finally:
        db.stop_writer(writer)


def test_fts_trigger_indexes_prompt(tmpdb: Path) -> None:
    db.init_schema(tmpdb)
    writer = db.start_writer(tmpdb)
    try:
        lib_id = db.add_library(writer, path="/tmp/y", name="Y")
        img_id = db.upsert_image(
            writer,
            library_id=lib_id,
            rel_path="a.png",
            sha1="deadbeef",
            mtime=int(time.time()),
            size_bytes=100,
            width=512, height=512,
            source_kind="comfyui",
            prompt="a photo of a cat sitting on a mat",
            negative=None,
            model_name="flux1-dev",
            sampler=None, steps=None, cfg=None, seed=None,
            raw_metadata="{}",
            loras=[],
        )
        # FTS query:
        con = sqlite3.connect(tmpdb)
        rows = list(con.execute(
            "SELECT rowid FROM images_fts WHERE images_fts MATCH 'cat'"
        ))
        con.close()
        assert rows == [(img_id,)]
    finally:
        db.stop_writer(writer)
```

- [ ] **Step 2: Uruchom test, sprawdź że pada**

```bash
./.venv/bin/pytest tests/test_db.py -v
```

Expected: ImportError / brak modułu.

- [ ] **Step 3: Implementacja `backend/db.py`**

```python
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
```

- [ ] **Step 4: Uruchom test**

```bash
./.venv/bin/pytest tests/test_db.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py tests/test_db.py
git commit -m "db: schema + FTS5 triggers + writer thread"
```

---

## Task 3: Metadata module — ComfyUI extraction

**Files:**
- Create: `backend/metadata.py`
- Create: `tests/test_metadata.py`
- Create: `tests/fixtures/images/comfyui_sample.png` (generated by test setup)

- [ ] **Step 1: Napisz fixture builder + failing test**

`tests/test_metadata.py`:

```python
"""Testy ekstrakcji metadata z PNG (ComfyUI + A1111) i EXIF."""
from __future__ import annotations
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from backend import metadata

FIXTURES = Path(__file__).parent / "fixtures" / "images"


def _make_png(path: Path, text_chunks: dict[str, str]) -> None:
    """Zapisz mały PNG z tEXt chunkami."""
    img = Image.new("RGB", (8, 8), "white")
    info = PngInfo()
    for k, v in text_chunks.items():
        info.add_text(k, v)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", pnginfo=info)


@pytest.fixture
def comfy_png(tmp_path: Path) -> Path:
    workflow = {
        "nodes": [
            {"type": "CheckpointLoaderSimple", "widgets_values": ["flux1-dev.safetensors"]},
            {"type": "KSampler", "widgets_values": ["dpm++_2m", "normal", 28, 7.0, 12345, 1.0]},
        ]
    }
    prompt = {
        "3": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "flux1-dev.safetensors"}},
        "5": {"class_type": "KSampler", "inputs": {
            "seed": 12345, "steps": 28, "cfg": 7.0,
            "sampler_name": "dpm++_2m", "scheduler": "normal",
        }},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a portrait of a cat"}},
    }
    p = tmp_path / "comfy.png"
    _make_png(p, {"workflow": json.dumps(workflow), "prompt": json.dumps(prompt)})
    return p


def test_extract_comfyui_prompt(comfy_png: Path) -> None:
    m = metadata.extract(comfy_png)
    assert m["source_kind"] == "comfyui"
    assert "cat" in (m["prompt"] or "")
    assert m["model_name"] == "flux1-dev.safetensors"
    assert m["sampler"] == "dpm++_2m"
    assert m["steps"] == 28
    assert m["cfg"] == 7.0
    assert m["seed"] == 12345


def test_extract_unknown_png(tmp_path: Path) -> None:
    p = tmp_path / "plain.png"
    Image.new("RGB", (4, 4), "red").save(p)
    m = metadata.extract(p)
    assert m["source_kind"] is None
    assert m["prompt"] is None
    assert m["width"] == 4 and m["height"] == 4
```

- [ ] **Step 2: Uruchom test — powinno paść (brak modułu)**

```bash
./.venv/bin/pytest tests/test_metadata.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implementuj `backend/metadata.py` (część ComfyUI + szkielet)**

```python
"""Ekstrakcja metadata z plików obrazów AI-generated.

Wspierane źródła:
  - ComfyUI: PNG tEXt chunks 'workflow' (JSON UI) + 'prompt' (JSON exec graph)
  - A1111  : PNG tEXt 'parameters' lub EXIF UserComment (JPG)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PIL import Image

# Wynikowy słownik. Wszystkie pola opcjonalne.
EMPTY: dict[str, Any] = {
    "source_kind": None,
    "prompt": None,
    "negative": None,
    "model_name": None,
    "sampler": None,
    "steps": None,
    "cfg": None,
    "seed": None,
    "raw_metadata": None,
    "loras": [],          # list[tuple[str, float|None]]
    "width": None,
    "height": None,
}


def extract(path: Path) -> dict[str, Any]:
    """Zwróć słownik metadata. Pola których nie wykryto → None / []."""
    out = dict(EMPTY)
    try:
        with Image.open(path) as img:
            out["width"], out["height"] = img.size
            text = getattr(img, "text", {}) or {}
            if "prompt" in text or "workflow" in text:
                _parse_comfyui(text, out)
            elif "parameters" in text:
                _parse_a1111(text["parameters"], out)
            # EXIF UserComment fallback robimy w Task 4
    except Exception:  # noqa: BLE001
        # corrupt/unreadable — zwracamy co mamy (puste pola)
        pass
    return out


def _parse_comfyui(text: dict[str, str], out: dict[str, Any]) -> None:
    out["source_kind"] = "comfyui"
    raw = {k: text[k] for k in ("workflow", "prompt") if k in text}
    out["raw_metadata"] = json.dumps(raw)

    prompt_json: dict[str, Any] | None = None
    if "prompt" in text:
        try:
            prompt_json = json.loads(text["prompt"])
        except json.JSONDecodeError:
            prompt_json = None

    if prompt_json:
        out["prompt"] = _comfy_positive_prompt(prompt_json)
        out["negative"] = _comfy_negative_prompt(prompt_json)
        _comfy_sampler_fields(prompt_json, out)
        out["model_name"] = _comfy_model_name(prompt_json)
        out["loras"] = _comfy_loras(prompt_json)


def _comfy_positive_prompt(graph: dict[str, Any]) -> str | None:
    """Najprościej: weź tekst z CLIPTextEncode podłączonego do KSampler.positive.

    Bez pełnego trace'owania linków robimy heurystykę: pierwszy CLIPTextEncode
    którego tekst NIE wygląda na negatywny.
    """
    candidates: list[str] = []
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
            text = (node.get("inputs") or {}).get("text")
            if isinstance(text, str) and text.strip():
                candidates.append(text)
    if not candidates:
        return None
    # Pierwszy nie-negatywny:
    for t in candidates:
        if not _looks_negative(t):
            return t
    return candidates[0]


def _comfy_negative_prompt(graph: dict[str, Any]) -> str | None:
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
            text = (node.get("inputs") or {}).get("text")
            if isinstance(text, str) and _looks_negative(text):
                return text
    return None


_NEG_HINTS = ("blurry", "low quality", "lowres", "bad anatomy", "watermark",
              "negative", "deformed")


def _looks_negative(text: str) -> bool:
    t = text.lower()
    return any(h in t for h in _NEG_HINTS)


def _comfy_sampler_fields(graph: dict[str, Any], out: dict[str, Any]) -> None:
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        if "Sampler" in ct or ct.startswith("KSampler"):
            inp = node.get("inputs") or {}
            out["sampler"] = out["sampler"] or _str_or_none(inp.get("sampler_name"))
            out["steps"] = out["steps"] or _int_or_none(inp.get("steps"))
            out["cfg"] = out["cfg"] or _float_or_none(inp.get("cfg"))
            out["seed"] = out["seed"] or _int_or_none(inp.get("seed") or inp.get("noise_seed"))


def _comfy_model_name(graph: dict[str, Any]) -> str | None:
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        if "Checkpoint" in ct or "UNetLoader" in ct or "UNETLoader" in ct:
            inp = node.get("inputs") or {}
            for key in ("ckpt_name", "unet_name", "model_name"):
                v = inp.get(key)
                if isinstance(v, str):
                    return v
    return None


def _comfy_loras(graph: dict[str, Any]) -> list[tuple[str, float | None]]:
    out: list[tuple[str, float | None]] = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        if "Lora" in ct or "LoRA" in ct:
            inp = node.get("inputs") or {}
            name = inp.get("lora_name") or inp.get("name")
            strength = _float_or_none(inp.get("strength_model") or inp.get("strength"))
            if isinstance(name, str):
                out.append((name, strength))
    return out


def _parse_a1111(_params: str, _out: dict[str, Any]) -> None:
    """A1111 'parameters' string — implementacja w Task 4."""
    pass


# ---------- helpery typów ----------

def _str_or_none(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None

def _int_or_none(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

def _float_or_none(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Uruchom test**

```bash
./.venv/bin/pytest tests/test_metadata.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/metadata.py tests/test_metadata.py
git commit -m "metadata: ComfyUI workflow/prompt extraction"
```

---

## Task 4: Metadata — A1111 (PNG `parameters` + EXIF UserComment)

**Files:**
- Modify: `backend/metadata.py`
- Modify: `tests/test_metadata.py`

- [ ] **Step 1: Dodaj failing tests**

W `tests/test_metadata.py` dopisz:

```python
def test_extract_a1111_png(tmp_path: Path) -> None:
    params = (
        "a beautiful sunset over mountains, photorealistic\n"
        "Negative prompt: blurry, low quality\n"
        "Steps: 30, Sampler: DPM++ 2M Karras, CFG scale: 7.5, Seed: 987654321, "
        "Size: 1024x1024, Model hash: abc123, Model: sd_xl_base_1.0, "
        "Lora hashes: \"char_v1: aaa, style_v2: bbb\""
    )
    p = tmp_path / "a1111.png"
    _make_png(p, {"parameters": params})
    m = metadata.extract(p)
    assert m["source_kind"] == "a1111"
    assert "sunset" in m["prompt"]
    assert m["negative"] == "blurry, low quality"
    assert m["steps"] == 30
    assert m["sampler"] == "DPM++ 2M Karras"
    assert m["cfg"] == 7.5
    assert m["seed"] == 987654321
    assert m["model_name"] == "sd_xl_base_1.0"


def test_extract_a1111_jpg_exif(tmp_path: Path) -> None:
    """A1111 JPG zapisuje parameters w EXIF UserComment (tag 0x9286)."""
    import piexif  # not in deps — używamy Pillow EXIF API zamiast
    params = "city street at night\nNegative prompt: cars\nSteps: 20, Sampler: Euler, CFG scale: 5, Seed: 1, Size: 512x512, Model: somemodel"
    p = tmp_path / "a1111.jpg"
    img = Image.new("RGB", (16, 16), "blue")
    # UserComment musi być w specjalnym formacie: 8-byte charset header + tekst
    user_comment = b"UNICODE\x00" + params.encode("utf-16-be")
    exif_dict = img.getexif()
    exif_dict[0x9286] = user_comment
    img.save(p, "JPEG", exif=exif_dict.tobytes())

    m = metadata.extract(p)
    assert m["source_kind"] == "a1111"
    assert "city" in m["prompt"]
    assert m["sampler"] == "Euler"


def test_lora_extraction_from_a1111_prompt(tmp_path: Path) -> None:
    params = (
        "a portrait <lora:char_ohwx:0.8> <lora:style_anime:0.5>\n"
        "Steps: 20, Sampler: Euler, CFG scale: 7, Seed: 1, Size: 512x512, Model: x"
    )
    p = tmp_path / "lora.png"
    _make_png(p, {"parameters": params})
    m = metadata.extract(p)
    names = {n for n, _ in m["loras"]}
    assert names == {"char_ohwx", "style_anime"}
    strengths = dict(m["loras"])
    assert strengths["char_ohwx"] == 0.8
    assert strengths["style_anime"] == 0.5
```

(Usuń import `piexif` z testu — używamy `getexif()` API z Pillow, jest wbudowane.)

- [ ] **Step 2: Uruchom — sprawdź że padają**

```bash
./.venv/bin/pytest tests/test_metadata.py -v
```

Expected: 3 nowe testy padają.

- [ ] **Step 3: Rozszerz `backend/metadata.py`**

Zastąp body `_parse_a1111` i dopisz EXIF fallback + LoRA extraction:

```python
import re

# Linia z parametrami w stylu A1111: "Key: value, Key: value, ..."
# Wartość może być w cudzysłowach z przecinkami w środku ("Lora hashes: \"a, b\"").
_KV_RE = re.compile(r'([A-Za-z][A-Za-z0-9 ]*?):\s*("(?:[^"]|\\")*"|[^,]+?)(?=,\s*[A-Za-z][A-Za-z0-9 ]*?:\s*|$)')

# LoRA w prompcie A1111: <lora:name:strength>
_LORA_RE = re.compile(r'<lora:([^:>]+):([\d.]+)>', re.IGNORECASE)


def _parse_a1111(params: str, out: dict[str, Any]) -> None:
    out["source_kind"] = "a1111"
    out["raw_metadata"] = params
    # Format:
    #   <positive prompt linie>
    #   Negative prompt: <negative>
    #   Steps: ..., Sampler: ..., ...
    lines = params.splitlines()
    # Znajdź indeks 'Negative prompt:' i linii parametrów (zaczyna się od 'Steps:')
    neg_idx = next((i for i, ln in enumerate(lines) if ln.startswith("Negative prompt:")), None)
    param_idx = next((i for i, ln in enumerate(lines) if ln.startswith("Steps:")), None)

    if neg_idx is not None:
        pos = "\n".join(lines[:neg_idx]).strip()
        if param_idx is not None:
            neg = " ".join(lines[neg_idx:param_idx]).removeprefix("Negative prompt:").strip()
        else:
            neg = " ".join(lines[neg_idx:]).removeprefix("Negative prompt:").strip()
    else:
        pos = "\n".join(lines[:param_idx] if param_idx is not None else lines).strip()
        neg = None

    out["prompt"] = pos or None
    out["negative"] = neg or None

    if param_idx is not None:
        kv_line = ", ".join(lines[param_idx:])
        for m in _KV_RE.finditer(kv_line):
            k, v = m.group(1).strip(), m.group(2).strip().strip('"')
            if k == "Steps":
                out["steps"] = _int_or_none(v)
            elif k == "Sampler":
                out["sampler"] = v
            elif k == "CFG scale":
                out["cfg"] = _float_or_none(v)
            elif k == "Seed":
                out["seed"] = _int_or_none(v)
            elif k == "Model":
                out["model_name"] = v

    # LoRA z prompta:
    if pos:
        out["loras"] = [(name, float(strength)) for name, strength in _LORA_RE.findall(pos)]


# Rozszerz extract() o EXIF UserComment fallback:
def extract(path: Path) -> dict[str, Any]:
    out = dict(EMPTY)
    try:
        with Image.open(path) as img:
            out["width"], out["height"] = img.size
            text = getattr(img, "text", {}) or {}
            if "prompt" in text or "workflow" in text:
                _parse_comfyui(text, out)
            elif "parameters" in text:
                _parse_a1111(text["parameters"], out)
            else:
                # EXIF UserComment dla JPG (A1111)
                params = _read_exif_user_comment(img)
                if params:
                    _parse_a1111(params, out)
    except Exception:  # noqa: BLE001
        pass
    return out


def _read_exif_user_comment(img: Image.Image) -> str | None:
    try:
        exif = img.getexif()
    except Exception:  # noqa: BLE001
        return None
    raw = exif.get(0x9286)  # UserComment
    if not raw:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        # 8-byte charset header
        if raw.startswith(b"UNICODE\x00"):
            try:
                return raw[8:].decode("utf-16-be")
            except UnicodeDecodeError:
                return None
        if raw.startswith(b"ASCII\x00\x00\x00"):
            return raw[8:].decode("ascii", errors="replace")
        return raw.decode("utf-8", errors="replace")
    return None
```

- [ ] **Step 4: Uruchom testy**

```bash
./.venv/bin/pytest tests/test_metadata.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/metadata.py tests/test_metadata.py
git commit -m "metadata: A1111 PNG parameters + EXIF UserComment + LoRA from prompt"
```

---

## Task 5: Thumbnails module

**Files:**
- Create: `backend/thumbs.py`
- Create: `tests/test_thumbs.py`

- [ ] **Step 1: Failing test**

`tests/test_thumbs.py`:

```python
"""Testy backend/thumbs.py."""
from __future__ import annotations
from pathlib import Path

import pytest
from PIL import Image

from backend import thumbs


@pytest.fixture
def src_img(tmp_path: Path) -> Path:
    p = tmp_path / "big.png"
    Image.new("RGB", (1024, 768), "green").save(p)
    return p


def test_generate_creates_webp(tmp_path: Path, src_img: Path) -> None:
    cache_dir = tmp_path / "cache"
    out = thumbs.get_or_generate(src_img, sha1="aabbcc", cache_dir=cache_dir, max_size=256)
    assert out.exists()
    assert out.suffix == ".webp"
    with Image.open(out) as im:
        # max_size to dłuższy bok
        assert max(im.size) == 256


def test_generate_is_cached(tmp_path: Path, src_img: Path) -> None:
    cache_dir = tmp_path / "cache"
    p1 = thumbs.get_or_generate(src_img, sha1="x", cache_dir=cache_dir)
    mtime1 = p1.stat().st_mtime
    p2 = thumbs.get_or_generate(src_img, sha1="x", cache_dir=cache_dir)
    assert p1 == p2
    assert p2.stat().st_mtime == mtime1  # nie regenerowane


def test_sweep_removes_orphans(tmp_path: Path, src_img: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "orphan_sha.webp").write_bytes(b"x")
    kept = thumbs.get_or_generate(src_img, sha1="keep", cache_dir=cache_dir)
    removed = thumbs.sweep(cache_dir, known_sha1s={"keep"})
    assert removed == 1
    assert kept.exists()
    assert not (cache_dir / "orphan_sha.webp").exists()
```

- [ ] **Step 2: Uruchom — fail**

```bash
./.venv/bin/pytest tests/test_thumbs.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implementacja `backend/thumbs.py`**

```python
"""Generowanie i cache WebP miniatur."""
from __future__ import annotations

from pathlib import Path

from PIL import Image


def get_or_generate(
    src: Path, *, sha1: str, cache_dir: Path, max_size: int = 384, quality: int = 80
) -> Path:
    """Zwróć ścieżkę do cache'owanej miniatury. Generuj jeśli brak."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{sha1}.webp"
    if out.exists():
        return out
    with Image.open(src) as img:
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        img.save(out, "WEBP", quality=quality, method=4)
    return out


def sweep(cache_dir: Path, *, known_sha1s: set[str]) -> int:
    """Usuń pliki .webp których nazwa (sha1) nie jest w known_sha1s. Zwróć liczbę usuniętych."""
    if not cache_dir.exists():
        return 0
    removed = 0
    for p in cache_dir.iterdir():
        if not p.is_file() or p.suffix != ".webp":
            continue
        if p.stem not in known_sha1s:
            p.unlink()
            removed += 1
    return removed
```

- [ ] **Step 4: Test passes**

```bash
./.venv/bin/pytest tests/test_thumbs.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/thumbs.py tests/test_thumbs.py
git commit -m "thumbs: WebP cache + sweep orphans"
```

---

## Task 6: FileOps — XDG Trash delete + validation + audit

**Files:**
- Create: `backend/fileops.py`
- Create: `tests/test_fileops.py`

- [ ] **Step 1: Failing tests**

`tests/test_fileops.py`:

```python
"""Testy backend/fileops.py — bezpieczne operacje plikowe."""
from __future__ import annotations
import os
from pathlib import Path

import pytest

from backend import fileops


@pytest.fixture
def trash_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Override XDG_DATA_HOME żeby trash poszedł do tmp."""
    data = tmp_path / "xdg_data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    return data


@pytest.fixture
def src_file(tmp_path: Path) -> Path:
    p = tmp_path / "file.png"
    p.write_bytes(b"PNG")
    return p


def test_move_to_trash(trash_home: Path, src_file: Path) -> None:
    fileops.move_to_trash(src_file)
    assert not src_file.exists()
    trash_files = trash_home / "Trash" / "files"
    trash_info = trash_home / "Trash" / "info"
    assert any(p.name.startswith("file") for p in trash_files.iterdir())
    assert any(p.name.startswith("file") and p.suffix == ".trashinfo"
               for p in trash_info.iterdir())


def test_rename_in_place(tmp_path: Path) -> None:
    src = tmp_path / "a.png"
    src.write_bytes(b"x")
    dst = fileops.rename(src, new_name="b.png")
    assert dst == tmp_path / "b.png"
    assert dst.exists()
    assert not src.exists()


def test_rename_rejects_path_separator(tmp_path: Path) -> None:
    src = tmp_path / "a.png"
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="nazwa"):
        fileops.rename(src, new_name="../evil.png")
    with pytest.raises(ValueError):
        fileops.rename(src, new_name="sub/b.png")


def test_rename_rejects_existing_target(tmp_path: Path) -> None:
    src = tmp_path / "a.png"; src.write_bytes(b"a")
    other = tmp_path / "b.png"; other.write_bytes(b"b")
    with pytest.raises(FileExistsError):
        fileops.rename(src, new_name="b.png")


def test_move_to_library(tmp_path: Path) -> None:
    src_lib = tmp_path / "src_lib"
    dst_lib = tmp_path / "dst_lib"
    src_lib.mkdir(); dst_lib.mkdir()
    src = src_lib / "a.png"; src.write_bytes(b"x")
    dst = fileops.move(src, dst_library_root=dst_lib, dst_rel_path="sub/a.png")
    assert dst == dst_lib / "sub" / "a.png"
    assert dst.exists()
    assert not src.exists()


def test_move_rejects_outside_target_library(tmp_path: Path) -> None:
    src_lib = tmp_path / "src_lib"
    dst_lib = tmp_path / "dst_lib"
    src_lib.mkdir(); dst_lib.mkdir()
    src = src_lib / "a.png"; src.write_bytes(b"x")
    with pytest.raises(ValueError, match="poza"):
        fileops.move(src, dst_library_root=dst_lib, dst_rel_path="../outside.png")
```

- [ ] **Step 2: Run — fails**

```bash
./.venv/bin/pytest tests/test_fileops.py -v
```

- [ ] **Step 3: Implementuj `backend/fileops.py`**

```python
"""Bezpieczne operacje plikowe: XDG Trash, rename, move + walidacja.

Audit logowanie i DB sync są obsługiwane przez warstwę server.py
(woła db.log_file_op + db.delete_image/update_image po sukcesie).
"""
from __future__ import annotations

import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


def _xdg_data_home() -> Path:
    raw = os.environ.get("XDG_DATA_HOME")
    if raw:
        return Path(raw)
    return Path.home() / ".local" / "share"


def _trash_dirs() -> tuple[Path, Path]:
    base = _xdg_data_home() / "Trash"
    files = base / "files"
    info = base / "info"
    files.mkdir(parents=True, exist_ok=True)
    info.mkdir(parents=True, exist_ok=True)
    return files, info


def _unique_trash_name(files_dir: Path, name: str) -> str:
    """Jeśli nazwa wzięta, dorzuć .1 .2 .3 ..."""
    candidate = files_dir / name
    if not candidate.exists():
        return name
    stem = Path(name).stem
    suffix = Path(name).suffix
    i = 1
    while True:
        alt = f"{stem}.{i}{suffix}"
        if not (files_dir / alt).exists():
            return alt
        i += 1


def move_to_trash(path: Path) -> Path:
    """Przenieś plik do XDG Trash. Zwróć ścieżkę w koszu."""
    if not path.is_file():
        raise FileNotFoundError(path)
    files_dir, info_dir = _trash_dirs()
    name = _unique_trash_name(files_dir, path.name)
    dst = files_dir / name
    info_path = info_dir / f"{name}.trashinfo"
    deletion_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    info_content = (
        "[Trash Info]\n"
        f"Path={quote(str(path.resolve()))}\n"
        f"DeletionDate={deletion_date}\n"
    )
    info_path.write_text(info_content, encoding="utf-8")
    shutil.move(str(path), str(dst))
    return dst


def rename(path: Path, *, new_name: str) -> Path:
    """Rename w obrębie tego samego folderu. Walidacja nazwy."""
    if "/" in new_name or "\\" in new_name or ".." in new_name.split("/"):
        raise ValueError("nazwa: niedozwolone znaki")
    if new_name.strip() == "" or new_name in (".", ".."):
        raise ValueError("nazwa: pusta lub zarezerwowana")
    dst = path.parent / new_name
    if dst.exists():
        raise FileExistsError(dst)
    os.rename(path, dst)  # POSIX atomic w obrębie partycji
    return dst


def move(path: Path, *, dst_library_root: Path, dst_rel_path: str) -> Path:
    """Przenieś plik do innej biblioteki / podścieżki.

    Wymaga że dst_rel_path NIE wychodzi poza dst_library_root (no ../).
    Tworzy brakujące katalogi. Atomowo jeśli ta sama partycja, inaczej
    shutil.move z rollback semantycznie kontrolowanym przez warstwę
    server.py (tu rzucamy wyjątek przy błędzie, server nie aktualizuje DB).
    """
    dst_library_root = dst_library_root.resolve()
    dst = (dst_library_root / dst_rel_path).resolve()
    # Walidacja: dst musi być w dst_library_root
    try:
        dst.relative_to(dst_library_root)
    except ValueError as exc:
        raise ValueError("target poza biblioteką docelową") from exc
    if dst.exists():
        raise FileExistsError(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dst))
    return dst
```

- [ ] **Step 4: Tests pass**

```bash
./.venv/bin/pytest tests/test_fileops.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/fileops.py tests/test_fileops.py
git commit -m "fileops: XDG Trash + rename + move with validation"
```

---

## Task 7: Scanner — initial recursive scan

**Files:**
- Create: `backend/scanner.py`
- Create: `tests/test_scanner.py`

- [ ] **Step 1: Failing test**

`tests/test_scanner.py`:

```python
"""Testy backend/scanner.py — initial scan + watchdog."""
from __future__ import annotations
import json
import time
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from backend import db, scanner


def _make_comfy_png(path: Path, prompt_text: str = "a cat") -> None:
    info = PngInfo()
    info.add_text("prompt", json.dumps({
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt_text}},
        "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m.safetensors"}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": 1, "steps": 20, "cfg": 7.0,
            "sampler_name": "euler", "scheduler": "normal",
        }},
    }))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "white").save(path, "PNG", pnginfo=info)


@pytest.fixture
def setup_db_writer(tmp_path: Path):
    db_path = tmp_path / "g.db"
    db.init_schema(db_path)
    writer = db.start_writer(db_path)
    yield db_path, writer
    db.stop_writer(writer)


def test_initial_scan_indexes_files(tmp_path: Path, setup_db_writer) -> None:
    db_path, writer = setup_db_writer
    lib_root = tmp_path / "lib"
    _make_comfy_png(lib_root / "a.png", "a cat")
    _make_comfy_png(lib_root / "sub" / "b.png", "a dog")
    lib_id = db.add_library(writer, path=str(lib_root), name="lib")

    progress_events: list[tuple[int, int]] = []

    def on_progress(scanned: int, total: int) -> None:
        progress_events.append((scanned, total))

    result = scanner.scan_library(
        library_id=lib_id, library_root=lib_root,
        writer=writer, db_path=db_path,
        on_progress=on_progress,
    )
    assert result["added"] == 2
    assert result["removed"] == 0
    con = db.readonly(db_path)
    rows = con.execute("SELECT rel_path, prompt FROM images ORDER BY rel_path").fetchall()
    con.close()
    assert [r["rel_path"] for r in rows] == ["a.png", "sub/b.png"]
    assert progress_events  # at least one progress event


def test_rescan_detects_removed(tmp_path: Path, setup_db_writer) -> None:
    db_path, writer = setup_db_writer
    lib_root = tmp_path / "lib"
    _make_comfy_png(lib_root / "a.png")
    _make_comfy_png(lib_root / "b.png")
    lib_id = db.add_library(writer, path=str(lib_root), name="lib")
    scanner.scan_library(library_id=lib_id, library_root=lib_root,
                         writer=writer, db_path=db_path)
    (lib_root / "b.png").unlink()
    result = scanner.scan_library(library_id=lib_id, library_root=lib_root,
                                  writer=writer, db_path=db_path)
    assert result["added"] == 0
    assert result["removed"] == 1


def test_rescan_skips_unchanged(tmp_path: Path, setup_db_writer) -> None:
    db_path, writer = setup_db_writer
    lib_root = tmp_path / "lib"
    _make_comfy_png(lib_root / "a.png")
    lib_id = db.add_library(writer, path=str(lib_root), name="lib")
    scanner.scan_library(library_id=lib_id, library_root=lib_root,
                         writer=writer, db_path=db_path)
    result = scanner.scan_library(library_id=lib_id, library_root=lib_root,
                                  writer=writer, db_path=db_path)
    assert result["added"] == 0
    assert result["updated"] == 0
```

- [ ] **Step 2: Run — fails**

```bash
./.venv/bin/pytest tests/test_scanner.py -v
```

- [ ] **Step 3: Implementuj `backend/scanner.py`**

```python
"""Skaner: initial recursive scan + watchdog observer + debouncer.

Watchdog observer dorzuca eventy do bucket dict per ścieżka, flush_interval
500 ms. Wszystkie zmiany DB idą przez db.Writer (single writer thread).
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
    """rel_path -> (mtime, size_bytes) z DB."""
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
    """Pełny skan biblioteki. Zwraca summary {added, updated, removed}."""
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
            pass  # skip
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
                # Corrupt plik — ignorujemy, kontynuujemy
                pass
        if on_progress and (i % PROGRESS_EVERY == 0 or i == total - 1):
            on_progress(i + 1, total)

    # Cleanup — pliki które zniknęły z FS:
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

    # Update last_scan_at:
    _set_last_scan(writer, library_id=library_id)

    return {"added": added, "updated": updated, "removed": removed, "total": total}


def _set_last_scan_op(con, *, library_id: int) -> None:
    con.execute("UPDATE libraries SET last_scan_at=? WHERE id=?",
                (int(time.time()), library_id))


def _set_last_scan(writer: db.Writer, *, library_id: int) -> None:
    db._submit(writer, _set_last_scan_op, library_id=library_id)  # type: ignore[arg-type]
```

(Watchdog observer dodamy w Task 8.)

- [ ] **Step 4: Test passes**

```bash
./.venv/bin/pytest tests/test_scanner.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/scanner.py tests/test_scanner.py
git commit -m "scanner: initial recursive scan with skip/add/remove detection"
```

---

## Task 8: Scanner — watchdog observer + debouncing

**Files:**
- Modify: `backend/scanner.py`
- Modify: `tests/test_scanner.py`

- [ ] **Step 1: Dodaj failing test dla watchdog**

W `tests/test_scanner.py` dopisz:

```python
def test_watchdog_picks_up_new_file(tmp_path: Path, setup_db_writer) -> None:
    db_path, writer = setup_db_writer
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    lib_id = db.add_library(writer, path=str(lib_root), name="lib")

    added_ids: list[int] = []
    observer = scanner.start_watchdog(
        library_id=lib_id, library_root=lib_root,
        writer=writer, db_path=db_path,
        on_image_added=lambda iid: added_ids.append(iid),
        on_image_removed=lambda iid: None,
        debounce_ms=200,
    )
    try:
        time.sleep(0.2)  # observer up
        _make_comfy_png(lib_root / "live.png", "live cat")
        # Czekaj na debounce + parsing:
        deadline = time.time() + 5
        while time.time() < deadline and not added_ids:
            time.sleep(0.1)
        assert added_ids, "watchdog nie złapał nowego pliku w 5s"
    finally:
        scanner.stop_watchdog(observer)


def test_watchdog_handles_delete(tmp_path: Path, setup_db_writer) -> None:
    db_path, writer = setup_db_writer
    lib_root = tmp_path / "lib"
    _make_comfy_png(lib_root / "x.png")
    lib_id = db.add_library(writer, path=str(lib_root), name="lib")
    scanner.scan_library(library_id=lib_id, library_root=lib_root,
                         writer=writer, db_path=db_path)

    removed_ids: list[int] = []
    observer = scanner.start_watchdog(
        library_id=lib_id, library_root=lib_root,
        writer=writer, db_path=db_path,
        on_image_added=lambda iid: None,
        on_image_removed=lambda iid: removed_ids.append(iid),
        debounce_ms=200,
    )
    try:
        time.sleep(0.2)
        (lib_root / "x.png").unlink()
        deadline = time.time() + 5
        while time.time() < deadline and not removed_ids:
            time.sleep(0.1)
        assert removed_ids
    finally:
        scanner.stop_watchdog(observer)
```

- [ ] **Step 2: Run — fails (no watchdog API in scanner)**

```bash
./.venv/bin/pytest tests/test_scanner.py::test_watchdog_picks_up_new_file -v
```

- [ ] **Step 3: Dopisz watchdog do `backend/scanner.py`**

Dodaj na końcu pliku:

```python
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
        # path -> ("upsert"|"delete", scheduled_at)
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
                for p, kind in ready:
                    del self._pending[p]
                to_process = ready
            for path_str, kind in to_process:
                self._process(Path(path_str), kind)

    def _process(self, path: Path, kind: str) -> None:
        try:
            rel = path.resolve().relative_to(self.library_root).as_posix()
        except ValueError:
            return  # poza biblioteką
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
        else:  # upsert
            if not path.is_file():
                return
            # Sprawdź czy faktycznie się zmienił (przeciw redundancji):
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
```

- [ ] **Step 4: Tests pass**

```bash
./.venv/bin/pytest tests/test_scanner.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/scanner.py tests/test_scanner.py
git commit -m "scanner: watchdog observer with 500ms debounce"
```

---

## Task 9: Server — application state + libraries endpoints

**Files:**
- Modify: `backend/server.py`
- Create: `tests/test_server.py`

- [ ] **Step 1: Failing test**

`tests/test_server.py`:

```python
"""E2E smoke test serwera."""
from __future__ import annotations
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo


def _make_comfy_png(path: Path) -> None:
    info = PngInfo()
    info.add_text("prompt", json.dumps({
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello cat"}},
    }))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "white").save(path, "PNG", pnginfo=info)


@pytest.fixture
def app_with_tmpdb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Świeży serwer z DB w tmp."""
    monkeypatch.setenv("AI_GALLERY_WORK_DIR", str(tmp_path / "work"))
    # importujemy po ustawieniu env:
    import importlib, backend.server as srv_mod
    importlib.reload(srv_mod)
    yield TestClient(srv_mod.app), tmp_path


def test_health(app_with_tmpdb) -> None:
    client, _ = app_with_tmpdb
    assert client.get("/api/health").json() == {"status": "ok"}


def test_add_library_and_list(app_with_tmpdb) -> None:
    client, tmp_path = app_with_tmpdb
    lib_path = tmp_path / "photos"
    lib_path.mkdir()
    _make_comfy_png(lib_path / "a.png")
    r = client.post("/api/libraries", json={"path": str(lib_path), "name": "photos"})
    assert r.status_code == 200, r.text
    lib_id = r.json()["id"]

    r2 = client.get("/api/libraries")
    libs = r2.json()
    assert any(L["id"] == lib_id and L["name"] == "photos" for L in libs)


def test_add_library_rejects_missing_path(app_with_tmpdb) -> None:
    client, tmp_path = app_with_tmpdb
    r = client.post("/api/libraries", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 400


def test_add_library_rejects_duplicate(app_with_tmpdb) -> None:
    client, tmp_path = app_with_tmpdb
    lib_path = tmp_path / "photos"; lib_path.mkdir()
    client.post("/api/libraries", json={"path": str(lib_path)})
    r = client.post("/api/libraries", json={"path": str(lib_path)})
    assert r.status_code == 409
```

- [ ] **Step 2: Run — fails (endpoints nie istnieją)**

```bash
./.venv/bin/pytest tests/test_server.py -v
```

- [ ] **Step 3: Rozszerz `backend/server.py`**

Zastąp aktualną zawartość:

```python
"""FastAPI backend dla AI Gallery."""
from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

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
    loop = asyncio.get_event_loop_policy().get_event_loop()
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
    # duplikat?
    con = db.readonly(DB_PATH)
    existing = con.execute("SELECT id FROM libraries WHERE path=?", (str(p),)).fetchone()
    con.close()
    if existing:
        raise HTTPException(409, f"Biblioteka już dodana: {p}")
    name = req.name or p.name
    lib_id = db.add_library(state.writer, path=str(p), name=name)
    # Start scan w tle:
    _start_initial_scan(lib_id, p)
    # Start watchdog:
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


# Mount frontend na końcu:
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
```

- [ ] **Step 4: Tests pass**

```bash
./.venv/bin/pytest tests/test_server.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/server.py tests/test_server.py
git commit -m "server: AppState + /api/libraries (add/list) with scan+watchdog"
```

---

## Task 10: Server — images list (filters, FTS, cursor pagination)

**Files:**
- Modify: `backend/server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Failing tests**

W `tests/test_server.py` dopisz:

```python
def test_list_images_by_library(app_with_tmpdb) -> None:
    client, tmp_path = app_with_tmpdb
    lib_path = tmp_path / "photos"; lib_path.mkdir()
    _make_comfy_png(lib_path / "a.png")
    _make_comfy_png(lib_path / "b.png")
    r = client.post("/api/libraries", json={"path": str(lib_path)})
    lib_id = r.json()["id"]
    # poczekaj na scan
    import time
    deadline = time.time() + 5
    while time.time() < deadline:
        imgs = client.get(f"/api/images?library_id={lib_id}").json()
        if len(imgs["items"]) == 2:
            break
        time.sleep(0.2)
    assert len(imgs["items"]) == 2


def test_list_images_with_fts(app_with_tmpdb) -> None:
    client, tmp_path = app_with_tmpdb
    lib_path = tmp_path / "photos"; lib_path.mkdir()
    _make_comfy_png(lib_path / "a.png")  # prompt: "hello cat"
    client.post("/api/libraries", json={"path": str(lib_path)})
    import time
    deadline = time.time() + 5
    while time.time() < deadline:
        r = client.get("/api/images?q=cat").json()
        if r["items"]:
            break
        time.sleep(0.2)
    assert r["items"], "FTS nie znalazł 'cat'"
    r2 = client.get("/api/images?q=xyzunknown").json()
    assert r2["items"] == []
```

- [ ] **Step 2: Run — fails**

```bash
./.venv/bin/pytest tests/test_server.py::test_list_images_by_library -v
```

- [ ] **Step 3: Dodaj `/api/images` do `backend/server.py`**

Pod libraries endpoint dopisz:

```python
import base64
from typing import Literal


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
    q: str | None = None,                       # FTS query
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
```

- [ ] **Step 4: Tests pass**

```bash
./.venv/bin/pytest tests/test_server.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/server.py tests/test_server.py
git commit -m "server: GET /api/images with library/model/lora/FTS + cursor pagination"
```

---

## Task 11: Server — thumbs (z sweep na starcie), file serving, facets, rescan

**Files:**
- Modify: `backend/server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Failing tests**

W `tests/test_server.py` dopisz:

```python
def test_thumbnail_served(app_with_tmpdb) -> None:
    client, tmp_path = app_with_tmpdb
    lib_path = tmp_path / "photos"; lib_path.mkdir()
    _make_comfy_png(lib_path / "a.png")
    client.post("/api/libraries", json={"path": str(lib_path)})
    import time
    deadline = time.time() + 5
    img_id = None
    while time.time() < deadline:
        items = client.get("/api/images").json()["items"]
        if items:
            img_id = items[0]["id"]; break
        time.sleep(0.2)
    assert img_id is not None
    r = client.get(f"/api/images/{img_id}/thumb")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert len(r.content) > 0


def test_facets(app_with_tmpdb) -> None:
    client, tmp_path = app_with_tmpdb
    lib_path = tmp_path / "photos"; lib_path.mkdir()
    _make_comfy_png(lib_path / "a.png")
    client.post("/api/libraries", json={"path": str(lib_path)})
    import time
    time.sleep(1.5)  # let scan complete
    r = client.get("/api/facets").json()
    assert "models" in r and "loras" in r
```

- [ ] **Step 2: Run — fails (404)**

```bash
./.venv/bin/pytest tests/test_server.py::test_thumbnail_served -v
```

- [ ] **Step 3: Dodaj endpoints do `backend/server.py`**

```python
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


def _sweep_thumbs() -> int:
    """Usuń sieroty z cache miniatur (sha1 nieznane w images)."""
    con = db.readonly(DB_PATH)
    known = {r["sha1"] for r in con.execute(
        "SELECT DISTINCT sha1 FROM images WHERE sha1 IS NOT NULL"
    )}
    con.close()
    return thumbs.sweep(THUMBS_DIR, known_sha1s=known)


@app.on_event("startup")
def _on_startup() -> None:
    """Po wystartowaniu serwera: re-attach observery dla istniejących bibliotek + sweep."""
    con = db.readonly(DB_PATH)
    libs = [dict(r) for r in con.execute("SELECT id, path FROM libraries")]
    con.close()
    for lib in libs:
        p = Path(lib["path"])
        if p.exists() and p.is_dir():
            _start_observer(lib["id"], p)
    _sweep_thumbs()


@app.delete("/api/libraries/{library_id}")
def delete_library(library_id: int) -> dict:
    con = db.readonly(DB_PATH)
    exists = con.execute("SELECT 1 FROM libraries WHERE id=?", (library_id,)).fetchone()
    con.close()
    if not exists:
        raise HTTPException(404)
    # zatrzymaj observer
    h = state.observers.pop(library_id, None)
    if h:
        scanner.stop_watchdog(h)
    # delete cascades do images i image_loras
    def _del(con):
        con.execute("DELETE FROM libraries WHERE id=?", (library_id,))
    db._submit(state.writer, _del)  # type: ignore[arg-type]
    return {"status": "deleted"}
```

- [ ] **Step 4: Tests pass**

```bash
./.venv/bin/pytest tests/test_server.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/server.py tests/test_server.py
git commit -m "server: thumb/file/facets/rescan/delete-library endpoints"
```

---

## Task 12: Server — file operations + audit + WebSocket

**Files:**
- Modify: `backend/server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Failing tests**

W `tests/test_server.py` dopisz:

```python
def test_delete_image_moves_to_trash(app_with_tmpdb, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    client, _ = app_with_tmpdb
    lib_path = tmp_path / "photos"; lib_path.mkdir()
    _make_comfy_png(lib_path / "a.png")
    client.post("/api/libraries", json={"path": str(lib_path)})
    import time
    deadline = time.time() + 5
    img_id = None
    while time.time() < deadline:
        items = client.get("/api/images").json()["items"]
        if items:
            img_id = items[0]["id"]; break
        time.sleep(0.2)
    assert img_id
    r = client.delete(f"/api/images/{img_id}")
    assert r.status_code == 200
    assert not (lib_path / "a.png").exists()
    # poczekaj aż watchdog/operacja zniknie z DB:
    deadline = time.time() + 3
    while time.time() < deadline:
        if not client.get(f"/api/images/{img_id}").status_code == 200:
            break
        time.sleep(0.2)


def test_rename_image(app_with_tmpdb) -> None:
    client, tmp_path = app_with_tmpdb
    lib_path = tmp_path / "photos"; lib_path.mkdir()
    _make_comfy_png(lib_path / "old.png")
    client.post("/api/libraries", json={"path": str(lib_path)})
    import time
    deadline = time.time() + 5
    img_id = None
    while time.time() < deadline:
        items = client.get("/api/images").json()["items"]
        if items:
            img_id = items[0]["id"]; break
        time.sleep(0.2)
    r = client.post(f"/api/images/{img_id}/rename", json={"new_name": "new.png"})
    assert r.status_code == 200, r.text
    assert (lib_path / "new.png").exists()
    assert not (lib_path / "old.png").exists()


def test_websocket_receives_scan_done(app_with_tmpdb) -> None:
    client, tmp_path = app_with_tmpdb
    lib_path = tmp_path / "photos"; lib_path.mkdir()
    _make_comfy_png(lib_path / "a.png")
    with client.websocket_connect("/ws") as ws:
        client.post("/api/libraries", json={"path": str(lib_path)})
        # zbierz parę eventów, szukaj scan_done:
        import time
        deadline = time.time() + 5
        seen_done = False
        while time.time() < deadline and not seen_done:
            try:
                msg = ws.receive_json(timeout=2)
            except Exception:
                continue
            if msg.get("type") == "scan_done":
                seen_done = True
        assert seen_done
```

- [ ] **Step 2: Run — fail**

```bash
./.venv/bin/pytest tests/test_server.py::test_delete_image_moves_to_trash -v
```

- [ ] **Step 3: Dodaj fileops endpoints + WebSocket do `backend/server.py`**

```python
from fastapi import WebSocket, WebSocketDisconnect
from . import fileops


class RenameRequest(BaseModel):
    new_name: str


class MoveRequest(BaseModel):
    to_library_id: int
    to_rel_path: str


def _image_source_path(image_id: int) -> tuple[Path, int, str]:
    """Zwróć (absolute path, library_id, rel_path) albo rzuć 404."""
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
    error: str | None = None; success = False; dst: Path | None = None
    try:
        dst = fileops.rename(src, new_name=req.new_name)
        # Update rel_path w DB:
        new_rel = dst.relative_to(src.parent.parent if "/" in rel else src.parent).as_posix() \
                  if "/" in rel else dst.name
        # Bezpieczniej: liczyć względem library root:
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
    state.ws_clients.add(ws)
    try:
        while True:
            # czytamy ping/keepalive od klienta (front sam wysyła co 30s):
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.ws_clients.discard(ws)
```

- [ ] **Step 4: Tests pass**

```bash
./.venv/bin/pytest tests/test_server.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/server.py tests/test_server.py
git commit -m "server: delete/rename/move + audit + WebSocket broadcast"
```

---

## Task 13: Frontend — HTML scaffold + CSS layout (3-kolumnowy)

**Files:**
- Modify: `frontend/index.html`
- Create: `frontend/style.css`
- Create: `frontend/app.js`

- [ ] **Step 1: Napisz `frontend/index.html`**

```html
<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <title>AI Gallery</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <header class="topbar">
    <div class="brand">🖼️ AI Gallery</div>
    <div class="topbar-actions">
      <button id="btn-add-library">+ Dodaj folder</button>
      <button id="btn-rescan" title="Skanuj ponownie aktywną bibliotekę">⟳</button>
    </div>
    <input id="search" type="search" placeholder="🔍 szukaj w promptach (Enter)..." />
  </header>

  <main class="layout">
    <aside id="sidebar" class="sidebar">
      <section>
        <h3>Biblioteki</h3>
        <ul id="libraries"></ul>
      </section>
      <section>
        <h3>Model</h3>
        <ul id="filter-models" class="facets"></ul>
      </section>
      <section>
        <h3>LoRA</h3>
        <ul id="filter-loras" class="facets"></ul>
      </section>
    </aside>

    <section id="gallery" class="gallery"></section>

    <aside id="detail" class="detail hidden">
      <button id="detail-close" class="close-btn" title="Esc">✕</button>
      <img id="detail-img" alt="">
      <div id="detail-meta"></div>
      <div class="detail-actions">
        <button id="btn-copy-prompt">📋 Kopiuj prompt</button>
        <button id="btn-delete" class="danger">🗑 Do kosza</button>
        <button id="btn-rename">✎ Zmień nazwę</button>
      </div>
    </aside>
  </main>

  <div id="lightbox" class="lightbox hidden">
    <img id="lightbox-img" alt="">
  </div>

  <div id="toast"></div>

  <script type="module" src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Napisz `frontend/style.css`**

```css
:root {
  --bg: #1a1a1a;
  --bg-panel: #232323;
  --bg-elev: #2c2c2c;
  --fg: #e5e5e5;
  --fg-dim: #999;
  --accent: #4a9a4a;
  --danger: #c14545;
  --border: #333;
  --tile: 180px;
}

* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 14px/1.4 system-ui, sans-serif;
  height: 100vh; display: flex; flex-direction: column;
}

.topbar {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 12px; background: var(--bg-panel);
  border-bottom: 1px solid var(--border);
}
.brand { font-weight: 600; }
.topbar-actions { display: flex; gap: 4px; }
#search { flex: 1; padding: 6px 10px; background: var(--bg-elev);
  border: 1px solid var(--border); color: var(--fg); border-radius: 4px; }

button {
  background: var(--bg-elev); border: 1px solid var(--border);
  color: var(--fg); padding: 6px 10px; border-radius: 4px; cursor: pointer;
}
button:hover { background: #3a3a3a; }
button.danger { color: #ff8a8a; }

.layout {
  display: grid; grid-template-columns: 240px 1fr 320px;
  flex: 1; overflow: hidden;
}
@media (max-width: 900px) { .layout { grid-template-columns: 0 1fr 0; } }

.sidebar {
  background: var(--bg-panel); border-right: 1px solid var(--border);
  overflow-y: auto; padding: 12px;
}
.sidebar h3 { margin: 12px 0 6px; font-size: 12px; color: var(--fg-dim);
  text-transform: uppercase; letter-spacing: 0.5px; }
.sidebar ul { list-style: none; padding: 0; margin: 0; }
.sidebar li {
  padding: 4px 6px; cursor: pointer; border-radius: 3px;
  display: flex; justify-content: space-between; align-items: center;
}
.sidebar li:hover { background: var(--bg-elev); }
.sidebar li.active { background: var(--accent); color: white; }
.sidebar .count { color: var(--fg-dim); font-size: 11px; }
.facets li { font-size: 13px; }

.gallery {
  overflow-y: auto; padding: 8px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--tile), 1fr));
  gap: 6px; align-content: start;
}
.tile {
  aspect-ratio: 1; background: var(--bg-elev);
  border: 1px solid var(--border); border-radius: 4px;
  overflow: hidden; cursor: pointer; position: relative;
}
.tile.selected { outline: 2px solid var(--accent); }
.tile img { width: 100%; height: 100%; object-fit: cover;
  opacity: 0; transition: opacity 0.2s; }
.tile img.loaded { opacity: 1; }

.detail {
  background: var(--bg-panel); border-left: 1px solid var(--border);
  overflow-y: auto; padding: 12px; position: relative;
}
.detail.hidden { display: none; }
.close-btn { position: absolute; top: 8px; right: 8px; }
#detail-img { width: 100%; border-radius: 4px; cursor: zoom-in; }
#detail-meta { margin-top: 10px; font-size: 13px; }
#detail-meta dt { color: var(--fg-dim); margin-top: 6px; }
#detail-meta dd { margin: 2px 0 0; word-break: break-word; }
.detail-actions { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; }

.lightbox {
  position: fixed; inset: 0; background: rgba(0,0,0,0.92);
  display: flex; justify-content: center; align-items: center;
  z-index: 100; cursor: zoom-out;
}
.lightbox.hidden { display: none; }
#lightbox-img { max-width: 95vw; max-height: 95vh; }

#toast {
  position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
  background: var(--bg-elev); padding: 8px 14px; border-radius: 4px;
  border: 1px solid var(--border); opacity: 0;
  transition: opacity 0.3s; pointer-events: none; z-index: 200;
}
#toast.show { opacity: 1; }

.hidden { display: none !important; }
```

- [ ] **Step 3: Pusty `frontend/app.js` (placeholder + verify load)**

```javascript
// AI Gallery — frontend entrypoint. Kolejne taski uzupełnią funkcjonalność.
console.log("AI Gallery frontend loaded");
```

- [ ] **Step 4: Manualny smoke**

```bash
./run.sh &
sleep 3
xdg-open http://127.0.0.1:8000 2>/dev/null || echo "Open http://127.0.0.1:8000"
sleep 5
pkill -f "uvicorn backend.server"
```

Sprawdź wizualnie: trzy kolumny, ciemny motyw, topbar widoczny.

- [ ] **Step 5: Commit**

```bash
git add frontend/
git commit -m "frontend: HTML scaffold + 3-column dark layout"
```

---

## Task 14: Frontend — libraries panel (list + add dialog)

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Implementacja**

`frontend/app.js`:

```javascript
// AI Gallery — frontend. Vanilla JS.

const state = {
  libraries: [],
  activeLibraryId: null,
  filters: { model: null, lora: null, q: "" },
  images: [],
  nextCursor: null,
  selectedId: null,
};

// ---------- HTTP helpers ----------
async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${r.status}: ${text}`);
  }
  return r.json();
}

// ---------- toast ----------
const toastEl = document.getElementById("toast");
let toastTimer = null;
function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2500);
}

// ---------- libraries ----------
async function refreshLibraries() {
  state.libraries = await api("/api/libraries");
  renderLibraries();
}

function renderLibraries() {
  const ul = document.getElementById("libraries");
  ul.innerHTML = "";
  const allLi = document.createElement("li");
  allLi.textContent = "Wszystkie";
  allLi.classList.toggle("active", state.activeLibraryId === null);
  allLi.onclick = () => selectLibrary(null);
  ul.appendChild(allLi);
  for (const L of state.libraries) {
    const li = document.createElement("li");
    li.classList.toggle("active", L.id === state.activeLibraryId);
    li.onclick = () => selectLibrary(L.id);
    const name = document.createElement("span");
    name.textContent = L.name;
    name.title = L.path;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = L.image_count;
    li.append(name, count);
    ul.appendChild(li);
  }
}

function selectLibrary(id) {
  state.activeLibraryId = id;
  localStorage.setItem("activeLibraryId", id ?? "");
  renderLibraries();
  loadImages();
}

document.getElementById("btn-add-library").onclick = async () => {
  const path = prompt("Ścieżka do folderu:");
  if (!path) return;
  const name = prompt("Nazwa biblioteki (puste = nazwa folderu):") || null;
  try {
    await api("/api/libraries", {
      method: "POST",
      body: JSON.stringify({ path, name }),
    });
    toast("Biblioteka dodana — skanowanie w toku…");
    await refreshLibraries();
  } catch (err) {
    toast("Błąd: " + err.message);
  }
};

document.getElementById("btn-rescan").onclick = async () => {
  if (state.activeLibraryId == null) {
    toast("Wybierz bibliotekę");
    return;
  }
  await api(`/api/libraries/${state.activeLibraryId}/rescan`, { method: "POST" });
  toast("Rescan rozpoczęty");
};

// ---------- gallery (placeholder w Task 15) ----------
async function loadImages() {
  // wypełni Task 15
}

// ---------- init ----------
(async function init() {
  const saved = localStorage.getItem("activeLibraryId");
  state.activeLibraryId = saved ? Number(saved) || null : null;
  await refreshLibraries();
  await loadImages();
})();
```

- [ ] **Step 2: Manualny smoke**

```bash
./run.sh &
sleep 3
```

Otwórz w przeglądarce, kliknij **+ Dodaj folder**, wpisz np. `/home/bart/comfyX/ComfyUI/output` (jeśli istnieje) albo dowolny inny folder. Sprawdź że pojawia się w panelu.

```bash
pkill -f "uvicorn backend.server"
```

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "frontend: libraries panel + add/rescan actions"
```

---

## Task 15: Frontend — gallery grid z lazy thumbs + scroll-load

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Zastąp funkcję `loadImages` + dopisz lazy renderer**

W `frontend/app.js` zastąp pusty `loadImages` i dodaj funkcje pomocnicze:

```javascript
const galleryEl = document.getElementById("gallery");

let thumbObserver = null;
function ensureThumbObserver() {
  if (thumbObserver) return thumbObserver;
  thumbObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const img = e.target;
      const id = img.dataset.id;
      img.src = `/api/images/${id}/thumb`;
      img.onload = () => img.classList.add("loaded");
      thumbObserver.unobserve(img);
    }
  }, { rootMargin: "200px" });
  return thumbObserver;
}

let scrollObserver = null;
function ensureScrollObserver(sentinel) {
  if (scrollObserver) scrollObserver.disconnect();
  scrollObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting && state.nextCursor) {
        loadMore();
      }
    }
  }, { root: galleryEl, rootMargin: "400px" });
  scrollObserver.observe(sentinel);
}

function buildImagesQuery() {
  const params = new URLSearchParams();
  if (state.activeLibraryId != null) params.set("library_id", state.activeLibraryId);
  if (state.filters.model) params.set("model", state.filters.model);
  if (state.filters.lora) params.set("lora", state.filters.lora);
  if (state.filters.q) params.set("q", state.filters.q);
  params.set("limit", "200");
  if (state.nextCursor) params.set("cursor", state.nextCursor);
  return params.toString();
}

async function loadImages() {
  state.images = [];
  state.nextCursor = null;
  galleryEl.innerHTML = "";
  await loadMore();
}

async function loadMore() {
  const r = await api("/api/images?" + buildImagesQuery());
  for (const item of r.items) {
    state.images.push(item);
    galleryEl.appendChild(renderTile(item));
  }
  state.nextCursor = r.next_cursor;
  // sentinel:
  const old = galleryEl.querySelector(".sentinel");
  if (old) old.remove();
  if (state.nextCursor) {
    const s = document.createElement("div");
    s.className = "sentinel";
    s.style.gridColumn = "1 / -1";
    s.style.height = "1px";
    galleryEl.appendChild(s);
    ensureScrollObserver(s);
  }
}

function renderTile(img) {
  const div = document.createElement("div");
  div.className = "tile";
  div.dataset.id = img.id;
  if (img.id === state.selectedId) div.classList.add("selected");
  const i = document.createElement("img");
  i.dataset.id = img.id;
  i.alt = img.rel_path;
  div.appendChild(i);
  div.onclick = () => selectImage(img.id);
  ensureThumbObserver().observe(i);
  return div;
}

function selectImage(id) {
  state.selectedId = id;
  for (const t of galleryEl.querySelectorAll(".tile")) {
    t.classList.toggle("selected", Number(t.dataset.id) === id);
  }
  openDetail(id);  // implementowane w Task 16
}

function openDetail(_id) { /* Task 16 */ }
```

- [ ] **Step 2: Manualny smoke**

```bash
./run.sh &
sleep 3
```

Otwórz UI, dodaj folder ze zdjęciami (jeśli nie dodałeś). Sprawdź:
- Miniaturki ładują się w siatce (lazy)
- Scroll na dół ładuje kolejną stronę

```bash
pkill -f "uvicorn backend.server"
```

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "frontend: gallery grid with lazy thumbs + cursor scroll-load"
```

---

## Task 16: Frontend — detail panel + lightbox

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Zastąp `openDetail` + dodaj lightbox/close handlery**

```javascript
const detailEl = document.getElementById("detail");
const detailImg = document.getElementById("detail-img");
const detailMeta = document.getElementById("detail-meta");
const lightboxEl = document.getElementById("lightbox");
const lightboxImg = document.getElementById("lightbox-img");

async function openDetail(id) {
  const d = await api(`/api/images/${id}`);
  detailImg.src = `/api/images/${id}/file`;
  detailMeta.innerHTML = "";
  const dl = document.createElement("dl");
  function add(k, v) {
    if (v == null || v === "") return;
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = String(v);
    dl.append(dt, dd);
  }
  add("Plik", d.rel_path);
  add("Wymiary", `${d.width}×${d.height}`);
  add("Źródło", d.source_kind);
  add("Model", d.model_name);
  add("Sampler", d.sampler);
  add("Steps", d.steps);
  add("CFG", d.cfg);
  add("Seed", d.seed);
  if (d.loras && d.loras.length) {
    const dt = document.createElement("dt"); dt.textContent = "LoRA";
    const dd = document.createElement("dd");
    dd.innerHTML = d.loras.map(L => `· ${L.name}${L.strength != null ? ` (${L.strength})` : ""}`).join("<br>");
    dl.append(dt, dd);
  }
  add("Prompt", d.prompt);
  add("Negative", d.negative);
  detailMeta.appendChild(dl);
  detailEl.dataset.imageId = id;
  detailEl.classList.remove("hidden");
}

function closeDetail() {
  detailEl.classList.add("hidden");
  state.selectedId = null;
  for (const t of galleryEl.querySelectorAll(".tile.selected")) {
    t.classList.remove("selected");
  }
}

document.getElementById("detail-close").onclick = closeDetail;

detailImg.onclick = () => {
  lightboxImg.src = detailImg.src;
  lightboxEl.classList.remove("hidden");
};
lightboxEl.onclick = () => lightboxEl.classList.add("hidden");

document.getElementById("btn-copy-prompt").onclick = async () => {
  const id = Number(detailEl.dataset.imageId);
  const d = await api(`/api/images/${id}`);
  await navigator.clipboard.writeText(d.prompt || "");
  toast("Prompt skopiowany");
};
```

- [ ] **Step 2: Manualny smoke**

```bash
./run.sh &
sleep 3
```

Kliknij miniaturę: prawy panel pokazuje metadane + duży obraz. Klik na duży obraz: lightbox. Esc / click poza: zamyka.

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "frontend: detail panel + lightbox + copy prompt"
```

---

## Task 17: Frontend — filtry (model + LoRA) + FTS search

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Dodaj refresh facets + search handler**

```javascript
async function refreshFacets() {
  const f = await api("/api/facets");
  renderFacets("filter-models", f.models, "model");
  renderFacets("filter-loras", f.loras, "lora");
}

function renderFacets(elementId, items, filterKey) {
  const ul = document.getElementById(elementId);
  ul.innerHTML = "";
  const allLi = document.createElement("li");
  allLi.textContent = "— wszystkie —";
  allLi.classList.toggle("active", state.filters[filterKey] == null);
  allLi.onclick = () => { state.filters[filterKey] = null; refreshFacets(); loadImages(); };
  ul.appendChild(allLi);
  for (const it of items) {
    const li = document.createElement("li");
    li.classList.toggle("active", state.filters[filterKey] === it.name);
    const n = document.createElement("span");
    n.textContent = it.name; n.title = it.name;
    const c = document.createElement("span"); c.className = "count"; c.textContent = it.count;
    li.append(n, c);
    li.onclick = () => {
      state.filters[filterKey] = state.filters[filterKey] === it.name ? null : it.name;
      refreshFacets(); loadImages();
    };
    ul.appendChild(li);
  }
}

// Search:
const searchEl = document.getElementById("search");
searchEl.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  state.filters.q = searchEl.value.trim();
  loadImages();
});
```

I w `init()` dorzuć wywołanie:

```javascript
await refreshFacets();
```

- [ ] **Step 2: Manualny smoke**

```bash
./run.sh &
sleep 3
```

Sprawdź że dropdowny modelów i LoRA są wypełnione, kliknięcie filtruje. Wpisz słowo w search bar i Enter — galeria pokazuje tylko pasujące.

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "frontend: facets (model+LoRA) + FTS search bar"
```

---

## Task 18: Frontend — WebSocket live updates

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Dodaj WS client**

```javascript
// ---------- WebSocket ----------
let ws = null;
let wsReconnectTimer = null;

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => {
    clearTimeout(wsReconnectTimer);
    // keepalive co 30s:
    setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send("ping"); }, 30000);
  };
  ws.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    handleWSMessage(msg);
  };
  ws.onclose = () => {
    wsReconnectTimer = setTimeout(connectWS, 2000);
  };
  ws.onerror = () => ws.close();
}

let pendingNewImages = 0;
const liveBadge = document.createElement("div");
liveBadge.style.cssText = "position:absolute;top:8px;left:50%;transform:translateX(-50%);" +
  "background:var(--accent);color:white;padding:6px 12px;border-radius:4px;cursor:pointer;display:none;z-index:50;";
liveBadge.onclick = () => {
  pendingNewImages = 0;
  liveBadge.style.display = "none";
  galleryEl.scrollTo({ top: 0, behavior: "smooth" });
  loadImages();
};
galleryEl.parentElement.style.position = "relative";
galleryEl.parentElement.appendChild(liveBadge);

function handleWSMessage(msg) {
  switch (msg.type) {
    case "scan_progress":
      toast(`Skanuję: ${msg.scanned}/${msg.total}`);
      break;
    case "scan_done":
      toast(`Skan ukończony: +${msg.added} ~${msg.updated} -${msg.removed}`);
      refreshLibraries();
      refreshFacets();
      break;
    case "image_added":
      if (galleryEl.scrollTop < 50) {
        loadImages();
      } else {
        pendingNewImages++;
        liveBadge.textContent = `${pendingNewImages} nowych — kliknij aby pokazać`;
        liveBadge.style.display = "block";
      }
      break;
    case "image_removed":
      const tile = galleryEl.querySelector(`.tile[data-id="${msg.image_id}"]`);
      if (tile) tile.remove();
      if (state.selectedId === msg.image_id) closeDetail();
      break;
    case "image_changed":
      if (state.selectedId === msg.image_id) openDetail(msg.image_id);
      break;
  }
}

// w init() dodaj:
connectWS();
```

- [ ] **Step 2: Manualny smoke**

Test: uruchom serwer, otwórz UI, w terminalu wrzuć nowy PNG do folderu biblioteki:

```bash
./run.sh &
sleep 3
# (w przeglądarce już otwarte; podstaw swój folder biblioteki:)
cp /tmp/some.png /sciezka/do/biblioteki/test_$(date +%s).png
sleep 2
```

W UI: powinien pojawić się kafelek / badge "1 nowych".

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "frontend: WebSocket live updates + new-images badge"
```

---

## Task 19: Frontend — file operations (delete + rename) z confirm

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Podłącz przyciski delete + rename**

```javascript
document.getElementById("btn-delete").onclick = async () => {
  const id = Number(detailEl.dataset.imageId);
  if (!id) return;
  const d = state.images.find(x => x.id === id);
  if (!confirm(`Przenieść do kosza?\n${d?.rel_path || id}`)) return;
  try {
    await api(`/api/images/${id}`, { method: "DELETE" });
    toast("Przeniesione do kosza");
    closeDetail();
  } catch (err) {
    toast("Błąd: " + err.message);
  }
};

document.getElementById("btn-rename").onclick = async () => {
  const id = Number(detailEl.dataset.imageId);
  if (!id) return;
  const d = state.images.find(x => x.id === id);
  const currentName = (d?.rel_path || "").split("/").pop();
  const newName = prompt("Nowa nazwa pliku:", currentName);
  if (!newName || newName === currentName) return;
  try {
    await api(`/api/images/${id}/rename`, {
      method: "POST",
      body: JSON.stringify({ new_name: newName }),
    });
    toast("Zmieniono nazwę");
    await loadImages();
    openDetail(id);
  } catch (err) {
    toast("Błąd: " + err.message);
  }
};
```

- [ ] **Step 2: Manualny smoke**

Test delete: wybierz testowy plik, klik 🗑, confirm. Plik powinien zniknąć z galerii i pojawić się w systemowym koszu Plasma.

Test rename: wybierz plik, klik ✎, wpisz nową nazwę. Plik zmienia nazwę na dysku, galeria refresh.

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "frontend: delete (XDG trash) + rename with confirms"
```

---

## Task 20: Frontend — hotkeys + tile size + UI prefs persistence

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html` (dodaj kontrolki zoom)
- Modify: `frontend/style.css` (kontrolka tile size)

- [ ] **Step 1: Dodaj kontrolki rozmiaru kafelka do `index.html`**

W `<div class="topbar-actions">` dodaj:

```html
<button id="btn-tile-smaller" title="Mniejsze kafelki (Ctrl+-)">−</button>
<button id="btn-tile-bigger" title="Większe kafelki (Ctrl+=)">+</button>
```

- [ ] **Step 2: Hotkeys + prefs w `app.js`**

```javascript
// ---------- prefs ----------
function loadPrefs() {
  const t = Number(localStorage.getItem("tileSize") || "180");
  document.documentElement.style.setProperty("--tile", `${t}px`);
}
function setTileSize(px) {
  const clamped = Math.max(80, Math.min(400, px));
  localStorage.setItem("tileSize", String(clamped));
  document.documentElement.style.setProperty("--tile", `${clamped}px`);
}
document.getElementById("btn-tile-smaller").onclick = () => {
  setTileSize(parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--tile")) - 30);
};
document.getElementById("btn-tile-bigger").onclick = () => {
  setTileSize(parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--tile")) + 30);
};

// ---------- hotkeys ----------
document.addEventListener("keydown", (e) => {
  // ignoruj gdy fokus w polu tekstowym
  if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) {
    if (e.key === "Escape") document.activeElement.blur();
    return;
  }
  if (e.key === "/") { e.preventDefault(); searchEl.focus(); return; }
  if (e.key === "Escape") {
    if (!lightboxEl.classList.contains("hidden")) {
      lightboxEl.classList.add("hidden");
    } else {
      closeDetail();
    }
    return;
  }
  if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    if (state.selectedId == null) return;
    const idx = state.images.findIndex(x => x.id === state.selectedId);
    const nextIdx = e.key === "ArrowLeft" ? idx - 1 : idx + 1;
    const next = state.images[nextIdx];
    if (next) selectImage(next.id);
    return;
  }
  if (e.key === "Delete" && state.selectedId != null) {
    document.getElementById("btn-delete").click();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "c" && state.selectedId != null) {
    document.getElementById("btn-copy-prompt").click();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === "-" || e.key === "=")) {
    e.preventDefault();
    if (e.key === "-") document.getElementById("btn-tile-smaller").click();
    else document.getElementById("btn-tile-bigger").click();
  }
});

// w init() na początku:
loadPrefs();
```

- [ ] **Step 3: Manualny smoke**

Test:
- `/` → focus search
- Esc → zamyka detail/lightbox
- ←/→ → przeskakuje między zdjęciami
- Ctrl+- / Ctrl+= → zmienia rozmiar kafelka (zachowuje się po reload)
- Del → delete (z confirm)
- Ctrl+C → copy prompt (gdy detail otwarty)

- [ ] **Step 4: Commit**

```bash
git add frontend/
git commit -m "frontend: hotkeys + tile size controls + localStorage prefs"
```

---

## Task 21: Final smoke + README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Uzupełnij `README.md`**

```markdown
# AI Gallery

Lokalna apka do przeglądania bibliotek AI-generated images
(ComfyUI / A1111 / SD). Skanuje rekurencyjnie wskazane foldery,
wyciąga prompty i parametry generacji, trzyma indeks w SQLite
z FTS5. Reaguje na zmiany w plikach na żywo.

## Uruchomienie

```bash
./run.sh
```

Otwórz http://127.0.0.1:8000.

## Wymagania

- Linux z Pythonem 3.12 dostępnym przez `uv`
- [`uv`](https://docs.astral.sh/uv/)

## Użytkowanie

1. Klik **+ Dodaj folder** → wpisz absolutną ścieżkę.
2. Aplikacja skanuje rekurencyjnie i parsuje metadane (PNG tEXt
   `prompt`/`workflow` dla ComfyUI, `parameters` dla A1111, EXIF
   UserComment dla A1111 JPG).
3. Klik miniatury → prawy panel z promptami + akcje
   (copy / delete / rename).
4. Search bar to FTS5 — wpisz słowo, Enter.
5. Filtry w lewym panelu: model i LoRA.

## Skróty klawiszowe

| Klawisz | Akcja |
|---|---|
| `/` | Focus search |
| `Esc` | Zamknij detail/lightbox |
| `← →` | Prev/next image |
| `Del` | Usuń (do kosza) |
| `Ctrl+C` | Kopiuj prompt |
| `Ctrl+-` / `Ctrl+=` | Mniejsze/większe kafelki |

## Operacje plikowe

- **Delete** przenosi do XDG Trash (`~/.local/share/Trash/`).
  Restore przez systemowy menedżer kosza (Plasma, Files itp.).
- **Rename** zmienia nazwę w obrębie tego samego folderu.
- Audit log w SQLite (`file_ops` table), endpoint `GET /api/audit`.

## Stack

- FastAPI + uvicorn[standard]
- Pillow, watchdog
- SQLite z FTS5 (stdlib)
- Frontend: vanilla JS, brak build step

## Dokumentacja

- Spec: `docs/superpowers/specs/2026-05-30-ai-gallery-design.md`
- Plan implementacji: `docs/superpowers/plans/2026-05-30-ai-gallery.md`
```

- [ ] **Step 2: Final test run**

```bash
./.venv/bin/pytest -v
```

Expected: all tests pass.

- [ ] **Step 3: Manualny e2e**

```bash
./run.sh &
sleep 3
```

Manualnie zweryfikuj:
1. ✅ Dodaj folder z prawdziwymi ComfyUI outputami (`/home/bart/comfyX/ComfyUI/output` jeśli masz)
2. ✅ Miniatury się ładują w siatce
3. ✅ Klik miniatury otwiera detail panel z promptem
4. ✅ Search działa (wpisz słowo z prompta)
5. ✅ Filtr po modelu/LoRA działa
6. ✅ Delete przenosi do kosza
7. ✅ Rename zmienia nazwę
8. ✅ Dodanie nowego pliku do folderu (np. `cp`) pojawia się w UI w ciągu sekundy

```bash
pkill -f "uvicorn backend.server"
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: full README with usage, hotkeys, operations"
```

---

## Self-review notes

**Spec coverage:**

- ✅ Architecture (single uvicorn + per-library watchdog + writer thread) — Task 1+9
- ✅ Tabele `libraries`, `images`, `loras`, `image_loras`, `file_ops` + FTS5 + triggery — Task 2
- ✅ ComfyUI metadata extraction — Task 3
- ✅ A1111 PNG `parameters` + EXIF UserComment — Task 4
- ✅ LoRA extraction (z workflow + z A1111 prompt) — Task 3+4
- ✅ WebP thumbnails + sweep — Task 5
- ✅ XDG Trash + move + rename + walidacja — Task 6
- ✅ Initial scan z skip/add/update/remove — Task 7
- ✅ Watchdog observer + 500ms debounce — Task 8
- ✅ REST: libraries, images (filters, FTS, cursor) — Tasks 9, 10
- ✅ Thumb, file, facets, rescan, delete-library — Task 11
- ✅ File ops endpoints + audit + WebSocket — Task 12
- ✅ 3-column UI layout — Task 13
- ✅ Libraries panel + add dialog — Task 14
- ✅ Gallery virtual grid + lazy thumbs + cursor scroll-load — Task 15
- ✅ Detail panel + lightbox + copy prompt — Task 16
- ✅ Facets + FTS search — Task 17
- ✅ WebSocket live updates + new-images badge — Task 18
- ✅ Delete/rename z confirm — Task 19
- ✅ Hotkeys + tile size + localStorage prefs — Task 20
- ✅ README — Task 21

**Świadomie pominięte (zgodnie ze spec YAGNI):**
- Bulk delete UI — spec wspomina ale nie wybraliśmy multi-select; do dodania w osobnej iteracji jeśli będzie potrzeba
- Move UI — endpoint istnieje, ale nie ma frontend UI dla move (rename + delete pokrywają 90% potrzeb); do dodania na żądanie
- Audit viewer UI — endpoint `/api/audit` istnieje, brak osobnego ekranu (na razie dostępny przez curl)
- Sortowanie po wymiarach / źródle / dacie poza domyślnym mtime DESC

Każde z powyższych to mała iteracja po MVP.

**Type consistency check:** ✅ wszystkie nazwy funkcji w testach pasują do implementacji.

**Placeholder scan:** ✅ brak TBD/TODO/„implement later" w żadnym kroku.
