"""WYSIWYG editor state + frame-accurate render.

The editor stores one `edit.json` per project. The renderer turns that state
into per-segment ffmpeg graphs, encodes every timeline segment to an exact
frame count, concatenates the segments with stream copy, and muxes the
narration track. Nothing is re-encoded twice.

Timeline mapping:
    output segment duration = next.audio_start - this.audio_start
    output frames           = round(cumulative seconds * FPS)

That includes the small TTS gaps, so the concatenated video has exactly the
same length as narration.wav and A/V never drifts.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import config
import db
import faces
import fairuse
import fonts
import fairuse_recipe
import ffmpeg_utils as ff
import ingest
import languages
import scriptgen

FPS = config.PROXY_FPS
CAPTION_LIMIT = 58
DEFAULT_FRAME_COLOR = "#f5c542"
DEFAULT_LOGO = "assets/preset_logo.png"

# language → default font id (fonts.py holds the actual files)
LANGUAGE_FONT = {
    "my": "noto_myanmar",
    "en": "arial",
    "th": "leelawadee",
    "km": "noto_khmer",
    "ms": "arial",
    "zh": "yahei",
    "vi": "arial",
    "hi": "nirmala",
    "id": "arial",
    "ja": "yu_gothic",
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def project_dir(pid: str) -> Path:
    return ingest.project_dir(pid)


def edit_path(pid: str) -> Path:
    return project_dir(pid) / "edit.json"


def render_dir(pid: str) -> Path:
    d = project_dir(pid) / "render"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    return max(low, min(high, v))


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, v))


def _hex(value: Any, default: str) -> str:
    s = str(value or "").strip()
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", s)
    if m:
        return "#" + m.group(1).lower()
    m = re.fullmatch(r"#?([0-9a-fA-F]{3})", s)
    if m:
        return "#" + "".join(ch * 2 for ch in m.group(1).lower())
    return default


def _rgb(color: str) -> tuple[int, int, int]:
    c = _hex(color, "#000000").lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _ff_color(color: str) -> str:
    return "0x" + _hex(color, "#000000").lstrip("#")


def _ass_color(color: str, opacity: float = 1.0) -> str:
    """ASS is &HAABBGGRR; alpha 00 is opaque, FF transparent."""
    r, g, b = _rgb(color)
    alpha = int(round((1.0 - _clamp(opacity, 0.0, 1.0, 1.0)) * 255))
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _deep_merge(base: dict, incoming: Any) -> dict:
    out = json.loads(json.dumps(base))
    if not isinstance(incoming, dict):
        return out
    for key, value in incoming.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        elif key in out:
            out[key] = value
    return out


def _atomic_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# editor state
# --------------------------------------------------------------------------

def default_edit(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    level = int(settings.get("fairuse") or 30)
    if level not in config.FAIRUSE_LEVELS:
        level = 30
    try:
        fx = fairuse.scaled_fx(level)["fx"]
    except Exception:
        fx = {}

    canvas = fx.get("canvas") or {}
    cap = fx.get("cap") or {}
    logo = fx.get("logo") or {}
    frame = fx.get("frame") or {}
    watermark_text = str(settings.get("watermark_text") or "").strip()[:100]

    bg = str(canvas.get("bg") or "blur")
    if bg not in ("blur", "color", "none"):
        bg = "blur"

    return {
        "version": 1,
        "fairuse": level,
        "ratio": settings.get("ratio") if settings.get("ratio") in config.RATIOS else "16:9",
        "resolution": settings.get("resolution") if settings.get("resolution") in config.RESOLUTIONS else "1280:720",
        "canvas": {
            "zoom": _clamp(canvas.get("zoom"), 1.0, 2.0, 1.06),
            "flip": bool(fairuse_recipe.recipe(level).get("flip")),
            "fit": "cover",
            "x": _clamp(canvas.get("x"), 0.0, 1.0, 0.5),
            "y": _clamp(canvas.get("y"), 0.0, 1.0, 0.5),
        },
        "bg": {
            "mode": bg,
            "blur": 18.0,
            "color": _hex(canvas.get("color"), "#000000"),
        },
        "caption": {
            "on": bool(cap.get("on", True)),
            "color": _hex(cap.get("color"), "#ffe600"),
            "box": str(cap.get("bg") or "box") == "box",
            "box_color": _hex(cap.get("bgColor"), "#000000"),
            "box_alpha": _clamp(cap.get("bgAlpha"), 0.0, 1.0, 0.7),
            "size": _clamp(cap.get("size"), 0.02, 0.12, 0.044),
            "y": _clamp(cap.get("y"), 0.05, 0.95, 0.8),
            "font": LANGUAGE_FONT.get(str(settings.get("language") or "my"), "noto_myanmar"),
            "bold": False,
            "outline": 0.28,
            "outline_color": "#000000",
            "shadow": 0.0,
        },
        "blurs": [],
        "logo": {
            "on": True,
            "src": DEFAULT_LOGO,
            "x": _clamp(logo.get("x"), 0.0, 1.0, 0.84),
            "y": _clamp(logo.get("y"), 0.0, 1.0, 0.10),
            "w": _clamp(logo.get("w"), 0.02, 0.5, 0.11),
            "opacity": _clamp(logo.get("op"), 0.0, 1.0, 1.0),
            "shape": "rect",
            "border": True,
            "border_color": "#f5c542",
            "border_width": 0.0,
        },
        "watermark": {
            "on": bool(watermark_text),
            "text": watermark_text,
            "size": 0.028,
            "opacity": 0.75,
            "x": 0.03,
            "y": 0.03,
            "color": "#ffffff",
        },
        "frame": {
            "on": True,
            "style": int(frame.get("style") or 18),
            "color": DEFAULT_FRAME_COLOR,
            "width": 0.006,
        },
        "color": fx.get("color") or {},
    }


def merge_edit(base: dict[str, Any], incoming: Any) -> dict[str, Any]:
    return _deep_merge(base, incoming)


def normalize_edit(raw: Any, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge a partial edit over the preset defaults, then clamp everything."""
    edit = _deep_merge(default_edit(settings), raw)
    edit["version"] = 1
    try:
        edit["fairuse"] = int(edit.get("fairuse") or 30)
    except Exception:
        edit["fairuse"] = 30
    if edit["fairuse"] not in config.FAIRUSE_LEVELS:
        edit["fairuse"] = 30
    if edit.get("ratio") not in config.RATIOS:
        edit["ratio"] = "16:9"
    if edit.get("resolution") not in config.RESOLUTIONS:
        edit["resolution"] = "1280:720"

    canvas = edit["canvas"]
    canvas["zoom"] = round(_clamp(canvas.get("zoom"), 1.0, 2.0, 1.06), 3)
    canvas["flip"] = bool(canvas.get("flip", True))
    canvas["fit"] = "contain" if canvas.get("fit") == "contain" else "cover"
    canvas["x"] = round(_clamp(canvas.get("x"), 0.0, 1.0, 0.5), 4)
    canvas["y"] = round(_clamp(canvas.get("y"), 0.0, 1.0, 0.5), 4)

    bg = edit["bg"]
    if bg.get("mode") not in ("blur", "color", "none"):
        bg["mode"] = "blur"
    bg["blur"] = round(_clamp(bg.get("blur"), 0.0, 60.0, 18.0), 1)
    bg["color"] = _hex(bg.get("color"), "#000000")

    cap = edit["caption"]
    cap["on"] = bool(cap.get("on", True))
    cap["box"] = bool(cap.get("box", True))
    cap["color"] = _hex(cap.get("color"), "#ffe600")
    cap["box_color"] = _hex(cap.get("box_color"), "#000000")
    cap["box_alpha"] = round(_clamp(cap.get("box_alpha"), 0.0, 1.0, 0.7), 3)
    cap["size"] = round(_clamp(cap.get("size"), 0.02, 0.12, 0.044), 4)
    cap["y"] = round(_clamp(cap.get("y"), 0.05, 0.95, 0.8), 4)
    font_id = str(cap.get("font") or "")
    if not fonts.get_font(font_id) or not fonts.regular_path(fonts.get_font(font_id) or {}):
        font_id = LANGUAGE_FONT.get(str((settings or {}).get("language") or "my"), "noto_myanmar")
        if not fonts.get_font(font_id):
            font_id = "noto_myanmar"
    cap["font"] = font_id
    cap["bold"] = bool(cap.get("bold"))
    cap["outline"] = round(_clamp(cap.get("outline"), 0.0, 0.5, 0.28 if cap.get("box") else 0.07), 3)
    cap["outline_color"] = _hex(cap.get("outline_color"), "#000000")
    cap["shadow"] = round(_clamp(cap.get("shadow"), 0.0, 0.4, 0.0), 3)

    raw_blurs = edit.get("blurs")
    blurs: list[dict[str, Any]] = []
    if isinstance(raw_blurs, list):
        for item in raw_blurs[:8]:
            if not isinstance(item, dict):
                continue
            bw = _clamp(item.get("w"), 0.02, 1.0, 0.22)
            bh = _clamp(item.get("h"), 0.02, 1.0, 0.12)
            bx = _clamp(item.get("x"), 0.0, 1.0 - bw, 0.5 - bw / 2)
            by = _clamp(item.get("y"), 0.0, 1.0 - bh, 0.5 - bh / 2)
            blurs.append({
                "on": bool(item.get("on", True)),
                "x": round(bx, 4),
                "y": round(by, 4),
                "w": round(bw, 4),
                "h": round(bh, 4),
                "strength": round(_clamp(item.get("strength"), 1.0, 80.0, 24.0), 1),
            })
    edit["blurs"] = blurs

    logo = edit["logo"]
    logo["on"] = bool(logo.get("on", True))
    logo["src"] = DEFAULT_LOGO
    logo["x"] = round(_clamp(logo.get("x"), 0.0, 1.0, 0.84), 4)
    logo["y"] = round(_clamp(logo.get("y"), 0.0, 1.0, 0.10), 4)
    logo["w"] = round(_clamp(logo.get("w"), 0.02, 0.5, 0.11), 4)
    logo["opacity"] = round(_clamp(logo.get("opacity"), 0.0, 1.0, 1.0), 3)
    logo["shape"] = "circle" if logo.get("shape") == "circle" else "rect"
    logo["border"] = bool(logo.get("border"))
    logo["border_color"] = _hex(logo.get("border_color"), "#f5c542")
    logo["border_width"] = round(_clamp(logo.get("border_width"), 0.0, 0.2, 0.0), 3)
    if logo["border_width"] <= 0:
        logo["border"] = False

    wm = edit["watermark"]
    wm["on"] = bool(wm.get("on", False))
    wm["text"] = str(wm.get("text") or "").strip()[:100]
    wm["size"] = round(_clamp(wm.get("size"), 0.01, 0.08, 0.028), 4)
    wm["opacity"] = round(_clamp(wm.get("opacity"), 0.0, 1.0, 0.75), 3)
    wm["x"] = round(_clamp(wm.get("x"), 0.0, 0.95, 0.03), 4)
    wm["y"] = round(_clamp(wm.get("y"), 0.0, 0.95, 0.03), 4)
    wm["color"] = _hex(wm.get("color"), "#ffffff")
    if not wm["text"]:
        wm["on"] = False

    frame = edit["frame"]
    frame["on"] = bool(frame.get("on", True))
    frame["style"] = _clamp_int(frame.get("style"), 0, 99, 18)
    frame["color"] = _hex(frame.get("color"), DEFAULT_FRAME_COLOR)
    frame["width"] = round(_clamp(frame.get("width"), 0.0, 0.04, 0.006), 4)
    if frame["width"] <= 0:
        frame["on"] = False
    return edit


