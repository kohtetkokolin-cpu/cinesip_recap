"""Fair-use preset loader.

The five Zippy_Cut `.zippy` files are JSON presets. The baseline `fx` values
are the same in all five files; `fairUse` selects the edit recipe in
`fairuse_recipe.py` (zoom/fade/freeze/flip/transition/face zoom and the 5-second
cut cadence) and scales the static zoom and caption-box alpha from the preset.
"""
from __future__ import annotations

import json
from typing import Any

import config
import fairuse_recipe

LABELS = {
    30: "အနည်းငယ်ပဲ ပြင်မယ်",
    60: "အလယ်အလတ်",
    90: "ပုံမှန်",
    120: "ပိုများမယ်",
    150: "အများဆုံး ပြင်မယ်",
}


def preset_path(level: int) -> str:
    return str(config.FAIRUSE_DIR / f"fairuse_{int(level)}.json")


def load_preset(level: int) -> dict[str, Any]:
    level = int(level)
    if level not in config.FAIRUSE_LEVELS:
        raise ValueError(f"unsupported fairuse level: {level}")
    with open(preset_path(level), "r", encoding="utf-8") as f:
        data = json.load(f)
    data["intensity"] = round(level / 100.0, 3)
    data["label"] = LABELS.get(level, f"{level}%")
    return data


def list_levels() -> list[dict[str, Any]]:
    out = []
    for level in config.FAIRUSE_LEVELS:
        try:
            p = load_preset(level)
        except Exception as exc:  # noqa: BLE001
            out.append({"level": level, "label": LABELS.get(level, str(level)), "error": str(exc)})
            continue
        fx = json.loads(json.dumps(p.get("fx", {})))
        logo = fx.get("logo") or {}
        if isinstance(logo, dict):
            logo.pop("src", None)  # keep the 663 KB base64 out of the config API
        out.append({
            "level": level,
            "label": LABELS.get(level, f"{level}%"),
            "intensity": level / 100.0,
            "techniques": fairuse_recipe.techniques(level),
            "cut_every": fairuse_recipe.recipe(level).get("cut_every"),
            "cut_sec": fairuse_recipe.CUT_SEC,
            "recipe": fairuse_recipe.recipe(level),
            "fx": fx,
            "logo": "assets/preset_logo.png",
        })
    return out


def scaled_fx(level: int) -> dict[str, Any]:
    """Return the preset fx with intensity-scaled visual transforms.

    Only transforms that have a natural 'zero' value are scaled:
      * canvas zoom around 1.0
      * caption box alpha and size
    Frame style, logo position and color preset are structural choices and are
    kept as specified by the preset.
    """
    p = load_preset(level)
    fx = json.loads(json.dumps(p.get("fx", {})))
    k = level / 100.0
    canvas = fx.setdefault("canvas", {})
    try:
        zoom = float(canvas.get("zoom", 1.0))
        canvas["zoom"] = round(1.0 + (zoom - 1.0) * k, 3)
    except Exception:
        pass
    cap = fx.setdefault("cap", {})
    try:
        cap["bgAlpha"] = round(min(0.95, max(0.0, float(cap.get("bgAlpha", 0.7)) * k)), 3)
    except Exception:
        pass
    return {"level": level, "intensity": k, "fx": fx}
