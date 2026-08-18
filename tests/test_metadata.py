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


_CAPTION = {
    "high_level_description": "A portrait of quz0 in a modern city",
    "style_description": {"aesthetics": "cinematic moody", "lighting": "soft ambient"},
    "compositional_deconstruction": {
        "background": "urban bokeh background",
        "elements": [{"type": "obj", "desc": "quz0 athletic man with chest hair"}],
    },
}


def test_comfy_json_caption_routed_through_switch(tmp_path: Path) -> None:
    """Prompt JSON lives in a PrimitiveString routed via ComfySwitchNode into
    CLIPTextEncode (text is a link, not a literal) — as saved by stock SaveImage."""
    prompt = {
        "1": {"class_type": "SamplerCustomAdvanced", "inputs": {"guider": ["2", 0]}},
        "2": {"class_type": "DualModelGuider",
              "inputs": {"positive": ["3", 0], "negative": ["6", 0], "cfg": 6.0}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": ["4", 0]}},
        "4": {"class_type": "ComfySwitchNode",
              "inputs": {"switch": False, "on_false": ["5", 0], "on_true": ["3", 0]}},
        "5": {"class_type": "PrimitiveStringMultiline",
              "inputs": {"value": json.dumps(_CAPTION)}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["3", 0]}},
    }
    p = tmp_path / "cjson.png"
    _make_png(p, {"prompt": json.dumps(prompt)})
    m = metadata.extract(p)
    assert m["source_kind"] == "comfyui"
    assert "quz0" in (m["prompt"] or "")
    assert "urban bokeh background" in (m["prompt"] or "")
    assert "quz0 athletic man with chest hair" in (m["prompt"] or "")
    assert m["negative"] is None  # zeroed branch must not echo positive
    assert "{" not in (m["prompt"] or "")  # flattened, not raw JSON
    # original structured JSON preserved alongside the readable prompt
    assert m["prompt_json"] is not None
    assert json.loads(m["prompt_json"])["high_level_description"].startswith("A portrait of quz0")


def test_ai_gallery_json_caption_flattened(tmp_path: Path) -> None:
    meta = {"version": 1, "source": "other-module",
            "prompt": json.dumps(_CAPTION), "model_name": "some.safetensors"}
    p = tmp_path / "agjson.png"
    _make_png(p, {"ai_gallery_meta": json.dumps(meta)})
    m = metadata.extract(p)
    assert m["source_kind"] == "ai_gallery"
    assert "quz0" in (m["prompt"] or "")
    assert "soft ambient" in (m["prompt"] or "")  # style fields surfaced
    assert "{" not in (m["prompt"] or "")
    assert m["prompt_json"] is not None and '"high_level_description"' in m["prompt_json"]


def test_plain_prompt_has_no_prompt_json(tmp_path: Path) -> None:
    params = ("just a plain prompt, nothing structured\n"
              "Steps: 20, Sampler: Euler, CFG scale: 7, Seed: 1, Size: 512x512, Model: x")
    p = tmp_path / "plain_a1111.png"
    _make_png(p, {"parameters": params})
    m = metadata.extract(p)
    assert m["prompt_json"] is None
    assert "plain prompt" in (m["prompt"] or "")


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


def test_comfy_prompt_through_preview_any_and_llm_switch(tmp_path: Path) -> None:
    """Positive text goes CLIPTextEncode → PreviewAny(source) → ComfySwitchNode whose
    active branch is an LLM TextGenerate (output not stored in the file). The
    tracer must pass through PreviewAny and fall back to the other branch, which
    holds the user's raw prompt."""
    prompt = {
        "53": {"class_type": "KSampler", "inputs": {
            "positive": ["79", 0], "negative": ["58", 0], "seed": ["76", 0],
            "steps": 13, "cfg": 1.0, "sampler_name": "euler"}},
        "58": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["79", 0]}},
        "60": {"class_type": "TextGenerate", "inputs": {"prompt": ["61", 0], "max_length": 512}},
        "61": {"class_type": "StringConcatenate",
               "inputs": {"string_a": ["62", 0], "string_b": ["63", 0], "delimiter": ""}},
        "62": {"class_type": "PrimitiveStringMultiline",
               "inputs": {"value": "You are an expert prompt engineer. Expand the prompt."}},
        "63": {"class_type": "PrimitiveStringMultiline",
               "inputs": {"value": "portrait of a man by a rooftop infinity pool in Dubai"}},
        "65": {"class_type": "ComfySwitchNode",
               "inputs": {"switch": ["68", 0], "on_false": ["63", 0], "on_true": ["60", 0]}},
        "68": {"class_type": "PrimitiveBoolean", "inputs": {"value": True}},
        "78": {"class_type": "PreviewAny", "inputs": {"source": ["65", 0]}},
        "79": {"class_type": "CLIPTextEncode", "inputs": {"text": ["78", 0]}},
    }
    p = tmp_path / "llm_switch.png"
    _make_png(p, {"prompt": json.dumps(prompt)})
    m = metadata.extract(p)
    assert m["prompt"] == "portrait of a man by a rooftop infinity pool in Dubai"
    assert m["negative"] is None


