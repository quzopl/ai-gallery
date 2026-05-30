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


def test_extract_a1111_png(tmp_path: Path) -> None:
    params = (
        "a beautiful sunset over mountains, photorealistic\n"
        "Negative prompt: blurry, low quality\n"
        "Steps: 30, Sampler: DPM++ 2M Karras, CFG scale: 7.5, Seed: 987654321, "
        "Size: 1024x1024, Model hash: abc123, Model: sd_xl_base_1.0, "
        "Lora hashes: \"char_v1: aaa, style_v2: bbb\""
    )
    p = tmp_path / "a1111.png"
    _make_png(p, {"parameters": params})
    m = metadata.extract(p)
    assert m["source_kind"] == "a1111"
    assert "sunset" in m["prompt"]
    assert m["negative"] == "blurry, low quality"
    assert m["steps"] == 30
    assert m["sampler"] == "DPM++ 2M Karras"
    assert m["cfg"] == 7.5
    assert m["seed"] == 987654321
    assert m["model_name"] == "sd_xl_base_1.0"


def test_extract_a1111_jpg_exif(tmp_path: Path) -> None:
    """A1111 JPG zapisuje parameters w EXIF UserComment (tag 0x9286)."""
    params = "city street at night\nNegative prompt: cars\nSteps: 20, Sampler: Euler, CFG scale: 5, Seed: 1, Size: 512x512, Model: somemodel"
    p = tmp_path / "a1111.jpg"
    img = Image.new("RGB", (16, 16), "blue")
    # UserComment 8-byte charset header + UTF-16-BE text:
    user_comment = b"UNICODE\x00" + params.encode("utf-16-be")
    exif_dict = img.getexif()
    exif_dict[0x9286] = user_comment
    img.save(p, "JPEG", exif=exif_dict.tobytes())

    m = metadata.extract(p)
    assert m["source_kind"] == "a1111"
    assert "city" in m["prompt"]
    assert m["sampler"] == "Euler"


def test_lora_extraction_from_a1111_prompt(tmp_path: Path) -> None:
    params = (
        "a portrait <lora:char_ohwx:0.8> <lora:style_anime:0.5>\n"
        "Steps: 20, Sampler: Euler, CFG scale: 7, Seed: 1, Size: 512x512, Model: x"
    )
    p = tmp_path / "lora.png"
    _make_png(p, {"parameters": params})
    m = metadata.extract(p)
    names = {n for n, _ in m["loras"]}
    assert names == {"char_ohwx", "style_anime"}
    strengths = dict(m["loras"])
    assert strengths["char_ohwx"] == 0.8
    assert strengths["style_anime"] == 0.5