def load_edit(pid: str) -> dict[str, Any] | None:
    path = edit_path(pid)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def save_edit(pid: str, edit: dict[str, Any]) -> dict[str, Any]:
    path = edit_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(path, edit)
    return edit


def load_or_create_edit(pid: str) -> dict[str, Any]:
    p = db.get_project(pid)
    if not p:
        raise RuntimeError("project not found")
    settings = p.get("settings_json") or {}
    raw = load_edit(pid)
    if raw is None:
        edit = default_edit(settings)
        save_edit(pid, edit)
        return edit
    edit = normalize_edit(raw, settings)
    if edit != raw:
        save_edit(pid, edit)
    return edit


# --------------------------------------------------------------------------
# geometry + timeline
# --------------------------------------------------------------------------

def canvas_size(edit: dict[str, Any]) -> tuple[int, int]:
    try:
        w, h = (int(x) for x in str(edit.get("resolution") or "1280:720").split(":"))
    except Exception:
        w, h = 1280, 720
    long_side, short_side = max(w, h), min(w, h)
    ratio = edit.get("ratio") or "16:9"
    if ratio == "9:16":
        return short_side, long_side
    if ratio == "1:1":
        return short_side, short_side
    return long_side, short_side


def source_size(p: dict[str, Any]) -> tuple[int, int]:
    try:
        w = int(p.get("width") or 0)
        h = int(p.get("height") or 0)
    except Exception:
        w = h = 0
    if w > 0 and h > 0:
        return w, h
    path = p.get("source_path") or p.get("proxy_path") or ""
    if path and Path(path).exists():
        try:
            info = ff.probe(path)
            return int(info["display_width"]), int(info["display_height"])
        except Exception:
            pass
    return 16, 9


