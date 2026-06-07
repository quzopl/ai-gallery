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


def test_fts_update_trigger_resyncs(tmpdb: Path) -> None:
    """FTS update trigger usuwa starą wersję i wstawia nową."""
    db.init_schema(tmpdb)
    writer = db.start_writer(tmpdb)
    try:
        lib_id = db.add_library(writer, path="/tmp/u", name="U")
        kwargs = dict(
            library_id=lib_id, rel_path="a.png", sha1="s1",
            mtime=int(time.time()), size_bytes=1, width=1, height=1,
            source_kind="comfyui", negative=None, model_name=None,
            sampler=None, steps=None, cfg=None, seed=None,
            raw_metadata=None, loras=[],
        )
        img_id = db.upsert_image(writer, prompt="first cat", **kwargs)
        # Update: zmiana prompta
        db.upsert_image(writer, prompt="second dog", **kwargs)
        con = sqlite3.connect(tmpdb)
        hits_cat = list(con.execute(
            "SELECT rowid FROM images_fts WHERE images_fts MATCH 'cat'"))
        hits_dog = list(con.execute(
            "SELECT rowid FROM images_fts WHERE images_fts MATCH 'dog'"))
        con.close()
        assert hits_cat == [], "stary prompt 'cat' powinien być usunięty z FTS"
        assert hits_dog == [(img_id,)]
    finally:
        db.stop_writer(writer)


def test_fts_delete_trigger_removes(tmpdb: Path) -> None:
    db.init_schema(tmpdb)
    writer = db.start_writer(tmpdb)
    try:
        lib_id = db.add_library(writer, path="/tmp/d", name="D")
        img_id = db.upsert_image(
            writer, library_id=lib_id, rel_path="a.png", sha1="s",
            mtime=1, size_bytes=1, width=1, height=1,
            source_kind="comfyui", prompt="findable text", negative=None,
            model_name=None, sampler=None, steps=None, cfg=None, seed=None,
            raw_metadata=None, loras=[],
        )
        db.delete_image(writer, image_id=img_id)
        con = sqlite3.connect(tmpdb)
        rows = list(con.execute(
            "SELECT rowid FROM images_fts WHERE images_fts MATCH 'findable'"))
        con.close()
        assert rows == []
    finally:
        db.stop_writer(writer)


def test_lora_junction_rebuilds_on_upsert(tmpdb: Path) -> None:
    """Re-upsert powinien zbudować image_loras od zera (nie akumulować)."""
    db.init_schema(tmpdb)
    writer = db.start_writer(tmpdb)
    try:
        lib_id = db.add_library(writer, path="/tmp/L", name="L")
        base = dict(
            library_id=lib_id, rel_path="a.png", sha1="s",
            mtime=1, size_bytes=1, width=1, height=1,
            source_kind="comfyui", prompt="x", negative=None,
            model_name=None, sampler=None, steps=None, cfg=None, seed=None,
            raw_metadata=None,
        )
        img_id = db.upsert_image(writer, loras=[("char", 0.8), ("style", 0.5)], **base)
        # Re-upsert z innym zestawem LoRA:
        db.upsert_image(writer, loras=[("only_one", 1.0)], **base)
        con = sqlite3.connect(tmpdb)
        names = sorted(r[0] for r in con.execute(
            "SELECT lo.name FROM image_loras il JOIN loras lo ON lo.id=il.lora_id "
            "WHERE il.image_id=?", (img_id,)))
        con.close()
        assert names == ["only_one"], f"oczekiwane ['only_one'], otrzymano {names}"
    finally:
        db.stop_writer(writer)


def test_upsert_with_duplicate_lora(tmpdb: Path) -> None:
    """Ta sama LoRA podana dwa razy (np. model+clip) nie może wywalić upsertu.

    Regresja: metadane z ComfyUI potrafią zawierać tę samą LoRA dwukrotnie;
    wcześniej drugi INSERT do image_loras łamał PRIMARY KEY → IntegrityError
    → cała transakcja cofnięta → obraz nie trafiał do bazy (cicho połknięty
    przez skaner/watchdog).
    """
    db.init_schema(tmpdb)
    writer = db.start_writer(tmpdb)
    try:
        lib_id = db.add_library(writer, path="/tmp/dup", name="DUP")
        img_id = db.upsert_image(
            writer, library_id=lib_id, rel_path="a.png", sha1="s",
            mtime=1, size_bytes=1, width=1, height=1,
            source_kind="comfyui", prompt="x", negative=None,
            model_name=None, sampler=None, steps=None, cfg=None, seed=None,
            raw_metadata=None,
            loras=[("ideogram/bart1.safetensors", 1.0),
                   ("ideogram/bart1.safetensors", 1.0)],
        )
        con = db.readonly(tmpdb)
        row = con.execute("SELECT id FROM images WHERE id=?", (img_id,)).fetchone()
        names = [r[0] for r in con.execute(
            "SELECT lo.name FROM image_loras il JOIN loras lo ON lo.id=il.lora_id "
            "WHERE il.image_id=?", (img_id,))]
        con.close()
        assert row is not None, "obraz musi się zapisać mimo zduplikowanej LoRA"
        assert names == ["ideogram/bart1.safetensors"], \
            f"duplikat powinien zostać zdeduplikowany, otrzymano {names}"
    finally:
        db.stop_writer(writer)


def test_log_file_op_persists(tmpdb: Path) -> None:
    db.init_schema(tmpdb)
    writer = db.start_writer(tmpdb)
    try:
        db.log_file_op(writer, op="delete", library_id=None,
                       from_path="/a", to_path=None, success=True, error=None)
        db.log_file_op(writer, op="rename", library_id=None,
                       from_path="/b", to_path="/c", success=False, error="boom")
        con = sqlite3.connect(tmpdb)
        rows = list(con.execute(
            "SELECT op, from_path, to_path, success, error FROM file_ops ORDER BY id"))
        con.close()
        assert rows == [
            ("delete", "/a", None, 1, None),
            ("rename", "/b", "/c", 0, "boom"),
        ]
    finally:
        db.stop_writer(writer)


def test_readonly_returns_row_factory(tmpdb: Path) -> None:
    db.init_schema(tmpdb)
    writer = db.start_writer(tmpdb)
    try:
        db.add_library(writer, path="/tmp/r", name="R")
    finally:
        db.stop_writer(writer)
    con = db.readonly(tmpdb)
    row = con.execute("SELECT name FROM libraries").fetchone()
    con.close()
    assert row["name"] == "R"
