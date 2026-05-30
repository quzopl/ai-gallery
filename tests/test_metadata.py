"""Testy ekstrakcji metadata z PNG (ComfyUI + A1111) i EXIF."""
from __future__ import annotations
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from backend import metadata

FIXTURES = Path(__file__).parent / "fixtures" / "images"


def _make_png(path: Path, text_chunks: dict[str, str]) -> None:
    """Zapisz mały PNG z tEXt chunkami."""
    img = Image.new("RGB", (8, 8), "white")
    info = PngInfo()
    for k, v in text_chunks.items():
        info.add_text(k, v)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", pnginfo=info)


@pytest.fixture
def comfy_png(tmp_path: Path) -> Path:
    workflow = {
        "nodes": [
            {"type": "CheckpointLoaderSimple", "widgets_values": ["flux1-dev.safetensors"]},
            {"type": "KSampler", "widgets_values": ["dpm++_2m", "normal", 28, 7.0, 12345, 1.0]},
        ]
    }
    prompt = {
        "3": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "flux1-dev.safetensors"}},
        "5": {"class_type": "KSampler", "inputs": {
            "seed": 12345, "steps": 28, "cfg": 7.0,
            "sampler_name": "dpm++_2m", "scheduler": "normal",
        }},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a portrait of a cat"}},
    }
    p = tmp_path / "comfy.png"
    _make_png(p, {"workflow": json.dumps(workflow), "prompt": json.dumps(prompt)})
    return p


def test_extract_comfyui_prompt(comfy_png: Path) -> None:
    m = metadata.extract(comfy_png)
    assert m["source_kind"] == "comfyui"
    assert "cat" in (m["prompt"] or "")
    assert m["model_name"] == "flux1-dev.safetensors"
    assert m["sampler"] == "dpm++_2m"
    assert m["steps"] == 28
    assert m["cfg"] == 7.0
    assert m["seed"] == 12345


def test_extract_unknown_png(tmp_path: Path) -> None:
    p = tmp_path / "plain.png"
    Image.new("RGB", (4, 4), "red").save(p)
    m = metadata.extract(p)
    assert m["source_kind"] is None
    assert m["prompt"] is None
    assert m["width"] == 4 and m["height"] == 4