def geometry(p: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any]:
    w, h = canvas_size(edit)
    sw, sh = source_size(p)
    src_ar = sw / max(1, sh)
    out_ar = w / max(1, h)
    fit = "contain" if edit["canvas"].get("fit") == "contain" else "cover"

    # cover: fill the canvas and crop the overflow (canvas.x/y chooses the crop).
    # contain: show the whole frame and fill the rest with the blurred backdrop.
    if fit == "contain":
        if src_ar >= out_ar:
            fit_w, fit_h = w, max(2, round(w / max(0.01, src_ar)))
        else:
            fit_h, fit_w = h, max(2, round(h * src_ar))
    else:
        if src_ar >= out_ar:
            fit_h, fit_w = h, max(2, round(h * src_ar))
        else:
            fit_w, fit_h = w, max(2, round(w / max(0.01, src_ar)))

    zoom = float(edit["canvas"].get("zoom") or 1.0)
    fg_w = max(2, int(round(fit_w * zoom / 2)) * 2)
    fg_h = max(2, int(round(fit_h * zoom / 2)) * 2)
    return {
        "width": w,
        "height": h,
        "fps": FPS,
        "ratio": edit.get("ratio") or "16:9",
        "resolution": edit.get("resolution") or "1280:720",
        "source_width": sw,
        "source_height": sh,
        "fit": fit,
        "fit_width": fit_w,
        "fit_height": fit_h,
        "fg_width": fg_w,
        "fg_height": fg_h,
        "zoom": zoom,
    }


def timeline_data(pid: str) -> dict[str, Any] | None:
    p = db.get_project(pid)
    if not p:
        return None
    meta = p.get("meta_json") or {}
    raw = meta.get("timeline_json") or ""
    if not raw or not Path(raw).exists():
        return None
    path = Path(raw)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    segs = data.get("segments") or []
    if not segs:
        return None
    try:
        st = path.stat()
        signature = f"{int(st.st_mtime)}-{st.st_size}"
    except Exception:
        signature = ""
    data["_signature"] = signature
    data["_path"] = str(path)
    return data


def timeline_signature(pid: str) -> str:
    data = timeline_data(pid)
    return str((data or {}).get("_signature") or "")


def build_plan(p: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any]:
    data = timeline_data(p["id"])
    if not data:
        raise RuntimeError("narration timeline is missing — generate the voice first")
    segs = data.get("segments") or []
    total = float(data.get("actual_sec") or 0)
    if total <= 0:
        total = float(segs[-1].get("audio_end") or 0)
    total = max(total, float(segs[-1].get("audio_end") or 0))
    if total <= 0:
        raise RuntimeError("narration timeline has no duration")

    src_dur = float(p.get("duration") or 0)
    if src_dur <= 0:
        try:
            src_dur = float(ff.probe(p.get("source_path") or p.get("proxy_path"))["duration"])
        except Exception:
            src_dur = 1.0

    settings = p.get("settings_json") or {}
    scene_sec = _clamp(settings.get("scene_sec"), 3.0, 15.0, 5.0)
    level = int(edit.get("fairuse") or 30)
    recipe = fairuse_recipe.recipe(level)
    blocks = fairuse_recipe.cut_blocks(src_dur, level)
    source = p.get("source_path") or p.get("proxy_path") or ""
    proxy = p.get("proxy_path") or ""
    face_source = proxy if proxy and Path(proxy).exists() else source
    face_cache = project_dir(p["id"]) / "faces.json"

    plan_segs: list[dict[str, Any]] = []
    total_frames = 0
    last_index = len(segs) - 1
    for i, seg in enumerate(segs):
        start = max(0.0, float(seg.get("audio_start") or 0))
        narr = float(seg.get("duration") or 0)
        if narr <= 0:
            audio_end = float(seg.get("audio_end") or start)
            narr = max(0.3, audio_end - start)
        # Every scene shows at least `scene_sec` of footage at natural speed;
        # a longer narration line extends the scene instead of speeding it up.
        out_dur_want = max(scene_sec, narr + 0.12)
        frames = max(1, int(round(out_dur_want * FPS)))
        out_dur = frames / FPS
        out_start = total_frames / FPS

        try:
            ss = float(seg.get("src_start"))
        except (TypeError, ValueError):
            ss = 0.0
        try:
            se = float(seg.get("src_end"))
        except (TypeError, ValueError):
            se = ss + 1.0
        ss = max(0.0, min(src_dur - 0.05, ss))
        se = max(ss + 0.05, min(src_dur, se))
        if se - ss < 0.05:
            se = min(src_dur, ss + 0.5)
        if se <= ss:
            ss, se = 0.0, min(1.0, src_dur)

        freeze = min(float(recipe.get("freeze") or 0.0), max(0.0, out_dur * 0.35))
        need = max(0.2, out_dur - freeze)

        # Fair use: take `need` seconds of footage at 1x speed, jumping over
        # the removed 5-second blocks. The scene is centred on the scripted
        # window so the shot still matches what the narration describes.
        mid = (ss + se) / 2.0
        spans = fairuse_recipe.scene_spans(mid, need, blocks, src_dur)
        if not spans:
            spans = [(ss, min(src_dur, ss + need))]
        kept = sum(b - a for a, b in spans)
        if kept <= 0.05:
            spans = [(ss, min(src_dur, ss + max(0.5, min(need, 2.0))))]
            kept = max(0.05, spans[0][1] - spans[0][0])
        stretch = need / kept

        focus = [0.5, 0.5]
        if recipe.get("face_zoom") and face_source:
            mid = (spans[0][0] + spans[-1][1]) / 2.0
            focus = faces.focus(face_source, mid, face_cache)
        focus = [
            round(_clamp(focus[0], 0.15, 0.85, 0.5), 4),
            round(_clamp(focus[1], 0.15, 0.85, 0.5), 4),
        ]

        fade_in = min(float(recipe.get("fade_in") or 0.0), out_dur * 0.4)
        fade_out = min(float(recipe.get("fade_out") or 0.0), out_dur * 0.4)
        if recipe.get("transition") and i < last_index:
            fade_out = max(fade_out, min(0.18, out_dur * 0.4))

        plan_segs.append({
            "index": len(plan_segs),
            "source_index": i,
            "id": seg.get("id"),
            "kind": seg.get("kind"),
            "text": seg.get("text") or "",
            "audio_start": round(start, 3),
            "audio_end": round(float(seg.get("audio_end") or start), 3),
            "duration": round(narr, 3),
            "narr_sec": round(narr, 3),
            "src_start": round(spans[0][0], 3),
            "src_end": round(spans[-1][1], 3),
            "spans": [[round(a, 3), round(b, 3)] for a, b in spans],
            "kept_sec": round(kept, 3),
            "stretch": round(stretch, 6),
            "focus": focus,
            "fx": {
                "zoom": round(float(recipe.get("zoom") or 0.0), 4),
                "zoom_out": bool(recipe.get("zoom_out")),
                "flip": bool(recipe.get("flip")),
                "fade_in": round(fade_in, 3),
                "fade_out": round(fade_out, 3),
                "freeze": round(freeze, 3),
                "transition": bool(recipe.get("transition")),
                "colour": dict(recipe.get("colour") or {}),
            },
            "out_start": round(out_start, 4),
            "out_dur": round(out_dur, 4),
            "frames": frames,
        })
        total_frames += frames

    if not plan_segs:
        raise RuntimeError("timeline has no usable segments")
    return {
        "duration": round(total_frames / FPS, 3),
        "frames": total_frames,
        "fps": FPS,
        "fairuse": level,
        "scene_sec": scene_sec,
        "cut_blocks": [[round(a, 3), round(b, 3)] for a, b in blocks],
        "segments": plan_segs,
        "timeline": data,
    }


# --------------------------------------------------------------------------
# narration alignment
# --------------------------------------------------------------------------

