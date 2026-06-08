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

# Linia parametrów A1111: "Key: value, Key: value, ..."
# Wartość może być w cudzysłowach z przecinkami w środku.
_KV_RE = re.compile(r'([A-Za-z][A-Za-z0-9 ]*?):\s*("(?:[^"]|\\")*"|[^,]+?)(?=,\s*[A-Za-z][A-Za-z0-9 ]*?:\s*|$)')
_LORA_RE = re.compile(r'<lora:([^:>]+):([\d.]+)>', re.IGNORECASE)

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
            # Priorytet: nasz własny chunk z ComfyUI plugina (idealne dane).
            if "ai_gallery_meta" in text and _parse_ai_gallery_meta(text["ai_gallery_meta"], out):
                pass
            elif "prompt" in text or "workflow" in text:
                _parse_comfyui(text, out)
            elif "parameters" in text:
                _parse_a1111(text["parameters"], out)
            else:
                params = _read_exif_user_comment(img)
                if params:
                    _parse_a1111(params, out)
    except Exception:  # noqa: BLE001
        # corrupt/unreadable — zwracamy co mamy (puste pola)
        pass
    # Structured JSON caption (Ideogram-4 style) → readable prompt, regardless
    # of which module/source produced it.
    out["prompt"] = _flatten_caption(out["prompt"])
    return out


