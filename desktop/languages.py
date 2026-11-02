"""The 10 output languages for Cinesip Recap.

`edge_*` are the native Edge TTS voices used by the TTS stage. Script and
subtitle stages use the same language code. Font files are deliberately not
bundled for every script yet; the render phase will add Noto fonts and verify
glyph coverage before burning subtitles.
"""
from __future__ import annotations

LANGUAGES: list[dict] = [
    {
        "code": "my", "label_mm": "မြန်မာ", "label_en": "Burmese",
        "edge_female": "my-MM-NilarNeural", "edge_male": "my-MM-ThihaNeural",
        "font": "Padauk", "font_file": "Padauk.ttf",
    },
    {
        "code": "en", "label_mm": "အင်္ဂလိပ်", "label_en": "English",
        "edge_female": "en-US-AriaNeural", "edge_male": "en-US-GuyNeural",
        "font": "Noto Sans", "font_file": None,
    },
    {
        "code": "th", "label_mm": "ထိုင်း", "label_en": "Thai",
        "edge_female": "th-TH-PremwadeeNeural", "edge_male": "th-TH-NiwatNeural",
        "font": "Noto Sans Thai", "font_file": None,
    },
    {
        "code": "km", "label_mm": "ခမာ (ကမ္ဘောဒီးယား)", "label_en": "Khmer",
        "edge_female": "km-KH-SreymomNeural", "edge_male": "km-KH-PisethNeural",
        "font": "Noto Sans Khmer", "font_file": None,
    },
    {
        "code": "ms", "label_mm": "မလေး", "label_en": "Malay",
        "edge_female": "ms-MY-YasminNeural", "edge_male": "ms-MY-OsmanNeural",
        "font": "Noto Sans", "font_file": None,
    },
    {
        "code": "zh", "label_mm": "တရုတ်", "label_en": "Chinese",
        "edge_female": "zh-CN-XiaoxiaoNeural", "edge_male": "zh-CN-YunxiNeural",
        "font": "Noto Sans SC", "font_file": None,
    },
    {
        "code": "vi", "label_mm": "ဗီယက်နမ်", "label_en": "Vietnamese",
        "edge_female": "vi-VN-HoaiMyNeural", "edge_male": "vi-VN-NamMinhNeural",
        "font": "Noto Sans", "font_file": None,
    },
    {
        "code": "hi", "label_mm": "ဟိန္ဒီ (India)", "label_en": "Hindi",
        "edge_female": "hi-IN-SwaraNeural", "edge_male": "hi-IN-MadhurNeural",
        "font": "Noto Sans Devanagari", "font_file": None,
    },
    {
        "code": "id", "label_mm": "အင်ဒိုနီးရှား", "label_en": "Indonesian",
        "edge_female": "id-ID-GadisNeural", "edge_male": "id-ID-ArdiNeural",
        "font": "Noto Sans", "font_file": None,
    },
    {
        "code": "ja", "label_mm": "ဂျပန်", "label_en": "Japanese",
        "edge_female": "ja-JP-NanamiNeural", "edge_male": "ja-JP-KeitaNeural",
        "font": "Noto Sans JP", "font_file": None,
    },
]

_BY_CODE = {x["code"]: x for x in LANGUAGES}
SUPPORTED_CODES = tuple(_BY_CODE)


def get_language(code: str) -> dict | None:
    return _BY_CODE.get(str(code or ""))
