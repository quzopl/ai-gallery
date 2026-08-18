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
    "prompt_json": None,
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
    # Structured JSON caption (Ideogram-4 style): keep the original JSON in
    # prompt_json AND expose a readable, FTS-searchable prompt — regardless of
    # which module/source produced it.
    out["prompt_json"], out["prompt"] = _split_caption(out["prompt"])
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
            p = (_comfy_positive_prompt(prompt_json)
                 or _scan_caption(prompt_json)
                 or _scan_literal(prompt_json, negative=False))
        if not n:
            n = _comfy_negative_prompt(prompt_json)
        if not n and pos is None and neg is None:  # no sampler links at all (video config nodes)
            n = _scan_literal(prompt_json, negative=True)
        # Same text feeding both inputs (e.g. one Flux encoder wired to
        # positive+negative, or an unrelated fallback hit) is not a negative.
        if n and p and n.strip() == p.strip():
            n = None
        out["prompt"] = p
        out["negative"] = n
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
                    if pos is None:  # BasicGuider: single `conditioning` input
                        pos = gin.get("conditioning")
            return pos, neg
    return None, None


# Input keys that carry (or lead to) prompt text, in preference order. Both
# literal strings and links are tried; the first key that yields text wins.
_TEXT_KEYS = (
    "text", "populated_text", "text_positive", "positive_prompt", "prompt",
    "t5xxl", "clip_l", "value", "string", "wildcard_text", "source",
    "conditioning", "positive", "negative", "text_g", "text_l",
)
# Cached display widgets of ShowText/showAnything-style nodes: hold the LAST
# value the node displayed, i.e. the real output of an upstream generator.
_CACHED_KEYS = ("text_0", "text_1", "text2", "text_2")
# Concatenation nodes: (ordered part keys, delimiter key)
_CONCAT_KEYS = (
    (("text_a", "text_b", "text_c", "text_d"), "delimiter"),
    (("string_a", "string_b"), "delimiter"),
)
_GENERATOR_CLASSES = ("TextGenerate", "Florence2Run", "LLM", "Ollama", "Joy",
                      "Qwen2VL", "Qwen3VL", "Caption", "Describe", "VLM")
_GENERATOR_INPUT_HINTS = ("max_length", "max_new_tokens", "max_tokens",
                          "temperature", "sampling_mode")

_MAX_TRACE_DEPTH = 24


def _is_generator(ct: str, inp: dict[str, Any]) -> bool:
    """Node whose text output is produced at run time (LLM/VLM) and thus not
    stored in the graph. Tracing into its inputs would return instructions,
    not the prompt, so it resolves to None."""
    if any(h.lower() in ct.lower() for h in _GENERATOR_CLASSES):
        return True
    return any(k in inp or k.split(".")[0] in inp for k in _GENERATOR_INPUT_HINTS) and (
        "prompt" in inp or "text" in inp)