def _parse_ai_gallery_meta(raw: str, out: dict[str, Any]) -> bool:
    """Parsuj chunk 'ai_gallery_meta' z naszego pluginu ComfyUI.
    Zwróć True jeśli dane były poprawne i zapisaliśmy do out."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    out["source_kind"] = "ai_gallery"
    out["raw_metadata"] = raw
    for key in ("prompt", "negative", "model_name", "sampler"):
        v = data.get(key)
        if isinstance(v, str):
            out[key] = v
    for key in ("steps", "seed"):
        v = data.get(key)
        if isinstance(v, (int, float)):
            out[key] = int(v)
    if isinstance(data.get("cfg"), (int, float)):
        out["cfg"] = float(data["cfg"])
    loras_raw = data.get("loras") or []
    loras: list[tuple[str, float | None]] = []
    for item in loras_raw:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            s = item.get("strength")
            try:
                strength = float(s) if s is not None else None
            except (TypeError, ValueError):
                strength = None
            loras.append((item["name"], strength))
    out["loras"] = loras
    if isinstance(data.get("width"), int) and isinstance(data.get("height"), int):
        out["width"] = data["width"]
        out["height"] = data["height"]
    return True


def _read_exif_user_comment(img: Image.Image) -> str | None:
    try:
        exif = img.getexif()
    except Exception:  # noqa: BLE001
        return None
    raw = exif.get(0x9286)
    if not raw:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        if raw.startswith(b"UNICODE\x00"):
            try:
                return raw[8:].decode("utf-16-be")
            except UnicodeDecodeError:
                return None
        if raw.startswith(b"ASCII\x00\x00\x00"):
            return raw[8:].decode("ascii", errors="replace")
        return raw.decode("utf-8", errors="replace")
    return None


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
        pos, neg = _comfy_sampler_conditioning(prompt_json)
        p = _trace_text(prompt_json, pos) if pos is not None else None
        n = _trace_text(prompt_json, neg) if neg is not None else None
        # Fallbacks for graphs without a traceable sampler→positive link.
        if not p:
            p = _comfy_positive_prompt(prompt_json) or _scan_caption(prompt_json)
        out["prompt"] = p
        out["negative"] = n or _comfy_negative_prompt(prompt_json)
        _comfy_sampler_fields(prompt_json, out)
        out["model_name"] = _comfy_model_name(prompt_json)
        out["loras"] = _comfy_loras(prompt_json)


def _resolve_link(graph: dict[str, Any], value: Any) -> dict | None:
    if isinstance(value, list) and len(value) == 2:
        node = graph.get(str(value[0]))
        return node if isinstance(node, dict) else None
    return None


def _comfy_sampler_conditioning(graph: dict[str, Any]) -> tuple[Any, Any]:
    """Return the (positive, negative) link refs feeding the first sampler,
    following a guider node for SamplerCustom* graphs."""
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        if "KSampler" in ct or "SamplerCustom" in ct:
            inp = node.get("inputs") or {}
            pos, neg = inp.get("positive"), inp.get("negative")
            if pos is None and neg is None and "guider" in inp:
                guider = _resolve_link(graph, inp.get("guider"))
                if guider:
                    gin = guider.get("inputs") or {}
                    pos, neg = gin.get("positive"), gin.get("negative")
            return pos, neg
    return None, None


def _trace_text(graph: dict[str, Any], value: Any, depth: int = 0) -> str | None:
    """Walk a link to the text that ultimately feeds a conditioning input.

    Handles literal strings, CLIPTextEncode `text`, string primitives (`value`),
    boolean routers (ComfySwitchNode on_true/on_false), the Ideogram-4 builder,
    and nested conditioning. ConditioningZeroOut → empty (zeroed branch)."""
    if depth > 8:
        return None
    if isinstance(value, str):
        return value
    if not (isinstance(value, list) and len(value) == 2):
        return None
    node = graph.get(str(value[0]))
    if not isinstance(node, dict):
        return None
    ct = node.get("class_type", "")
    if ct == "ConditioningZeroOut":
        return None
    inp = node.get("inputs") or {}
    # boolean routers: follow the active branch (switch literal) or try both
    if "on_true" in inp or "on_false" in inp:
        sw = inp.get("switch")
        order = (("on_true",) if sw is True
                 else ("on_false",) if sw is False
                 else ("on_true", "on_false"))
        for b in order:
            if b in inp:
                t = _trace_text(graph, inp[b], depth + 1)
                if t:
                    return t
        return None
    if "text" in inp:
        txt = inp["text"]
        return txt if isinstance(txt, str) else _trace_text(graph, txt, depth + 1)
    if "value" in inp:  # PrimitiveString / PrimitiveStringMultiline / literals
        val = inp["value"]
        if isinstance(val, str):
            return val
        t = _trace_text(graph, val, depth + 1)
        if t:
            return t
    if ct == "Ideogram4PromptBuilderKJ":
        hld = inp.get("high_level_description")
        if isinstance(hld, str) and hld.strip():
            return hld
    for key in ("conditioning", "positive", "negative", "text_g", "text_l"):
        if key in inp:
            t = _trace_text(graph, inp[key], depth + 1)
            if t:
                return t
    return None


def _scan_caption(graph: dict[str, Any]) -> str | None:
    """Last resort: any string input anywhere that looks like a JSON caption."""
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        for v in (node.get("inputs") or {}).values():
            if isinstance(v, str) and _looks_like_caption(v):
                return v
    return None


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


def _parse_a1111(params: str, out: dict[str, Any]) -> None:
    out["source_kind"] = "a1111"
    out["raw_metadata"] = params
    lines = params.splitlines()
    neg_idx = next((i for i, ln in enumerate(lines) if ln.startswith("Negative prompt:")), None)
    param_idx = next((i for i, ln in enumerate(lines) if ln.startswith("Steps:")), None)

    if neg_idx is not None:
        pos = "\n".join(lines[:neg_idx]).strip()
        if param_idx is not None:
            neg = " ".join(lines[neg_idx:param_idx]).removeprefix("Negative prompt:").strip()
        else:
            neg = " ".join(lines[neg_idx:]).removeprefix("Negative prompt:").strip()
    else:
        pos = "\n".join(lines[:param_idx] if param_idx is not None else lines).strip()
        neg = None

    out["prompt"] = pos or None
    out["negative"] = neg or None

    if param_idx is not None:
        kv_line = ", ".join(lines[param_idx:])
        for m in _KV_RE.finditer(kv_line):
            k, v = m.group(1).strip(), m.group(2).strip().strip('"')
            if k == "Steps":
                out["steps"] = _int_or_none(v)
            elif k == "Sampler":
                out["sampler"] = v
            elif k == "CFG scale":
                out["cfg"] = _float_or_none(v)
            elif k == "Seed":
                out["seed"] = _int_or_none(v)
            elif k == "Model":
                out["model_name"] = v

    if pos:
        out["loras"] = [(name, float(strength)) for name, strength in _LORA_RE.findall(pos)]


# ---------- structured JSON caption (Ideogram-4 style) ----------

def _looks_like_caption(s: str) -> bool:
    t = s.strip()
    return t.startswith("{") and (
        "high_level_description" in t or "compositional_deconstruction" in t
    )


def _load_json_object(s: str) -> dict | None:
    """Parse a JSON object, tolerating trailing junk after the closing brace."""
    s = s.strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(s[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except (json.JSONDecodeError, ValueError):
                    return None
    return None


def _flatten_caption(prompt: str | None) -> str | None:
    """Turn a structured caption JSON into a readable, searchable prompt.
    Non-caption prompts pass through unchanged."""
    if not isinstance(prompt, str) or not _looks_like_caption(prompt):
        return prompt
    obj = _load_json_object(prompt)
    if not isinstance(obj, dict):
        return prompt
    parts: list[str] = []
    hld = obj.get("high_level_description")
    if isinstance(hld, str) and hld.strip():
        parts.append(hld.strip())
    cd = obj.get("compositional_deconstruction")
    if isinstance(cd, dict):
        bg = cd.get("background")
        if isinstance(bg, str) and bg.strip():
            parts.append(bg.strip())
        for el in cd.get("elements") or []:
            if not isinstance(el, dict):
                continue
            desc = el.get("desc")
            if isinstance(desc, str) and desc.strip():
                parts.append(desc.strip())
            txt = el.get("text")
            if isinstance(txt, str) and txt.strip():
                parts.append(f'text: "{txt.strip()}"')
    sd = obj.get("style_description")
    if isinstance(sd, dict):
        for k in ("aesthetics", "lighting", "photo", "art_style", "medium"):
            v = sd.get(k)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
    return "\n".join(parts) if parts else prompt


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