# ---- generic ComfyUI text tracing across real-world node families ----------

_KS = {"class_type": "KSampler", "inputs": {
    "positive": ["P", 0], "negative": ["N", 0], "seed": 1, "steps": 20, "cfg": 5.0,
    "sampler_name": "euler"}}

_COMFY_CASES = {
    "flux_single_encoder_for_both": ({
        "S": _KS | {"inputs": _KS["inputs"] | {"negative": ["P", 0]}},
        "P": {"class_type": "CLIPTextEncodeFlux",
              "inputs": {"clip_l": "man in polo", "t5xxl": "man in a black polo shirt", "guidance": 3.5}},
    }, "man in a black polo shirt", None),
    "cr_prompt_text_via_guider": ({
        "S": {"class_type": "SamplerCustomAdvanced", "inputs": {"guider": ["G", 0]}},
        "G": {"class_type": "BasicGuider", "inputs": {"conditioning": ["E", 0]}},
        "E": {"class_type": "CLIPTextEncode", "inputs": {"text": ["T", 0]}},
        "T": {"class_type": "CR Prompt Text", "inputs": {"prompt": "switch face, quz0 man"}},
    }, "switch face, quz0 man", None),
    "showtext_cached_output_of_generator": ({
        "S": _KS | {"inputs": _KS["inputs"] | {"negative": ["E", 1]}},
        "P": {"class_type": "CLIPTextEncode", "inputs": {"text": ["F", 2]}},
        "E": {"class_type": "CLIPTextEncode", "inputs": {"text": ["F", 2]}},
        "F": {"class_type": "Florence2Run", "inputs": {"image": ["I", 0], "task": "caption",
                                                       "max_new_tokens": 1024}},
        "X": {"class_type": "ShowText|pysssss",
              "inputs": {"text": ["F", 2], "text_0": "The image shows a woman in a suit"}},
    }, "The image shows a woman in a suit", None),
    "text_concatenate_join": ({
        "S": {"class_type": "SamplerCustomAdvanced", "inputs": {"guider": ["G", 0]}},
        "G": {"class_type": "BasicGuider", "inputs": {"conditioning": ["E", 0]}},
        "E": {"class_type": "CLIPTextEncode", "inputs": {"text": ["C", 0]}},
        "C": {"class_type": "Text Concatenate",
              "inputs": {"delimiter": ", ", "clean_whitespace": "true",
                         "text_a": ["W", 0], "text_b": "", "text_d": "cinematic"}},
        "W": {"class_type": "Wildcard Processor", "inputs": {"prompt": "young woman, avant garde"}},
    }, "young woman, avant garde, cinematic", None),
    "rgthree_any_switch_and_crystools_switch": ({
        "S": {"class_type": "SamplerCustomAdvanced", "inputs": {"guider": ["G", 0]}},
        "G": {"class_type": "BasicGuider", "inputs": {"conditioning": ["E", 0]}},
        "E": {"class_type": "CLIPTextEncode", "inputs": {"text": ["A", 0]}},
        "A": {"class_type": "Any Switch (rgthree)", "inputs": {"any_01": ["B", 0], "any_04": ["T", 0]}},
        "B": {"class_type": "Switch any [Crystools]",
              "inputs": {"boolean": False, "on_true": ["X", 0], "on_false": ["T", 0]}},
        "X": {"class_type": "ShowText|pysssss", "inputs": {"text": ["F", 0], "text2": "florence caption"}},
        "F": {"class_type": "Florence2Run", "inputs": {"max_new_tokens": 1024}},
        "T": {"class_type": "CR Prompt Text", "inputs": {"prompt": "photograph of a young woman"}},
    }, "photograph of a young woman", None),
    "sdxl_prompt_styler_into_flux_encoders": ({
        "S": _KS,
        "P": {"class_type": "CLIPTextEncodeFlux", "inputs": {"clip_l": ["Y", 1], "t5xxl": ["Y", 0]}},
        "N": {"class_type": "CLIPTextEncodeFlux", "inputs": {"clip_l": ["L", 0], "t5xxl": ["L", 0]}},
        "L": {"class_type": "String Literal (Image Saver)", "inputs": {"string": ""}},
        "Y": {"class_type": "SDXLPromptStyler",
              "inputs": {"text_positive": "photograph of a young man", "text_negative": "blurry, 4k",
                         "style": "sai-cinematic"}},
    }, "photograph of a young man", None),
    "qwen_image_edit_prompt": ({
        "S": _KS | {"inputs": _KS["inputs"] | {"negative": ["P", 0]}},
        "P": {"class_type": "TextEncodeQwenImageEdit",
              "inputs": {"prompt": "man holding a sign that says hello", "image": ["I", 0]}},
    }, "man holding a sign that says hello", None),
    "impact_wildcard_populated_text": ({
        "S": _KS,
        "P": {"class_type": "CLIPTextEncode", "inputs": {"text": ["W", 3]}},
        "N": {"class_type": "CLIPTextEncode", "inputs": {"text": ["L", 0]}},
        "L": {"class_type": "String Literal (Image Saver)", "inputs": {"string": "worst quality, deformed"}},
        "W": {"class_type": "ImpactWildcardEncode",
              "inputs": {"wildcard_text": "headshot of __person__", "populated_text": "headshot of a man",
                         "mode": False}},
    }, "headshot of a man", "worst quality, deformed"),
    "wan_video_no_sampler_link_fallback_scan": ({
        "S": {"class_type": "WanVideoSampler_F2", "inputs": {"start_image": ["I", 0], "config": ["C", 0]}},
        "C": {"class_type": "WanVideoConfigure_F2",
              "inputs": {"positive": "a viking woman warrior with curly hair",
                         "negative": "overexposed, static, blurred details", "width": 512}},
    }, "a viking woman warrior with curly hair", "overexposed, static, blurred details"),
}


