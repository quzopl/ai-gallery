# AI Gallery — design

**Data:** 2026-05-30
**Lokalizacja projektu:** `/home/bart/wsl/ai-gallery`
**Bazowy wzorzec:** `/home/bart/wsl/flux-lora-prep` (FastAPI + vanilla JS + uv)

## Cel

Lokalna aplikacja webowa do przeglądania bibliotek AI-generated images
(ComfyUI / A1111 / SD). Skanuje rekurencyjnie wskazane foldery, wyciąga
metadane (prompty, model, sampler, seed, LoRA), trzyma je w SQLite z
indeksami i FTS5 do szybkich zapytań. Reaguje na zmiany w plikach na
żywo (watchdog) i pcha update do UI przez WebSocket.

## Zakres (z brainstormingu)

**W zakresie:**
- Wiele śledzonych bibliotek (folderów źródłowych), każda skanowana rekurencyjnie
- Ekstrakcja AI metadata z PNG tEXt chunks (ComfyUI workflow + prompt; A1111 parameters)
  oraz EXIF UserComment (A1111 JPG)
- Galeria z wirtualną siatką, lazy thumbs (cache na dysk WebP)
- Detail panel po prawej z promptami i metadanymi
- Filtry: model checkpoint, LoRA
- Full-text search po promptach (SQLite FTS5)
- Operacje plikowe: delete (do XDG Trash), move, rename — z confirm dialog
- Watchdog observer per biblioteka, debouncing 500 ms
- WebSocket broadcast: `scan_progress`, `scan_done`, `image_added/removed/changed`

**Świadomie poza zakresem (YAGNI):**
- Sortowanie po dacie/wymiarach/folderze jako pierwszorzędna funkcja UI
  (sortowanie po mtime DESC jest tylko jako default; nie buduję
  rozbudowanego sort UI bo użytkownik tego nie wybrał jako priorytetu)
- Tagowanie / kolekcje / favorites
- Detekcja duplikatów
- Wykrywanie EXIF z prawdziwych aparatów (tylko AI metadata)
- Migracje schematu (alembic) — drop+rescan tani
- Własny UI restore z kosza (XDG Trash + systemowy menedżer)
- Snapshoty SQLite (DB odbudowywalna z dysku)
- Autoryzacja / CORS / rate limiting (localhost, single user)

## Architektura

```
/home/bart/wsl/ai-gallery/
├── backend/
│   ├── __init__.py
│   ├── server.py       # FastAPI app, routes, WebSocket
│   ├── db.py           # SQLite connection + schema init
│   ├── scanner.py      # initial scan + watchdog observer
│   ├── metadata.py     # PNG tEXt / EXIF UserComment / ComfyUI workflow parsing
│   ├── thumbs.py       # thumbnail generation + cache
│   └── fileops.py      # safe delete (XDG Trash) / move / rename + DB sync
├── frontend/
│   ├── index.html
│   ├── app.js          # virtual grid, WebSocket client, hotkeys
│   └── style.css
├── .work/              # runtime data (gitignored)
│   ├── gallery.db
│   └── thumbs/<sha1>.webp
├── docs/superpowers/specs/...
├── requirements.txt    # fastapi, uvicorn, pillow, watchdog
├── run.sh              # uv venv (Python 3.12) + uvicorn
└── README.md
```

**Procesy wewnątrz jednego uvicorn:**
- Główny event loop FastAPI (HTTP + WebSocket)
- Per-library `watchdog.Observer` w daemon thread
- ThreadPoolExecutor (4–8 workerów) dla parsowania plików
- Pojedynczy writer thread do SQLite (queue.Queue), żeby uniknąć write-lock
  contention między observerami a HTTP

## Model danych (SQLite)