def _trace_text(graph: dict[str, Any], value: Any, depth: int = 0) -> str | None:
    """Walk a link to the text that ultimately feeds a conditioning input.

    Handles literal strings, CLIPTextEncode `text`, Flux dual encoders
    (`t5xxl`/`clip_l`), string primitives (`value`/`string`/`prompt`),
    pass-through nodes (PreviewAny `source`), boolean routers (ComfySwitchNode /
    Crystools `switch`/`boolean` — literal or linked to a PrimitiveBoolean),
    rgthree Any Switch (`any_NN`), Text/String Concatenate (joined with the
    node's delimiter), ShowText-style cached widgets, the Ideogram-4 builder,
    and nested conditioning. ConditioningZeroOut → empty (zeroed branch).
    Nodes whose output isn't stored in the graph (LLM generators like
    TextGenerate/Florence2Run) resolve to None, so a router falls back to its
    other branch — usually the user's raw prompt."""
    if depth > _MAX_TRACE_DEPTH:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    node = _resolve_link(graph, value)
    if node is None:
        return None
    ct = node.get("class_type", "")
    if ct == "ConditioningZeroOut":
        return None
    inp = node.get("inputs") or {}
    if _is_generator(ct, inp):
        # The generated text isn't in the graph, but a ShowText-style node
        # displaying the same output keeps the last value in a cached widget.
        return _cached_display(graph, value)
    # conditioning pass-throughs with (positive, negative) in AND out
    # (LTXVConditioning, WanImageToVideo, …): output slot picks the side
    if "positive" in inp and "negative" in inp:
        side = "negative" if value[1] == 1 else "positive"
        return _trace_text(graph, inp[side], depth + 1)
    # boolean routers: prefer the active branch (switch literal or linked
    # PrimitiveBoolean), but fall back to the other one if it yields no text
    if "on_true" in inp or "on_false" in inp:
        sw = inp.get("switch", inp.get("boolean"))
        if not isinstance(sw, bool):
            sw_node = _resolve_link(graph, sw)
            sw = (sw_node.get("inputs") or {}).get("value") if sw_node else None
        order = ("on_false", "on_true") if sw is False else ("on_true", "on_false")
        for b in order:
            if b in inp:
                t = _trace_text(graph, inp[b], depth + 1)
                if t:
                    return t
        return None
    # concatenation nodes: join every part that resolves
    for part_keys, delim_key in _CONCAT_KEYS:
        if any(k in inp for k in part_keys):
            parts = [_trace_text(graph, inp[k], depth + 1) for k in part_keys if k in inp]
            parts = [s.strip() for s in parts if s and s.strip()]
            if parts:
                delim = inp.get(delim_key)
                return (delim if isinstance(delim, str) else " ").join(parts)
            return None
    if ct == "Ideogram4PromptBuilderKJ":
        hld = inp.get("high_level_description")
        if isinstance(hld, str) and hld.strip():
            return hld
    # rgthree Any Switch: first connected any_NN that yields text
    lower = {k.lower(): k for k in inp}
    any_keys = sorted(k for k in lower if k.startswith("any_"))
    for key in list(_TEXT_KEYS) + any_keys:
        if key in lower:
            t = _trace_text(graph, inp[lower[key]], depth + 1)
            if t:
                return t
    # cached display value (ShowText etc.) — the upstream link was a generator
    for key in _CACHED_KEYS:
        v = inp.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return None


def _cached_display(graph: dict[str, Any], link: Any) -> str | None:
    """Cached widget text of any node that consumes `link` (ShowText|pysssss
    `text_0`, easy showAnything `text`, …)."""
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        inp = node.get("inputs") or {}
        if not any(v == link for v in inp.values()):
            continue
        for key in _CACHED_KEYS + ("text",):
            v = inp.get(key)
            if isinstance(v, str) and v.strip():
                return v
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


# Literal keys used by the last-resort scan, most explicit first.
_SCAN_POS_KEYS = ("positive_prompt", "text_positive", "positive", "populated_text",
                  "prompt", "t5xxl", "text", "value", "string")
_SCAN_NEG_KEYS = ("negative_prompt", "text_negative", "negative")
# Placeholder values of metadata-saver nodes, never a real prompt.
_JUNK = {"none", "unknown", "null", "n/a", "-", "empty"}


def _scan_literal(graph: dict[str, Any], *, negative: bool) -> str | None:
    """Last resort when no sampler→text link can be traced (e.g. video
    samplers that take a config node): pick the best literal string in the
    graph. Keys are tried in explicitness order; within a key the longest
    plausible text wins. Instruction-like LLM system prompts and (for the
    positive side) negative-looking texts are skipped."""
    keys = _SCAN_NEG_KEYS if negative else _SCAN_POS_KEYS
    for key in keys:
        best: str | None = None
        for node in graph.values():
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type", "")
            inp = node.get("inputs") or {}
            v = inp.get(key)
            if not isinstance(v, str) or not v.strip():
                continue
            if _is_generator(ct, inp) or _looks_instruction(v) or v.strip().lower() in _JUNK:
                continue
            if not negative and _looks_negative(v):
                continue
            if best is None or len(v) > len(best):
                best = v
        if best:
            return best
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


def _looks_instruction(text: str) -> bool:
    """LLM system prompt rather than an image prompt."""
    t = text.strip().lower()
    return t.startswith("you are ") or "your task is" in t


# Keys under which primitive/seed nodes keep their literal value.
_SCALAR_KEYS = ("value", "seed", "noise_seed", "Number", "number", "int", "float", "String", "string")


def _scalar(graph: dict[str, Any], v: Any, depth: int = 0) -> Any:
    """Literal value of a sampler input; follows a link into a primitive node
    (PrimitiveFloat `value`, Seed (rgthree) `seed`, Float `Number`, …)."""
    if not isinstance(v, list):
        return v
    node = _resolve_link(graph, v)
    if node is None or depth > 4:
        return None
    inp = node.get("inputs") or {}
    for k in _SCALAR_KEYS:
        if k in inp:
            return _scalar(graph, inp[k], depth + 1)
    return None