@pytest.mark.parametrize("name", list(_COMFY_CASES))
def test_comfy_generic_text_tracing(tmp_path: Path, name: str) -> None:
    graph, want_prompt, want_negative = _COMFY_CASES[name]
    p = tmp_path / f"{name}.png"
    _make_png(p, {"prompt": json.dumps(graph)})
    m = metadata.extract(p)
    assert m["prompt"] == want_prompt
    assert m["negative"] == want_negative


def test_comfy_conditioning_passthrough_picks_side_by_output_slot(tmp_path: Path) -> None:
    """LTXVConditioning-style node: (positive, negative) in, [positive, negative]
    out. Sampler links to slot 0 / slot 1 must resolve to different texts."""
    graph = {
        "S": {"class_type": "SamplerCustom", "inputs": {"positive": ["C", 0], "negative": ["C", 1]}},
        "C": {"class_type": "LTXVConditioning",
              "inputs": {"positive": ["P", 0], "negative": ["N", 0], "frame_rate": 25}},
        "P": {"class_type": "CLIPTextEncode", "inputs": {"text": ["W", 0]}},
        "W": {"class_type": "Switch any [Crystools]",
              "inputs": {"boolean": True, "on_true": ["L", 0], "on_false": ["X", 0]}},
        "L": {"class_type": "String", "inputs": {"String": "storm waves and lightning"}},
        "X": {"class_type": "String", "inputs": {"String": ""}},
        "N": {"class_type": "CLIPTextEncode", "inputs": {"text": "low quality, worst quality"}},
    }
    p = tmp_path / "ltxv.png"
    _make_png(p, {"prompt": json.dumps(graph)})
    m = metadata.extract(p)
    assert m["prompt"] == "storm waves and lightning"
    assert m["negative"] == "low quality, worst quality"


def test_comfy_saver_placeholder_negative_is_ignored(tmp_path: Path) -> None:
    """Image Saver nodes carry `negative: "unknown"` for their own metadata;
    that must not leak into the negative field when the graph has a real
    positive link but no negative one."""
    graph = {
        "S": {"class_type": "SamplerCustomAdvanced", "inputs": {"guider": ["G", 0]}},
        "G": {"class_type": "BasicGuider", "inputs": {"conditioning": ["E", 0]}},
        "E": {"class_type": "CLIPTextEncode", "inputs": {"text": "a resolute man"}},
        "V": {"class_type": "Image Saver", "inputs": {"positive": ["E", 0], "negative": "unknown",
                                                     "images": ["D", 0]}},
    }
    p = tmp_path / "saver.png"
    _make_png(p, {"prompt": json.dumps(graph)})
    m = metadata.extract(p)
    assert m["prompt"] == "a resolute man"
    assert m["negative"] is None


# ---- sampler fields via links / helper nodes, LoRA stack nodes --------------

def _extract_graph(tmp_path: Path, name: str, graph: dict) -> dict:
    p = tmp_path / f"{name}.png"
    _make_png(p, {"prompt": json.dumps(graph)})
    return metadata.extract(p)