def aligned_narration(work: Path, plan: dict[str, Any], narration: Path) -> Path:
    """Re-time narration.wav to the scene timeline.

    Every scene is now at least `scene_sec` long, so the original narration
    (built for the old stretched timeline) has to be sliced line by line and
    padded with silence. Slicing keeps natural speech speed — no atempo.
    """
    import wave

    out = work / "narration_aligned.wav"
    sig_path = work / "narration_aligned.json"
    segs = plan.get("segments") or []
    sig = {
        "narration": str(narration),
        "src": f"{int(narration.stat().st_mtime)}-{narration.stat().st_size}",
        "duration": plan.get("duration"),
        "frames": plan.get("frames"),
        "audio": [[s.get("audio_start"), s.get("audio_end"), s.get("out_start")] for s in segs],
    }
    sig_text = json.dumps(sig, sort_keys=True)
    if out.exists() and sig_path.exists():
        try:
            if sig_path.read_text(encoding="utf-8") == sig_text:
                return out
        except OSError:
            pass

    with wave.open(str(narration), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    frame_bytes = max(1, channels * width)
    out_frames = max(1, int(round(float(plan.get("duration") or 0) * rate)))
    buf = bytearray(out_frames * frame_bytes)
    src_frames = len(raw) // frame_bytes

    for seg in segs:
        try:
            a = max(0.0, float(seg.get("audio_start") or 0))
            b = max(a, float(seg.get("audio_end") or a))
            o = max(0.0, float(seg.get("out_start") or 0))
        except (TypeError, ValueError):
            continue
        src_from = min(src_frames, int(round(a * rate)))
        src_to = min(src_frames, int(round(b * rate)))
        if src_to <= src_from:
            continue
        dst_from = int(round(o * rate))
        count = min(src_to - src_from, out_frames - dst_from)
        if count <= 0:
            continue
        buf[dst_from * frame_bytes:(dst_from + count) * frame_bytes] = \
            raw[src_from * frame_bytes:(src_from + count) * frame_bytes]

    tmp = out.with_suffix(".tmp.wav")
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(bytes(buf))
    os.replace(tmp, out)
    sig_path.write_text(sig_text, encoding="utf-8")
    return out


# --------------------------------------------------------------------------
# fonts + captions
# --------------------------------------------------------------------------

def font_info(language: str, font_id: str | None = None) -> dict[str, Any]:
    """Resolve the caption font for this language / user choice."""
    chosen = font_id or LANGUAGE_FONT.get(str(language or "my"), "noto_myanmar")
    font = fonts.resolve(chosen, language)
    reg = fonts.regular_path(font)
    bold = fonts.bold_path(font)
    return {
        "id": font["id"],
        "family": font["family"],
        "file": reg.name if reg else "",
        "bold_file": bold.name if bold else "",
        "path": str(reg) if reg else "",
        "bold_path": str(bold) if bold else "",
        "css": f'"{font["family"]}", "Padauk", "Noto Sans Myanmar", sans-serif',
    }


def ensure_font(work: Path, language: str, edit: dict[str, Any] | None = None) -> dict[str, Any]:
    """Copy the selected caption font into `work/fonts` for libass/drawtext."""
    cap = (edit or {}).get("caption") or {}
    info = font_info(language, str(cap.get("font") or "") or None)
    font = fonts.resolve(info["id"], language)
    copied = fonts.ensure_in_work(work, font, bold=bool(cap.get("bold")))
    file = str(copied.get("file") or "")
    bold_file = str(copied.get("bold_file") or "")
    return {
        "id": font["id"],
        "family": font["family"],
        "css": f'"{font["family"]}", "Padauk", "Noto Sans Myanmar", sans-serif',
        "file": file,
        "rel": f"fonts/{file}" if file else "",
        "bold_file": bold_file,
        "bold_rel": f"fonts/{bold_file}" if bold_file else (f"fonts/{file}" if file else ""),
        "bold_ready": bool(bold_file),
    }


def _caption_chunks(text: str, limit: int = CAPTION_LIMIT) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    parts = re.split(r"(?<=[၊။.!?])\s*", text)
    out: list[str] = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not current:
            current = part
        elif len(current) + 1 + len(part) <= limit:
            current += " " + part
        else:
            out.append(current)
            current = part
    if current:
        out.append(current)
    final: list[str] = []
    for piece in out:
        while len(piece) > limit * 2:
            final.append(piece[: limit * 2])
            piece = piece[limit * 2:]
        final.append(piece)
    return final


def _max_chars(width: int, font_px: int, language: str) -> int:
    char_w = 1.0 if language in ("zh", "ja") else 0.56
    return max(6, int((width * 0.88) / max(1.0, font_px * char_w)))


def _wrap_caption(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    tokens = text.split(" ") if " " in text else list(text)
    sep = " " if " " in text else ""
    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = token if not current else current + sep + token
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = token
            while len(current) > max_chars:
                lines.append(current[:max_chars])
                current = current[max_chars:]
    if current:
        lines.append(current)
    return "\\N".join(lines) if lines else text


def caption_events(seg: dict[str, Any], language: str, width: int, font_px: int) -> list[dict[str, Any]]:
    duration = max(0.05, float(seg.get("duration") or seg.get("out_dur") or 0))
    caps = _caption_chunks(seg.get("text") or "")
    if not caps:
        return []
    weight = sum(len(c) for c in caps) or 1
    max_chars = _max_chars(width, font_px, language)
    out: list[dict[str, Any]] = []
    pos = 0.0
    for cap in caps:
        share = duration * (len(cap) / weight)
        end = min(duration, pos + max(0.6, share))
        out.append({
            "start": round(pos, 3),
            "end": round(max(end, pos + 0.2), 3),
            "text": _wrap_caption(cap, max_chars),
        })
        pos += share
    return out


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        seconds += 1
        cs = 0
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_text(text: str) -> str:
    # Keep the \N line breaks we insert, but never let source text inject an
    # ASS override block. A real backslash becomes a full-width slash.
    return str(text or "").replace("\\", "／").replace("{", "(").replace("}", ")")


def write_ass(path: Path, edit: dict[str, Any], events: list[dict[str, Any]],
              width: int, height: int, family: str) -> None:
    cap = edit["caption"]
    size_px = max(10, int(round(float(cap["size"]) * height)))
    primary = _ass_color(cap["color"], 1.0)
    box = bool(cap.get("box"))
    border_style = 3 if box else 1
    outline_frac = _clamp(cap.get("outline"), 0.0, 0.5, 0.28 if box else 0.07)
    outline_px = max(0, int(round(size_px * outline_frac)))
    shadow_px = max(0, int(round(size_px * _clamp(cap.get("shadow"), 0.0, 0.4, 0.0))))
    back = _ass_color(cap["box_color"], cap["box_alpha"]) if box else "&H00000000"
    outline_col = _ass_color(cap.get("outline_color") or "#000000", 1.0)
    bold = -1 if cap.get("bold") else 0
    cx = width / 2.0
    cy = float(cap["y"]) * height
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.601\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{family},{size_px},{primary},{primary},{outline_col},{back},{bold},0,0,0,"
        f"100,100,0,0,{border_style},{outline_px},{shadow_px},5,20,20,0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [header]
    for ev in events:
        lines.append(
            "Dialogue: 0,{start},{end},Cap,,0,0,0,,{{\\an5\\pos({x:.1f},{y:.1f})}}{text}\n".format(
                start=_ass_time(ev["start"]),
                end=_ass_time(ev["end"]),
                x=cx,
                y=cy,
                text="\\N".join(_ass_text(part) for part in str(ev["text"]).split("\\N")),
            )
        )
    path.write_text("".join(lines), encoding="utf-8-sig", newline="\n")


# --------------------------------------------------------------------------
# ffmpeg graph
# --------------------------------------------------------------------------

def _logo_chain(edit: dict[str, Any], W: int, H: int) -> list[str]:
    """Scale/mask the logo image into a `[logo]` label (RGBA, sized in px)."""
    logo = edit["logo"]
    lw = max(8, int(round(W * float(logo.get("w") or 0.1))))
    if lw % 2:
        lw += 1
    opacity = _clamp(logo.get("opacity"), 0.0, 1.0, 1.0)
    border_px = 0
    if logo.get("border"):
        border_px = int(round(float(logo.get("border_width") or 0) * min(W, H)))
    color = _ff_color(logo.get("border_color") or "#f5c542")
    circle = logo.get("shape") == "circle"

    lines: list[str] = []
    if circle:
        if 0 < border_px * 2 < lw:
            inner = lw - 2 * border_px
            if inner % 2:
                inner -= 1
            radius = f"(W-1)/2"
            lines.append(
                f"color=c={color}:s={lw}x{lw}:r={FPS},format=rgba,"
                f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                f"a='if(lte(hypot(X-{radius},Y-{radius}),W/2-1),255,0)'[lring]"
            )
            lines.append(
                f"[1:v]scale={inner}:{inner}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={inner}:{inner},format=rgba,"
                f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                f"a='if(lte(hypot(X-(W-1)/2,Y-(H-1)/2),W/2-1),alpha(X,Y),0)'[lcore]"
            )
            lines.append(
                f"[lring][lcore]overlay=x={border_px}:y={border_px}:format=auto[lringed]"
            )
            lines.append(
                f"[lringed]colorchannelmixer=aa={opacity:.3f},setsar=1[logo]"
            )
        else:
            lines.append(
                f"[1:v]scale={lw}:{lw}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={lw}:{lw},format=rgba,"
                f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                f"a='if(lte(hypot(X-(W-1)/2,Y-(H-1)/2),W/2-1),alpha(X,Y),0)',"
                f"colorchannelmixer=aa={opacity:.3f},setsar=1[logo]"
            )
    else:
        box = ""
        if border_px > 0:
            box = f",drawbox=x=0:y=0:w=iw:h=ih:color={color}@1.0:t={border_px}"
        lines.append(
            f"[1:v]scale={lw}:-2:flags=lanczos,format=rgba,"
            f"colorchannelmixer=aa={opacity:.3f}{box},setsar=1[logo]"
        )
    return lines


def _filter_graph(edit: dict[str, Any], geom: dict[str, Any], seg: dict[str, Any], *,
                  stretch: float, font_rel: str, logo_on: bool,
                  ass_name: str | None, wm_name: str | None,
                  wm_font_rel: str | None = None,
                  select_frame: int | None = None) -> str:
    W = int(geom["width"])
    H = int(geom["height"])
    fg_w = int(geom["fg_width"])
    fg_h = int(geom["fg_height"])
    canvas = edit["canvas"]
    cx = float(canvas.get("x", 0.5))
    cy = float(canvas.get("y", 0.5))
    bg = edit["bg"]
    fx = seg.get("fx") or {}
    out_dur = max(0.05, float(seg["out_dur"]))
    frames = max(1, int(seg["frames"]))

    spans = seg.get("spans") or [[seg.get("src_start"), seg.get("src_end")]]
    base = float(spans[0][0])
    lines: list[str] = []

    # 1. Keep only the parts of the scene that are not inside a removed
    #    5-second block; joining them turns the block into a jump cut.
    if len(spans) == 1:
        a, b = spans[0]
        lines.append(
            f"[0:v]trim=start={float(a) - base:.6f}:end={float(b) - base:.6f},"
            f"setpts=PTS-STARTPTS[src]"
        )
    else:
        labels = []
        for i, (a, b) in enumerate(spans):
            lines.append(
                f"[0:v]trim=start={float(a) - base:.6f}:end={float(b) - base:.6f},"
                f"setpts=PTS-STARTPTS[t{i}]"
            )
            labels.append(f"[t{i}]")
        lines.append(
            "".join(labels) + f"concat=n={len(spans)}:v=1:a=0[src]"
        )

    # 2. Fit the kept footage to the segment duration, then hold the first
    #    frame for the freeze part of the recipe.
    lines.append(f"[src]setpts=(PTS-STARTPTS)*{stretch:.6f},fps={FPS}[vb]")
    cur = "vb"
    freeze = max(0.0, float(fx.get("freeze") or 0.0))
    if freeze > 0.02 and out_dur > freeze * 2:
        lines.append(
            f"[{cur}]tpad=start_mode=clone:start_duration={freeze:.3f},"
            f"setpts=PTS-STARTPTS,fps={FPS}[vf]"
        )
        cur = "vf"

    if bg.get("mode") == "blur":
        lines.append(f"[{cur}]split=2[bgs][fgs]")
        sigma = max(0.0, float(bg.get("blur") or 0.0))
        blur = f",gblur=sigma={sigma:.1f}:steps=2" if sigma > 0.2 else ""
        lines.append(
            f"[bgs]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={W}:{H}{blur},setsar=1[base]"
        )
    else:
        color = _ff_color(bg.get("color") or "#000000") if bg.get("mode") == "color" else "0x000000"
        lines.append(f"[{cur}]null[fgs]")
        lines.append(f"color=c={color}:s={W}x{H}:r={FPS},format=yuv420p[base]")

    # 3. Zoom (animated push-in, or freeze → zoom out at 150%).
    flip = "hflip," if fx.get("flip") else ""
    zoom = max(0.0, float(fx.get("zoom") or 0.0))
    focus = seg.get("focus") or [0.5, 0.5]
    fx_x = _clamp(focus[0], 0.0, 1.0, 0.5)
    fx_y = _clamp(focus[1], 0.0, 1.0, 0.5)
    if fx.get("flip"):
        # The frame is mirrored before the zoom, so the face moves with it.
        fx_x = 1.0 - fx_x
    zoom_filter = ""
    if zoom > 0.001:
        if fx.get("zoom_out"):
            rate = f"1+{zoom:.4f}*(1-on/{frames})"
        else:
            rate = f"1+{zoom:.4f}*on/{frames}"
        x_expr = f"max(0,min(iw-iw/zoom,{fx_x:.4f}*iw-iw/zoom/2))"
        y_expr = f"max(0,min(ih-ih/zoom,{fx_y:.4f}*ih-ih/zoom/2))"
        zoom_filter = (
            f"zoompan=z='{rate}':x='{x_expr}':y='{y_expr}':d=1:"
            f"s={fg_w}x{fg_h}:fps={FPS},"
        )
    lines.append(
        f"[fgs]{flip}scale={fg_w}:{fg_h}:flags=lanczos,setsar=1,"
        f"{zoom_filter}setsar=1[fg]"
    )
    lines.append(
        f"[base][fg]overlay=x=(W-w)*{cx:.4f}:y=(H-h)*{cy:.4f}:shortest=1[canvas]"
    )
    cur = "canvas"

    # 3b. Colour grade (the fair-use "colour" technique). Only the footage is
    # graded; our logo and captions stay untouched.
    colour = fx.get("colour") or {}
    sat = _clamp(colour.get("sat"), 0.0, 3.0, 1.0)
    con = _clamp(colour.get("con"), 0.0, 3.0, 1.0)
    gamma = _clamp(colour.get("gamma"), 0.1, 3.0, 1.0)
    hue = _clamp(colour.get("hue"), -180.0, 180.0, 0.0)
    if abs(sat - 1) > 0.001 or abs(con - 1) > 0.001 or abs(gamma - 1) > 0.001:
        lines.append(
            f"[{cur}]eq=saturation={sat:.3f}:contrast={con:.3f}:gamma={gamma:.3f}[graded]"
        )
        cur = "graded"
    if abs(hue) > 0.05:
        lines.append(f"[{cur}]hue=h={hue:.2f}[hued]")
        cur = "hued"

    # 3c. Blur masks: cover source logos / hard-coded subtitles. Applied after
    # the canvas is composed, so the boxes stay in output coordinates.
    for i, box in enumerate(edit.get("blurs") or []):
        if not isinstance(box, dict) or not box.get("on", True):
            continue
        bx = max(0, min(W - 2, int(round(float(box.get("x") or 0) * W))))
        by = max(0, min(H - 2, int(round(float(box.get("y") or 0) * H))))
        bw = max(2, min(W - bx, int(round(float(box.get("w") or 0.2) * W))))
        bh = max(2, min(H - by, int(round(float(box.get("h") or 0.1) * H))))
        sigma = _clamp(box.get("strength"), 1.0, 80.0, 24.0)
        lines.append(f"[{cur}]split=2[mb{i}][mb{i}b]")
        lines.append(f"[mb{i}b]crop={bw}:{bh}:{bx}:{by},gblur=sigma={sigma:.1f}:steps=2[mb{i}g]")
        lines.append(f"[mb{i}][mb{i}g]overlay=x={bx}:y={by}[mb{i}o]")
        cur = f"mb{i}o"

    # 4. Fade in/out (and the fade-through-black transition between scenes).
    fades = []
    fade_in = max(0.0, float(fx.get("fade_in") or 0.0))
    fade_out = max(0.0, float(fx.get("fade_out") or 0.0))
    if fade_in > 0.01:
        fades.append(f"fade=t=in:st=0:d={min(fade_in, out_dur * 0.45):.3f}")
    if fade_out > 0.01:
        start_out = max(0.0, out_dur - fade_out)
        fades.append(f"fade=t=out:st={start_out:.3f}:d={fade_out:.3f}")
    if fades:
        lines.append(f"[{cur}]{','.join(fades)}[faded]")
        cur = "faded"

    if logo_on:
        logo = edit["logo"]
        lines.extend(_logo_chain(edit, W, H))
        lx = max(0, int(round(W * float(logo.get("x") or 0))))
        ly = max(0, int(round(H * float(logo.get("y") or 0))))
        lines.append(f"[{cur}][logo]overlay=x={lx}:y={ly}:eof_action=repeat[withlogo]")
        cur = "withlogo"

    if wm_name:
        wm = edit["watermark"]
        size_px = max(8, int(round(float(wm.get("size") or 0.028) * H)))
        wx = max(0, int(round(W * float(wm.get("x") or 0.03))))
        wy = max(0, int(round(H * float(wm.get("y") or 0.03))))
        wy = min(max(0, H - size_px), wy)
        opacity = _clamp(wm.get("opacity"), 0.0, 1.0, 0.75)
        color = _ff_color(wm.get("color") or "#ffffff")
        lines.append(
            f"[{cur}]drawtext=fontfile={wm_font_rel or font_rel}:textfile={wm_name}:"
            f"fontcolor={color}@{opacity:.3f}:fontsize={size_px}:x={wx}:y={wy}:"
            f"shadowcolor=black@0.55:shadowx=2:shadowy=2[withwm]"
        )
        cur = "withwm"

    if ass_name:
        lines.append(f"[{cur}]subtitles=filename={ass_name}:fontsdir=fonts[withcap]")
        cur = "withcap"

    frame = edit["frame"]
    if frame.get("on") and float(frame.get("width") or 0) > 0:
        width_px = max(1, int(round(float(frame["width"]) * min(W, H))))
        lines.append(
            f"[{cur}]drawbox=x=0:y=0:w=iw:h=ih:color={_ff_color(frame['color'])}@1.0:"
            f"t={width_px}[withframe]"
        )
        cur = "withframe"

    if select_frame is not None:
        lines.append(
            f"[{cur}]tpad=stop_mode=clone:stop_duration=2,"
            f"trim=end_frame={frames},"
            f"select='eq(n,{max(0, int(select_frame))})',"
            f"format=yuv420p[vout]"
        )
    else:
        lines.append(
            f"[{cur}]tpad=stop_mode=clone:stop_duration=2,"
            f"trim=end_frame={frames},format=yuv420p[vout]"
        )
    return ";\n".join(lines) + "\n"


def _logo_path() -> Path | None:
    path = config.ASSETS_DIR / "preset_logo.png"
    return path if path.exists() else None


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def render_segment(pid: str, work: Path, edit: dict[str, Any], p: dict[str, Any],
                   geom: dict[str, Any], plan: dict[str, Any], seg: dict[str, Any],
                   language: str, fnt: dict[str, Any], logo: Path | None) -> Path:
    index = int(seg["index"])
    out = work / "segments" / f"seg_{index:05d}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.mp4")
    tmp.unlink(missing_ok=True)

    family = str(fnt.get("family") or "Arial")
    font_rel = str(fnt.get("rel") or "")
    bold_rel = str(fnt.get("bold_rel") or font_rel)
    wm_font_rel = bold_rel if (edit["caption"].get("bold") and fnt.get("bold_ready")) else font_rel

    spans = seg.get("spans") or [[seg["src_start"], seg["src_end"]]]
    src_start = float(spans[0][0])
    window = max(0.05, float(spans[-1][1]) - src_start)
    stretch = float(seg.get("stretch") or 1.0)

    ass_name = None
    if edit["caption"].get("on"):
        font_px = max(10, int(round(float(edit["caption"]["size"]) * int(geom["height"]))))
        events = caption_events(seg, language, int(geom["width"]), font_px)
        if events:
            ass_name = f"cap_{index:05d}.ass"
            write_ass(work / ass_name, edit, events, int(geom["width"]), int(geom["height"]), family)

    wm_name = None
    if edit["watermark"].get("on") and edit["watermark"].get("text"):
        wm_name = f"wm_{index:05d}.txt"
        _write_text(work / wm_name, str(edit["watermark"]["text"]))

    graph = _filter_graph(
        edit, geom, seg, stretch=stretch, font_rel=font_rel,
        logo_on=bool(logo and edit["logo"].get("on")),
        ass_name=ass_name, wm_name=wm_name, wm_font_rel=wm_font_rel,
    )
    filter_name = f"f_{index:05d}.txt"
    _write_text(work / filter_name, graph)

    source = p.get("source_path") or p.get("proxy_path")
    if not source or not Path(source).exists():
        raise RuntimeError("source video is missing")

    args = [
        "-ss", f"{src_start:.3f}", "-t", f"{window + 0.5:.3f}",
        "-i", str(source),
    ]
    if logo and edit["logo"].get("on"):
        args += ["-i", str(logo)]
    args += [
        "-filter_complex_script", filter_name,
        "-map", "[vout]", "-an",
        "-r", str(FPS), "-frames:v", str(int(seg["frames"])),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
        "-video_track_timescale", "15360",
        f"segments/{tmp.name}",
    ]
    ff.run_ffmpeg(args, cwd=work, label=f"render segment {index + 1}")
    if not tmp.exists() or tmp.stat().st_size < 1024:
        raise RuntimeError(f"segment {index + 1} was not created")
    os.replace(tmp, out)
    return out


# --------------------------------------------------------------------------
# hashing / cache
# --------------------------------------------------------------------------

def _hash(edit: dict[str, Any], plan: dict[str, Any], source: str) -> str:
    payload = {
        "edit": edit,
        "source": str(source),
        "fps": plan.get("fps"),
        "frames": plan.get("frames"),
        "fairuse": plan.get("fairuse"),
        "cut_blocks": plan.get("cut_blocks"),
        "segments": [
            [s["frames"], s["src_start"], s["src_end"], s.get("kept_sec"),
             s.get("focus"), s.get("fx"), s.get("spans")]
            for s in plan.get("segments", [])
        ],
    }
    return hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _timeline_signature(p: dict[str, Any]) -> str:
    timeline = (p.get("meta_json") or {}).get("timeline_json") or ""
    try:
        data = Path(timeline).read_bytes() if timeline else b""
    except OSError:
        data = b""
    return hashlib.sha1(data).hexdigest()


def _input_signature(edit: dict[str, Any], p: dict[str, Any]) -> str:
    """Cheap stand-in for `_hash`: only the inputs the user can change.

    The full render hash also covers the built plan, but rebuilding the plan
    runs face detection, so it must not happen on every project poll. Edit
    changes and a re-recorded timeline both land here.
    """
    payload = {
        "edit": edit,
        "source": str(p.get("source_path") or p.get("proxy_path") or ""),
        "timeline": _timeline_signature(p),
        "scene_sec": (p.get("settings_json") or {}).get("scene_sec"),
    }
    return hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def is_stale(p: dict[str, Any]) -> bool:
    path = p.get("render_path") or ""
    if not path or not Path(path).exists():
        return False
    meta = p.get("render_meta_json") or {}
    edit = load_edit(p["id"])
    if edit is None:
        return False
    try:
        if meta.get("sig"):
            return meta["sig"] != _input_signature(edit, p)
        # Renders from older builds have no signature; fall back to
        # timestamps so the check stays cheap and read-only.
        return edit_path(p["id"]).stat().st_mtime > Path(path).stat().st_mtime + 1.0
    except OSError:
        return False


# --------------------------------------------------------------------------
# render job
# --------------------------------------------------------------------------

def run_render(pid: str) -> None:
    p = db.get_project(pid)
    if not p:
        raise RuntimeError("project not found")
    if p.get("status") != "ready":
        raise RuntimeError("ingest is not ready yet")

    edit = load_or_create_edit(pid)
    plan = build_plan(p, edit)
    geom = geometry(p, edit)
    language = (plan["timeline"].get("language") or (p.get("settings_json") or {}).get("language") or "my")
    work = render_dir(pid)
    fnt = ensure_font(work, language, edit)
    logo = _logo_path()
    source = p.get("source_path") or p.get("proxy_path") or ""
    h = _hash(edit, plan, source)

    db.update_project(
        pid, render_status="rendering", render_progress=1,
        render_message=f"{len(plan['segments'])} ကြောင်း render ပြင်ဆင်နေသည်...",
        render_error="",
    )
    db.append_log(
        pid,
        f"Render စတင် — {geom['width']}x{geom['height']}, {FPS}fps, "
        f"{plan['frames']} frames, {len(plan['segments'])} segments, "
        f"fairuse {plan['fairuse']}%, {len(plan['cut_blocks'])} cut blocks",
    )

    parts_path = work / "parts.json"
    parts: dict[str, Any] = {}
    if parts_path.exists():
        try:
            parts = json.loads(parts_path.read_text(encoding="utf-8"))
        except Exception:
            parts = {}
    if parts.get("hash") != h:
        shutil.rmtree(work / "segments", ignore_errors=True)
        parts = {"hash": h, "segments": {}}
        _atomic_json(parts_path, parts)
    (work / "segments").mkdir(parents=True, exist_ok=True)

    total = len(plan["segments"])
    for seg in plan["segments"]:
        index = int(seg["index"])
        name = f"seg_{index:05d}.mp4"
        existing = parts.get("segments", {}).get(str(index)) or {}
        path = work / "segments" / name
        reuse = (
            existing.get("frames") == seg["frames"]
            and path.exists() and path.stat().st_size > 1024
        )
        if not reuse:
            render_segment(pid, work, edit, p, geom, plan, seg, language, fnt, logo)
            parts.setdefault("segments", {})[str(index)] = {
                "frames": seg["frames"], "file": name,
            }
            _atomic_json(parts_path, parts)
        done = index + 1
        db.update_project(
            pid,
            render_progress=round(2 + done / total * 86, 1),
            render_message=f"Segment {done}/{total} render ပြီး",
        )

    concat_list = work / "concat.txt"
    lines = []
    for seg in plan["segments"]:
        name = f"segments/seg_{int(seg['index']):05d}.mp4"
        if not (work / name).exists():
            raise RuntimeError(f"missing rendered segment: {name}")
        lines.append("file '" + name + "'\n")
    _write_text(concat_list, "".join(lines))

    db.update_project(pid, render_progress=91, render_message="Segment များ ဆက်စပ်နေသည်...")
    concat_mp4 = work / "concat.mp4"
    concat_mp4.unlink(missing_ok=True)
    ff.run_ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy",
         "-an", "-movflags", "+faststart", "concat.mp4"],
        cwd=work, label="render concat",
    )

    narration = (p.get("meta_json") or {}).get("narration_wav") or ""
    if not narration or not Path(narration).exists():
        raise RuntimeError("narration track is missing")
    narration = aligned_narration(work, plan, Path(narration))
    out = work / "output.mp4"
    tmp = work / "output.tmp.mp4"
    tmp.unlink(missing_ok=True)
    db.update_project(pid, render_progress=95, render_message="Narration နဲ့ ပေါင်းနေသည်...")
    total_sec = plan["duration"]
    ff.run_ffmpeg(
        ["-i", "concat.mp4", "-i", str(narration),
         "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
         "-af", "apad", "-t", f"{total_sec:.3f}",
         "-movflags", "+faststart", tmp.name],
        cwd=work, label="render mux",
    )
    if not tmp.exists() or tmp.stat().st_size < 1024:
        raise RuntimeError("rendered video was not created")
    os.replace(tmp, out)

    info = ff.probe(out)
    actual_frames = _video_frames(out)
    meta = {
        "hash": h,
        "sig": _input_signature(edit, p),
        "duration": round(float(info.get("duration") or total_sec), 3),
        "expected_duration": total_sec,
        "frames": plan["frames"],
        "actual_frames": actual_frames,
        "fps": FPS,
        "width": geom["width"],
        "height": geom["height"],
        "ratio": geom["ratio"],
        "resolution": geom["resolution"],
        "size": out.stat().st_size,
        "updated": time.time(),
        "language": language,
        "segments": total,
        "edit_version": edit.get("version"),
    }
    if actual_frames and actual_frames != plan["frames"]:
        db.append_log(
            pid,
            f"⚠ Render frame check: expected {plan['frames']}, got {actual_frames}",
        )
    db.update_project(
        pid,
        render_path=str(out),
        render_meta_json=meta,
        render_status="ready",
        render_progress=100,
        render_message=(
            f"{ff.fmt_duration(info['duration'])} · {plan['frames']} frames · "
            f"{geom['width']}x{geom['height']}"
        ),
        render_error="",
    )
    db.append_log(
        pid,
        f"Render ပြီး — {ff.fmt_duration(info['duration'])}, {plan['frames']} frames, "
        f"{out.stat().st_size / 1024 / 1024:.1f} MB",
    )


