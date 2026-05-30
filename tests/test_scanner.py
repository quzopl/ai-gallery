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
    assert progress_events


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