def test_comfy_sampler_fields_follow_links_to_primitives(tmp_path: Path) -> None:
    m = _extract_graph(tmp_path, "linked_fields", {
        "1": {"class_type": "KSampler", "inputs": {
            "seed": ["9", 0], "steps": 13, "cfg": ["8", 0], "sampler_name": "euler",
            "positive": ["2", 0], "negative": ["2", 0]}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
        "8": {"class_type": "PrimitiveFloat", "inputs": {"value": 3.5}},
        "9": {"class_type": "Seed (rgthree)", "inputs": {"seed": 876576531857229}},
    })
    assert m["seed"] == 876576531857229
    assert m["cfg"] == 3.5
    assert m["steps"] == 13


def test_comfy_sampler_custom_advanced_helper_nodes(tmp_path: Path) -> None:
    """SamplerCustomAdvanced graphs keep seed/steps/cfg/sampler in RandomNoise,
    BasicScheduler, CFGGuider and KSamplerSelect."""
    m = _extract_graph(tmp_path, "sca", {
        "13": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["25", 0], "guider": ["22", 0], "sampler": ["16", 0], "sigmas": ["17", 0]}},
        "25": {"class_type": "RandomNoise", "inputs": {"noise_seed": ["30", 0]}},
        "30": {"class_type": "Seed Generator", "inputs": {"seed": 4242}},
        "22": {"class_type": "CFGGuider", "inputs": {"cfg": 4.0, "positive": ["6", 0], "negative": ["7", 0]}},
        "16": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "dpmpp_2m"}},
        "17": {"class_type": "BasicScheduler", "inputs": {"scheduler": "beta", "steps": 28, "denoise": 1.0}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a dog"}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    })
    assert m["prompt"] == "a dog"
    assert m["seed"] == 4242
    assert m["steps"] == 28
    assert m["cfg"] == 4.0
    assert m["sampler"] == "dpmpp_2m"


def test_comfy_seed_zero_is_kept(tmp_path: Path) -> None:
    m = _extract_graph(tmp_path, "seed0", {
        "1": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 5, "cfg": 1.0,
                                                   "sampler_name": "euler"}},
    })
    assert m["seed"] == 0


def test_comfy_power_lora_loader_rgthree(tmp_path: Path) -> None:
    m = _extract_graph(tmp_path, "power_lora", {
        "83": {"class_type": "Power Lora Loader (rgthree)", "inputs": {
            "PowerLoraLoaderHeaderWidget": {"type": "PowerLoraLoaderHeaderWidget"},
            "lora_1": {"on": True, "lora": "krea2/krea2_bart_fresh.safetensors", "strength": 1},
            "lora_2": {"on": False, "lora": "krea2/cyber.safetensors", "strength": 0.77},
            "lora_7": {"on": True, "lora": "krea2/styl.safetensors", "strength": 0.49},
            "➕ Add Lora": "", "model": ["55", 0], "clip": ["56", 0]}},
    })
    assert m["loras"] == [("krea2/krea2_bart_fresh.safetensors", 1.0),
                          ("krea2/styl.safetensors", 0.49)]


def test_comfy_lora_loader_stack_rgthree(tmp_path: Path) -> None:
    m = _extract_graph(tmp_path, "lora_stack", {
        "5": {"class_type": "Lora Loader Stack (rgthree)", "inputs": {
            "lora_01": "my_first_lora.safetensors", "strength_01": 1.0,
            "lora_02": "styl.safetensors", "strength_02": 0.8,
            "lora_03": "None", "strength_03": 1.0,
            "lora_04": "None", "strength_04": 1.0,
            "model": ["1", 0], "clip": ["2", 0]}},
    })
    assert m["loras"] == [("my_first_lora.safetensors", 1.0), ("styl.safetensors", 0.8)]


def test_comfy_lora_loader_stacked_advanced_dict_name(tmp_path: Path) -> None:
    m = _extract_graph(tmp_path, "lora_adv", {
        "89": {"class_type": "LoraLoaderStackedAdvanced", "inputs": {
            "lora_name": {"content": "zavy-cbrspc-flx.safetensors", "image": None, "type": "loras"},
            "lora_weight": 0.63, "force_fetch": False}},
        "90": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "lora_name": "b1982.safetensors", "strength_model": 1, "model": ["1", 0]}},
    })
    assert m["loras"] == [("zavy-cbrspc-flx.safetensors", 0.63), ("b1982.safetensors", 1.0)]


def test_comfy_cr_lora_stack_only_switched_on(tmp_path: Path) -> None:
    m = _extract_graph(tmp_path, "cr_stack", {
        "4": {"class_type": "CR LoRA Stack", "inputs": {
            "switch_1": "On", "lora_name_1": "a.safetensors", "model_weight_1": 0.9, "clip_weight_1": 1.0,
            "switch_2": "Off", "lora_name_2": "b.safetensors", "model_weight_2": 1.0, "clip_weight_2": 1.0,
            "switch_3": "On", "lora_name_3": "None", "model_weight_3": 1.0, "clip_weight_3": 1.0}},
    })
    assert m["loras"] == [("a.safetensors", 0.9)]