def _video_frames(path: Path) -> int:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            value = proc.stdout.strip().splitlines()[0].strip()
            return int(value)
    except Exception:
        pass
    return 0


# --------------------------------------------------------------------------
# exact still preview
# --------------------------------------------------------------------------

def render_preview(pid: str, t: float, width: int = 960) -> Path:
    p = db.get_project(pid)
    if not p:
        raise RuntimeError("project not found")
    edit = load_or_create_edit(pid)
    plan = build_plan(p, edit)
    geom = geometry(p, edit)
    duration = float(plan["duration"])
    t = max(0.0, min(duration - 0.001, float(t)))

    seg = plan["segments"][-1]
    for item in plan["segments"]:
        if item["out_start"] <= t < item["out_start"] + item["out_dur"]:
            seg = item
            break
    local = max(0.0, min(float(seg["out_dur"]) - 0.001, t - float(seg["out_start"])))
    frame_no = max(0, int(round(local * FPS)))

    language = (plan["timeline"].get("language") or (p.get("settings_json") or {}).get("language") or "my")
    work = render_dir(pid)
    fnt = ensure_font(work, language, edit)
    logo = _logo_path()
    font_rel = str(fnt.get("rel") or "")
    family = str(fnt.get("family") or "Arial")
    wm_font_rel = str(fnt.get("bold_rel") or font_rel) if edit["caption"].get("bold") else font_rel

    ass_name = None
    if edit["caption"].get("on"):
        font_px = max(10, int(round(float(edit["caption"]["size"]) * int(geom["height"]))))
        events = caption_events(seg, language, int(geom["width"]), font_px)
        active = next(
            (ev for ev in events if ev["start"] <= local < ev["end"]),
            None,
        )
        if active:
            ass_name = "preview.ass"
            write_ass(
                work / ass_name, edit,
                [{"start": max(0.0, local - 0.05), "end": local + 0.05,
                  "text": active["text"]}],
                int(geom["width"]), int(geom["height"]), family,
            )

    wm_name = None
    if edit["watermark"].get("on") and edit["watermark"].get("text"):
        wm_name = "preview_wm.txt"
        _write_text(work / wm_name, str(edit["watermark"]["text"]))

    graph = _filter_graph(
        edit, geom, seg, stretch=float(seg.get("stretch") or 1.0),
        font_rel=font_rel,
        logo_on=bool(logo and edit["logo"].get("on")),
        ass_name=ass_name, wm_name=wm_name, wm_font_rel=wm_font_rel,
        select_frame=frame_no,
    )
    pw = max(240, min(int(width or 960), int(geom["width"])))
    if pw < int(geom["width"]):
        graph = graph.replace(
            "format=yuv420p[vout]",
            f"scale={pw}:-2,format=yuvj420p[vout]",
        )
    else:
        graph = graph.replace("format=yuv420p[vout]", "format=yuvj420p[vout]")
    _write_text(work / "preview_filter.txt", graph)

    source = p.get("source_path") or p.get("proxy_path")
    if not source or not Path(source).exists():
        raise RuntimeError("source video is missing")
    spans = seg.get("spans") or [[seg["src_start"], seg["src_end"]]]
    src_start = float(spans[0][0])
    window = max(0.05, float(spans[-1][1]) - src_start)
    args = ["-ss", f"{src_start:.3f}", "-t", f"{window + 0.5:.3f}", "-i", str(source)]
    if logo and edit["logo"].get("on"):
        args += ["-i", str(logo)]
    out = work / "preview.jpg"
    out.unlink(missing_ok=True)
    args += [
        "-filter_complex_script", "preview_filter.txt",
        "-map", "[vout]", "-frames:v", "1", "-q:v", "3", "-update", "1",
        out.name,
    ]
    ff.run_ffmpeg(args, cwd=work, label="exact preview")
    if not out.exists() or out.stat().st_size < 500:
        raise RuntimeError("preview frame was not created")
    return out


