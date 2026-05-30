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
        assert max(im.size) == 256


def test_generate_is_cached(tmp_path: Path, src_img: Path) -> None:
    cache_dir = tmp_path / "cache"
    p1 = thumbs.get_or_generate(src_img, sha1="x", cache_dir=cache_dir)
    mtime1 = p1.stat().st_mtime
    p2 = thumbs.get_or_generate(src_img, sha1="x", cache_dir=cache_dir)
    assert p1 == p2
    assert p2.stat().st_mtime == mtime1


def test_sweep_removes_orphans(tmp_path: Path, src_img: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "orphan_sha.webp").write_bytes(b"x")
    kept = thumbs.get_or_generate(src_img, sha1="keep", cache_dir=cache_dir)
    removed = thumbs.sweep(cache_dir, known_sha1s={"keep"})
    assert removed == 1
    assert kept.exists()
    assert not (cache_dir / "orphan_sha.webp").exists()
