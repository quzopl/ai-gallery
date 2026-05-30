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
    os.rename(path, dst)
    return dst


def move(path: Path, *, dst_library_root: Path, dst_rel_path: str) -> Path:
    """Przenieś plik do innej biblioteki / podścieżki.

    Wymaga że dst_rel_path NIE wychodzi poza dst_library_root.
    """
    dst_library_root = dst_library_root.resolve()
    dst = (dst_library_root / dst_rel_path).resolve()
    try:
        dst.relative_to(dst_library_root)
    except ValueError as exc:
        raise ValueError("target poza biblioteką docelową") from exc
    if dst.exists():
        raise FileExistsError(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dst))
    return dst
