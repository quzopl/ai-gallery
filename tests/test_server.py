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


def test_list_images_by_library(app_with_tmpdb) -> None:
    client, tmp_path = app_with_tmpdb
    lib_path = tmp_path / "photos"; lib_path.mkdir()
    _make_comfy_png(lib_path / "a.png")
    _make_comfy_png(lib_path / "b.png")
    r = client.post("/api/libraries", json={"path": str(lib_path)})
    lib_id = r.json()["id"]
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
    r = None
    while time.time() < deadline:
        r = client.get("/api/images?q=cat").json()
        if r["items"]:
            break
        time.sleep(0.2)
    assert r and r["items"], "FTS nie znalazł 'cat'"
    r2 = client.get("/api/images?q=xyzunknown").json()
    assert r2["items"] == []


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
    time.sleep(1.5)
    r = client.get("/api/facets").json()
    assert "models" in r and "loras" in r


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
        import time
        deadline = time.time() + 5
        seen_done = False
        while time.time() < deadline and not seen_done:
            try:
                msg = ws.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:
                time.sleep(0.1)
                continue
            if msg.get("type") == "scan_done":
                seen_done = True
        assert seen_done


def _wait_first_image(client) -> int:
    import time
    deadline = time.time() + 5
    while time.time() < deadline:
        items = client.get("/api/images").json()["items"]
        if items:
            return items[0]["id"]
        time.sleep(0.2)
    raise AssertionError("no image indexed")


def test_export_image_copies_to_folder(app_with_tmpdb) -> None:
    client, tmp_path = app_with_tmpdb
    lib_path = tmp_path / "photos"; lib_path.mkdir()
    _make_comfy_png(lib_path / "pic.png")
    client.post("/api/libraries", json={"path": str(lib_path)})
    img_id = _wait_first_image(client)
    out = tmp_path / "export" / "sub"
    r = client.post(f"/api/images/{img_id}/export", json={"to_dir": str(out)})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "exported"
    assert Path(r.json()["path"]) == out / "pic.png"
    assert (out / "pic.png").exists()
    assert (lib_path / "pic.png").exists()
    audit = client.get("/api/audit").json()
    assert audit[0]["op"] == "export" and audit[0]["success"]


def test_export_image_rejects_bad_target(app_with_tmpdb) -> None:
    client, tmp_path = app_with_tmpdb
    lib_path = tmp_path / "photos"; lib_path.mkdir()
    _make_comfy_png(lib_path / "pic.png")
    client.post("/api/libraries", json={"path": str(lib_path)})
    img_id = _wait_first_image(client)
    r = client.post(f"/api/images/{img_id}/export", json={"to_dir": ""})
    assert r.status_code == 400
    notdir = tmp_path / "f.txt"; notdir.write_text("x")
    r = client.post(f"/api/images/{img_id}/export", json={"to_dir": str(notdir)})
    assert r.status_code == 400
    r = client.post("/api/images/999999/export", json={"to_dir": str(tmp_path)})
    assert r.status_code == 404


def test_browse_mkdir_creates_folder(app_with_tmpdb) -> None:
    client, tmp_path = app_with_tmpdb
    r = client.post("/api/browse/mkdir", json={"parent": str(tmp_path), "name": "nowy"})
    assert r.status_code == 200, r.text
    assert Path(r.json()["path"]) == tmp_path / "nowy"
    assert (tmp_path / "nowy").is_dir()
    # already exists → 400
    r = client.post("/api/browse/mkdir", json={"parent": str(tmp_path), "name": "nowy"})
    assert r.status_code == 400
    # invalid names
    for bad in ("", " ", "..", "a/b", "a\\b"):
        r = client.post("/api/browse/mkdir", json={"parent": str(tmp_path), "name": bad})
        assert r.status_code == 400, bad
    # parent must exist
    r = client.post("/api/browse/mkdir", json={"parent": str(tmp_path / "nope"), "name": "x"})
    assert r.status_code == 400