def _fill(out: dict[str, Any], key: str, value: Any) -> None:
    if out[key] is None and value is not None:
        out[key] = value


def _comfy_sampler_fields(graph: dict[str, Any], out: dict[str, Any]) -> None:
    """Sampler settings: first from sampler nodes, then from the helper nodes
    of SamplerCustom* graphs (RandomNoise / BasicScheduler / *Guider)."""
    nodes = [n for n in graph.values() if isinstance(n, dict)]

    def take(node: dict) -> None:
        inp = node.get("inputs") or {}
        s = _scalar(graph, inp.get("sampler_name"))
        _fill(out, "sampler", _str_or_none(s))
        _fill(out, "steps", _int_or_none(_scalar(graph, inp.get("steps"))))
        _fill(out, "cfg", _float_or_none(_scalar(graph, inp.get("cfg"))))
        seed = inp.get("seed", inp.get("noise_seed"))
        _fill(out, "seed", _int_or_none(_scalar(graph, seed)))

    for node in nodes:
        ct = node.get("class_type", "")
        if "Sampler" in ct or ct.startswith("KSampler"):
            take(node)
    for node in nodes:
        ct = node.get("class_type", "")
        if ct in ("RandomNoise", "BasicScheduler") or "Scheduler" in ct or "Guider" in ct:
            take(node)


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


def _lora_name(v: Any) -> str | None:
    """LoRA file name from a literal string or a {content: ...} widget dict.
    Placeholders ('None', '') → None."""
    if isinstance(v, dict):
        v = v.get("content") or v.get("lora") or v.get("name")
    if not isinstance(v, str) or not v.strip() or v.strip().lower() == "none":
        return None
    return v


def _comfy_loras(graph: dict[str, Any]) -> list[tuple[str, float | None]]:
    """LoRAs from every loader flavour: plain LoraLoader*, rgthree Power Lora
    Loader (`lora_N` dicts with on/lora/strength), rgthree Lora Loader Stack
    (`lora_NN` + `strength_NN`), CR LoRA Stack (`lora_name_N` + `switch_N` +
    `model_weight_N`), LoraLoaderStackedAdvanced (`lora_name` dict + `lora_weight`)."""
    out: list[tuple[str, float | None]] = []
    seen: set[str] = set()

    def add(name: Any, strength: Any) -> None:
        n = _lora_name(name)
        if n and n not in seen:
            seen.add(n)
            out.append((n, _float_or_none(strength)))

    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        if "lora" not in ct.lower():
            continue
        inp = node.get("inputs") or {}
        # rgthree Power Lora Loader: lora_1..N = {"on", "lora", "strength"}
        for k, v in inp.items():
            if isinstance(v, dict) and "lora" in v:
                if v.get("on", True):
                    add(v.get("lora"), v.get("strength"))
        # rgthree Lora Loader Stack: lora_01 + strength_01
        for k, v in inp.items():
            m = re.fullmatch(r"lora_(\d+)", k)
            if m and isinstance(v, str):
                add(v, inp.get(f"strength_{m.group(1)}"))
        # CR LoRA Stack: lora_name_1 + switch_1 + model_weight_1
        for k, v in inp.items():
            m = re.fullmatch(r"lora_name_(\d+)", k)
            if m and str(inp.get(f"switch_{m.group(1)}", "On")).lower() != "off":
                add(v, inp.get(f"model_weight_{m.group(1)}"))
        # single loaders
        if "lora_name" in inp or "name" in inp:
            name = inp.get("lora_name", inp.get("name"))
            strength = inp.get("strength_model", inp.get("strength", inp.get("lora_weight")))
            add(name, strength)
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


def _split_caption(prompt: str | None) -> tuple[str | None, str | None]:
    """Split a prompt into (structured_json, readable_text).

    If `prompt` is a structured caption JSON, returns its pretty-printed form
    and a readable flattening. Otherwise returns (None, prompt) unchanged."""
    if not isinstance(prompt, str) or not _looks_like_caption(prompt):
        return None, prompt
    obj = _load_json_object(prompt)
    if not isinstance(obj, dict):
        return None, prompt
    pretty = json.dumps(obj, ensure_ascii=False, indent=2)
    flat = _flatten_obj(obj)
    return pretty, (flat or prompt)


def _flatten_obj(obj: dict) -> str | None:
    """Readable, FTS-friendly text from a parsed caption object."""
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
    return "\n".join(parts) if parts else None


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
