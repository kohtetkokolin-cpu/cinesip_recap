"""Twenty distinct Burmese narrator profiles.

Edge TTS only ships two Burmese base voices (Nilar / Thiha), so the 20
profiles are rate/pitch variants. Pairs are kept unique; the renderer must
still validate perceptual distance before claiming they sound different.
"""
from __future__ import annotations

PROFILES: list[dict] = [
    # Female — my-MM-NilarNeural
    {"id": "F01", "name": "နီလာ", "gender": "female", "base": "my-MM-NilarNeural", "rate": 1.00, "pitch": 0},
    {"id": "F02", "name": "သဇင်", "gender": "female", "base": "my-MM-NilarNeural", "rate": 1.04, "pitch": 3},
    {"id": "F03", "name": "ခင်လှိုင်", "gender": "female", "base": "my-MM-NilarNeural", "rate": 1.08, "pitch": -3},
    {"id": "F04", "name": "မြင့်မြတ်", "gender": "female", "base": "my-MM-NilarNeural", "rate": 1.12, "pitch": 6},
    {"id": "F05", "name": "စန္ဒာ", "gender": "female", "base": "my-MM-NilarNeural", "rate": 0.96, "pitch": -5},
    {"id": "F06", "name": "ရွှေရည်", "gender": "female", "base": "my-MM-NilarNeural", "rate": 1.16, "pitch": 9},
    {"id": "F07", "name": "အိန္ဒြာ", "gender": "female", "base": "my-MM-NilarNeural", "rate": 0.92, "pitch": 4},
    {"id": "F08", "name": "နန္ဒာ", "gender": "female", "base": "my-MM-NilarNeural", "rate": 1.20, "pitch": -9},
    {"id": "F09", "name": "သီတာ", "gender": "female", "base": "my-MM-NilarNeural", "rate": 1.24, "pitch": 2},
    {"id": "F10", "name": "ဝတ်မှုန်", "gender": "female", "base": "my-MM-NilarNeural", "rate": 0.88, "pitch": -12},
    # Male — my-MM-ThihaNeural
    {"id": "M01", "name": "သီဟ", "gender": "male", "base": "my-MM-ThihaNeural", "rate": 1.00, "pitch": 0},
    {"id": "M02", "name": "ဇော်လင်း", "gender": "male", "base": "my-MM-ThihaNeural", "rate": 1.05, "pitch": 4},
    {"id": "M03", "name": "မင်းခန့်", "gender": "male", "base": "my-MM-ThihaNeural", "rate": 1.10, "pitch": -4},
    {"id": "M04", "name": "အောင်ကျော်", "gender": "male", "base": "my-MM-ThihaNeural", "rate": 1.15, "pitch": 7},
    {"id": "M05", "name": "နေမျိုး", "gender": "male", "base": "my-MM-ThihaNeural", "rate": 0.95, "pitch": -6},
    {"id": "M06", "name": "ရဲထွန်း", "gender": "male", "base": "my-MM-ThihaNeural", "rate": 1.20, "pitch": 10},
    {"id": "M07", "name": "ခန့်ဇော်", "gender": "male", "base": "my-MM-ThihaNeural", "rate": 0.90, "pitch": 5},
    {"id": "M08", "name": "မျိုးဟိန်း", "gender": "male", "base": "my-MM-ThihaNeural", "rate": 1.25, "pitch": -10},
    {"id": "M09", "name": "ဝင်းမောင်", "gender": "male", "base": "my-MM-ThihaNeural", "rate": 1.30, "pitch": 3},
    {"id": "M10", "name": "ထူးအောင်", "gender": "male", "base": "my-MM-ThihaNeural", "rate": 0.86, "pitch": -14},
]

_BY_ID = {p["id"]: p for p in PROFILES}


def get_voice(voice_id: str) -> dict | None:
    return _BY_ID.get(str(voice_id or ""))


def list_voices() -> list[dict]:
    return PROFILES


def duplicates() -> list[tuple[str, str]]:
    seen: dict[tuple, str] = {}
    out: list[tuple[str, str]] = []
    for p in PROFILES:
        key = (p["base"], round(p["rate"], 3), int(p["pitch"]))
        if key in seen:
            out.append((seen[key], p["id"]))
        else:
            seen[key] = p["id"]
    return out