```sql
CREATE TABLE libraries (
    id           INTEGER PRIMARY KEY,
    path         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    added_at     INTEGER NOT NULL,
    last_scan_at INTEGER
);

CREATE TABLE images (
    id            INTEGER PRIMARY KEY,
    library_id    INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    rel_path      TEXT NOT NULL,
    sha1          TEXT,
    mtime         INTEGER NOT NULL,
    size_bytes    INTEGER NOT NULL,
    width         INTEGER,
    height        INTEGER,
    source_kind   TEXT,         -- 'comfyui' | 'a1111' | 'unknown' | NULL
    prompt        TEXT,
    negative      TEXT,
    model_name    TEXT,
    sampler       TEXT,
    steps         INTEGER,
    cfg           REAL,
    seed          INTEGER,
    raw_metadata  TEXT,         -- pełny JSON workflow / parameters
    indexed_at    INTEGER NOT NULL,
    UNIQUE(library_id, rel_path)
);
CREATE INDEX idx_images_library ON images(library_id);
CREATE INDEX idx_images_mtime   ON images(mtime DESC);
CREATE INDEX idx_images_model   ON images(model_name);

CREATE TABLE loras (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);
CREATE TABLE image_loras (
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    lora_id  INTEGER NOT NULL REFERENCES loras(id),
    strength REAL,
    PRIMARY KEY (image_id, lora_id)
);
CREATE INDEX idx_image_loras_lora ON image_loras(lora_id);

CREATE VIRTUAL TABLE images_fts USING fts5(
    prompt, negative, model_name,
    content='images', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
-- triggery (insert/delete/update) utrzymują images_fts w sync, patrz schema init w db.py

CREATE TABLE file_ops (
    id         INTEGER PRIMARY KEY,
    ts         INTEGER NOT NULL,
    op         TEXT NOT NULL,    -- 'delete' | 'move' | 'rename'
    library_id INTEGER,
    from_path  TEXT,
    to_path    TEXT,
    success    INTEGER NOT NULL, -- 0/1
    error      TEXT
);
```

Retention `file_ops`: auto-trim do 10 000 najnowszych wpisów (raz na N
operacji albo nightly).

## API

REST (JSON):

| Method | Path | Opis |
|---|---|---|
| `GET`    | `/api/libraries` | Lista (id, name, path, image_count, last_scan_at) |
| `POST`   | `/api/libraries` | `{path, name?}` → startuje initial scan + observer. **400** gdy path nie istnieje / nie jest katalogiem / brak read permission. **409** gdy ścieżka już dodana. |
| `DELETE` | `/api/libraries/{id}` | Usuń bibliotekę z DB (plików NIE rusza) |
| `POST`   | `/api/libraries/{id}/rescan` | Force full rescan |
| `POST`   | `/api/libraries/{id}/cancel-scan` | Ustaw flagę cancel |
| `GET`    | `/api/images` | Filtry: `library_id`, `model`, `lora`, `q` (FTS), `sort`, `cursor`, `limit` |
| `GET`    | `/api/images/{id}` | Pełne dane + LoRA |
| `GET`    | `/api/images/{id}/thumb` | WebP miniatura (lazy-gen, immutable cache) |
| `GET`    | `/api/images/{id}/file` | Oryginał |
| `DELETE` | `/api/images/{id}` | Do XDG Trash |
| `POST`   | `/api/images/{id}/move` | `{to_library_id, to_rel_path}` |
| `POST`   | `/api/images/{id}/rename` | `{new_name}` |
| `GET`    | `/api/facets` | `{models: [{name, count}], loras: [...]}` |
| `GET`    | `/api/audit?limit=` | Ostatnie operacje plikowe |

**Paginacja:** kursorowa, cursor = base64(`f"{sort_value}|{id}"`). Default
limit 200, max 500.

**WebSocket `/ws`:** broadcast do wszystkich klientów:
```json
{"type": "scan_progress", "library_id": 1, "scanned": 1234, "total": 5000}
{"type": "scan_done",     "library_id": 1, "added": 412, "updated": 13, "removed": 5}
{"type": "image_added",   "image": {...summary...}}
{"type": "image_removed", "image_id": 42}
{"type": "image_changed", "image": {...summary...}}
```

## Pipeline skanowania

**Initial scan / rescan:**
1. INSERT/UPDATE library row.
2. Walk subfoldery (`os.scandir`, rekurencyjnie).
3. Dla każdego `.png/.jpg/.jpeg/.webp`:
   - `stat()` → mtime/size
   - Jeśli rel_path znany i mtime/size niezmienione → SKIP
   - W innym wypadku → FULL_PARSE w threadpoolu
4. FULL_PARSE: Pillow open → wym, sha1 streamingowo, metadata extract,
   INSERT/UPDATE row, **DELETE FROM image_loras WHERE image_id = ? potem
   INSERT** nowy zestaw junction rows (rebuild atomicznie), WS broadcast.
5. Cleanup: rows nieistniejące już na FS → DELETE + WS broadcast.
6. WS `scan_done`.

Progress event co 50 plików. Cancel flag sprawdzana co iterację.

**Watchdog (per library):**
- `recursive=True`
- Eventy zbierane do bucketów per ścieżka, flush co 500 ms (debounce).
- Handlery: `created/modified` → FULL_PARSE; `moved` → UPDATE rel_path lub
  cross-library move; `deleted` → DELETE row.