# --------------------------------------------------------------------------
# API payload
# --------------------------------------------------------------------------

def edit_payload(p: dict[str, Any], edit: dict[str, Any] | None = None) -> dict[str, Any]:
    edit = edit or load_or_create_edit(p["id"])
    geom = geometry(p, edit)
    language = (p.get("settings_json") or {}).get("language") or "my"
    payload: dict[str, Any] = {
        "edit": edit,
        "geometry": geom,
        "font": font_info(language, str((edit.get("caption") or {}).get("font") or "") or None),
        "fonts": fonts.list_fonts(),
        "timeline": {
            "ready": False,
            "duration": 0,
            "frames": 0,
            "segment_count": 0,
            "signature": "",
            "language": language,
            "segments": [],
        },
    }
    try:
        plan = build_plan(p, edit)
    except Exception:
        return payload
    tl = plan["timeline"]
    language = tl.get("language") or language
    payload["font"] = font_info(language, str((edit.get("caption") or {}).get("font") or "") or None)
    payload["timeline"] = {
        "ready": True,
        "duration": plan["duration"],
        "frames": plan["frames"],
        "segment_count": len(plan["segments"]),
        "signature": tl.get("_signature") or "",
        "language": language,
        "scene_sec": plan.get("scene_sec"),
        "segments": plan["segments"],
    }
    return payload
