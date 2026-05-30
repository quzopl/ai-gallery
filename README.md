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