**Persistencja przy restarcie:**
- Przy starcie serwera: dla każdej library z DB → uruchamiamy observer.
- Jeśli `last_scan_at` > 24 h temu → szybki diff scan (tylko mtime check).

## UI

Trzy kolumny:
1. **Lewy panel** (~18%, collapsible): lista bibliotek + facetowe filtry
   (model, LoRA) z licznikami z `/api/facets`. Checkboxy: AND między
   typami, OR wewnątrz typu.
2. **Środek**: wirtualna siatka (renderuje tylko widoczne kafelki),
   IntersectionObserver dla lazy thumbs. Rozmiar kafelka konfigurowalny.
   Default sort: `mtime DESC`.
3. **Prawy panel** (~30%, ukryty gdy nic nie wybrane): pełne metadane
   wybranego zdjęcia + akcje (copy prompt, delete, move, rename).
   Lightbox po kliknięciu dużego obrazu.

**Topbar:** [+ Add folder] · [⟳ rescan] · 🔍 search (FTS5).

**Persistencja preferencji UI:** rozmiar kafelka, stan collapsible
lewego panelu, ostatnio wybrana library — w `localStorage`.

**Live updates:** WebSocket → siatka dorzuca kafelki na bieżąco. Jeśli
scroll nie jest na top, badge "N nowych" na górze, klik = scroll-to-top.

**Skróty:** `/` focus search · `Esc` close detail · `← →` prev/next ·
`Del` delete · `Ctrl+C` copy prompt.

## Bezpieczeństwo operacji plikowych

- **Delete → XDG Trash** (`~/.local/share/Trash/files/` + `.trashinfo`
  zgodnie ze spec FreeDesktop.org). Restore przez systemowy menedżer
  kosza Plasma.
- **Confirm dialog** dla wszystkich destrukcyjnych akcji. Bulk delete
  pokazuje liczbę i pierwsze 5 nazw.
- **Walidacja serwera:**
  - move/rename target nie istnieje (else 409)
  - rename: brak `..`, `/` w nazwie
  - move: target musi być wewnątrz jakiejś śledzonej library
  - atomowo: `os.rename` (POSIX atomic w obrębie partycji) → DB UPDATE;
    cross-partition: `shutil.move` z rollback DB on error
- **Audit log** w `file_ops` (10k rolling).
- Brak `--allow-destructive` flag — localhost, single user.

## Stack

- Python 3.12 (przez `uv`)
- FastAPI + uvicorn[standard]
- Pillow (PIL)
- watchdog
- stdlib: `sqlite3`, `threading`, `queue`, `hashlib`, `json`
- Frontend: vanilla JS, brak build step

## Testowanie

- **Unit tests** dla `metadata.py` (parsowanie PNG tEXt, ComfyUI workflow,
  A1111 parameters; fixtures w `tests/fixtures/images/*.png`).
- **Unit tests** dla `fileops.py` (trash, rename, move, walidacja
  ścieżek) na tmpdir.
- **Integration test** dla scanner.py: tmpdir z kilkoma fixture'ami →
  initial scan → asercje na DB.
- **E2E (smoke)** — pojedynczy test który startuje uvicorn na losowym
  porcie, dodaje library, pyta `/api/images`, sprawdza odpowiedź.

Bez testów dla watchdog samego w sobie (zewnętrzna biblioteka). Test
integracyjny scannera + ręczny manual test dla flow live update'ów.

## Cache miniatur

- Klucz cache: `sha1(plik)` → `.work/thumbs/<sha1>.webp`.
- Generowane on-demand przy pierwszym GET `/api/images/{id}/thumb`.
- Gdy plik się zmieni (nowy sha1) → nowy thumb. Stary plik zostaje
  jako sierota w cache.
- **Sweep:** raz na uruchomienie serwera (oraz po `rescan`) — usuń pliki
  z `.work/thumbs/` których sha1 nie występuje w `images.sha1`. Tania
  operacja (jedno SELECT + listdir).
- Brak limitu rozmiaru cache (WebP ~10 KB/thumb × 20k = ~200 MB
  worst-case, akceptowalne).

## Otwarte ryzyka

- **inotify watch limits** przy 20k subfolderów — jeśli wystąpi, log +
  zalecenie `sysctl fs.inotify.max_user_watches=524288`.
- **Niejednolite metadane ComfyUI** — workflow JSON różni się między
  wersjami i custom_nodes. Parser musi być defensywny (try/except, brak
  fielda → NULL).
- **Duże LoRA listy** — gdyby okazało się że LoRA dropdown ma 500+
  pozycji, dodać search w panelu filtrów (na razie YAGNI).
