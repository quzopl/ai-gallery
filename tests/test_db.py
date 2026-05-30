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
