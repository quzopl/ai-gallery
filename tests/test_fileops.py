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
    with pytest.raises(ValueError, match="name"):
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
    with pytest.raises(ValueError, match="outside"):
        fileops.move(src, dst_library_root=dst_lib, dst_rel_path="../outside.png")
