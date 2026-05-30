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
