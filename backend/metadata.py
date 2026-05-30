"""Ekstrakcja metadata z plików obrazów AI-generated.

Wspierane źródła:
  - ComfyUI: PNG tEXt chunks 'workflow' (JSON UI) + 'prompt' (JSON exec graph)
  - A1111  : PNG tEXt 'parameters' lub EXIF UserComment (JPG)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PIL import Image

# Wynikowy słownik. Wszystkie pola opcjonalne.
EMPTY: dict[str, Any] = {
    "source_kind": None,
    "prompt": None,
    "negative": None,
    "model_name": None,
    "sampler": None,
    "steps": None,
    "cfg": None,
    "seed": None,
    "raw_metadata": None,
    "loras": [],          # list[tuple[str, float|None]]
    "width": None,
    "height": None,
}


def extract(path: Path) -> dict[str, Any]:
    """Zwróć słownik metadata. Pola których nie wykryto → None / []."""
    out = dict(EMPTY)
    try:
        with Image.open(path) as img:
            out["width"], out["height"] = img.size
            text = getattr(img, "text", {}) or {}
            if "prompt" in text or "workflow" in text:
                _parse_comfyui(text, out)
            elif "parameters" in text:
                _parse_a1111(text["parameters"], out)
            # EXIF UserComment fallback robimy w Task 4
    except Exception:  # noqa: BLE001
        # corrupt/unreadable — zwracamy co mamy (puste pola)
        pass
    return out


def _parse_comfyui(text: dict[str, str], out: dict[str, Any]) -> None:
    out["source_kind"] = "comfyui"
    raw = {k: text[k] for k in ("workflow", "prompt") if k in text}
    out["raw_metadata"] = json.dumps(raw)

    prompt_json: dict[str, Any] | None = None
    if "prompt" in text:
        try:
            prompt_json = json.loads(text["prompt"])
        except json.JSONDecodeError:
            prompt_json = None

    if prompt_json:
        out["prompt"] = _comfy_positive_prompt(prompt_json)
        out["negative"] = _comfy_negative_prompt(prompt_json)
        _comfy_sampler_fields(prompt_json, out)
        out["model_name"] = _comfy_model_name(prompt_json)
        out["loras"] = _comfy_loras(prompt_json)


def _comfy_positive_prompt(graph: dict[str, Any]) -> str | None:
    """Najprościej: weź tekst z CLIPTextEncode podłączonego do KSampler.positive.

    Bez pełnego trace'owania linków robimy heurystykę: pierwszy CLIPTextEncode
    którego tekst NIE wygląda na negatywny.
    """
    candidates: list[str] = []
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
            text = (node.get("inputs") or {}).get("text")
            if isinstance(text, str) and text.strip():
                candidates.append(text)
    if not candidates:
        return None
    for t in candidates:
        if not _looks_negative(t):
            return t
    return candidates[0]


def _comfy_negative_prompt(graph: dict[str, Any]) -> str | None:
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
            text = (node.get("inputs") or {}).get("text")
            if isinstance(text, str) and _looks_negative(text):
                return text
    return None


_NEG_HINTS = ("blurry", "low quality", "lowres", "bad anatomy", "watermark",
              "negative", "deformed")


def _looks_negative(text: str) -> bool:
    t = text.lower()
    return any(h in t for h in _NEG_HINTS)


def _comfy_sampler_fields(graph: dict[str, Any], out: dict[str, Any]) -> None:
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        if "Sampler" in ct or ct.startswith("KSampler"):
            inp = node.get("inputs") or {}
            out["sampler"] = out["sampler"] or _str_or_none(inp.get("sampler_name"))
            out["steps"] = out["steps"] or _int_or_none(inp.get("steps"))
            out["cfg"] = out["cfg"] or _float_or_none(inp.get("cfg"))
            out["seed"] = out["seed"] or _int_or_none(inp.get("seed") or inp.get("noise_seed"))


def _comfy_model_name(graph: dict[str, Any]) -> str | None:
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        if "Checkpoint" in ct or "UNetLoader" in ct or "UNETLoader" in ct:
            inp = node.get("inputs") or {}
            for key in ("ckpt_name", "unet_name", "model_name"):
                v = inp.get(key)
                if isinstance(v, str):
                    return v
    return None


def _comfy_loras(graph: dict[str, Any]) -> list[tuple[str, float | None]]:
    out: list[tuple[str, float | None]] = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        if "Lora" in ct or "LoRA" in ct:
            inp = node.get("inputs") or {}
            name = inp.get("lora_name") or inp.get("name")
            strength = _float_or_none(inp.get("strength_model") or inp.get("strength"))
            if isinstance(name, str):
                out.append((name, strength))
    return out


def _parse_a1111(_params: str, _out: dict[str, Any]) -> None:
    """A1111 'parameters' string — implementacja w Task 4."""
    pass


# ---------- helpery typów ----------

def _str_or_none(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None

def _int_or_none(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

def _float_or_none(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
