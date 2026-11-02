"""Caption / watermark font registry.

Bundled Unicode fonts live in `static/fonts`; Windows fonts are referenced in
place and copied into the render work dir when used. The editor sends a stable
`id`, so the renderer never guesses from a display name.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import config

# id, label, family, regular file, bold file, where, languages
FONTS: list[dict[str, Any]] = [
    {
        "id": "noto_myanmar",
        "label": "Noto Sans Myanmar · Unicode",
        "family": "Noto Sans Myanmar",
        "regular": "NotoSansMyanmar-Regular.ttf",
        "bold": "NotoSansMyanmar-Bold.ttf",
        "where": "static",
        "langs": ["my"],
    },
    {
        "id": "padauk",
        "label": "Padauk · Unicode",
        "family": "Padauk",
        "regular": "Padauk.ttf",
        "bold": "",
        "where": "static",
        "langs": ["my"],
    },
    {
        "id": "myanmar_text",
        "label": "Myanmar Text · Windows",
        "family": "Myanmar Text",
        "regular": "mmrtext.ttf",
        "bold": "mmrtextb.ttf",
        "where": "win",
        "langs": ["my"],
    },
    {
        "id": "noto_khmer",
        "label": "Noto Sans Khmer · Unicode",
        "family": "Noto Sans Khmer",
        "regular": "NotoSansKhmer-Regular.ttf",
        "bold": "",
        "where": "static",
        "langs": ["km"],
    },
    {
        "id": "arial",
        "label": "Arial",
        "family": "Arial",
        "regular": "arial.ttf",
        "bold": "arialbd.ttf",
        "where": "win",
        "langs": ["en", "ms", "vi", "id"],
    },
    {
        "id": "leelawadee",
        "label": "Leelawadee UI · Thai",
        "family": "Leelawadee UI",
        "regular": "LeelawUI.ttf",
        "bold": "LeelaUIb.ttf",
        "where": "win",
        "langs": ["th"],
    },
    {
        "id": "yahei",
        "label": "Microsoft YaHei · Chinese",
        "family": "Microsoft YaHei",
        "regular": "msyh.ttc",
        "bold": "msyhbd.ttc",
        "where": "win",
        "langs": ["zh"],
    },
    {
        "id": "nirmala",
        "label": "Nirmala UI · Hindi",
        "family": "Nirmala UI",
        "regular": "Nirmala.ttf",
        "bold": "NirmalaB.ttf",
        "where": "win",
        "langs": ["hi"],
    },
    {
        "id": "yu_gothic",
        "label": "Yu Gothic · Japanese",
        "family": "Yu Gothic",
        "regular": "YuGothR.ttc",
        "bold": "YuGothB.ttc",
        "where": "win",
        "langs": ["ja"],
    },
]

LANGUAGE_DEFAULT = {
    "my": "noto_myanmar",
    "km": "noto_khmer",
    "th": "leelawadee",
    "zh": "yahei",
    "hi": "nirmala",
    "ja": "yu_gothic",
    "en": "arial",
    "ms": "arial",
    "vi": "arial",
    "id": "arial",
}

_BY_ID = {f["id"]: f for f in FONTS}


def _source(filename: str, where: str) -> Path | None:
    if not filename:
        return None
    if where == "static":
        path = config.STATIC_DIR / "fonts" / filename
    else:
        windir = Path(os.environ.get("WINDIR") or "C:/Windows")
        path = windir / "Fonts" / filename
    return path if path.exists() else None


def regular_path(font: dict[str, Any]) -> Path | None:
    return _source(str(font.get("regular") or ""), str(font.get("where") or "static"))


def bold_path(font: dict[str, Any]) -> Path | None:
    return _source(str(font.get("bold") or ""), str(font.get("where") or "static"))


def get_font(font_id: str) -> dict[str, Any] | None:
    return _BY_ID.get(str(font_id or ""))


def default_id(language: str) -> str:
    return LANGUAGE_DEFAULT.get(str(language or "my"), "noto_myanmar")


def resolve(font_id: str | None, language: str) -> dict[str, Any]:
    """Return the usable font dict; falls back to the language default."""
    candidates = [str(font_id or ""), default_id(language), "arial"]
    for cid in candidates:
        font = _BY_ID.get(cid)
        if font and regular_path(font):
            return font
    # Last resort: any bundled font.
    for font in FONTS:
        if regular_path(font):
            return font
    return FONTS[0]


def list_fonts() -> list[dict[str, Any]]:
    out = []
    for f in FONTS:
        reg = regular_path(f)
        bold = bold_path(f)
        out.append({
            "id": f["id"],
            "label": f["label"],
            "family": f["family"],
            "available": bool(reg),
            "bold": bool(bold),
            "langs": list(f.get("langs") or []),
            "css": f'"{f["family"]}", "Padauk", "Noto Sans Myanmar", sans-serif',
        })
    return out


def ensure_in_work(work: Path, font: dict[str, Any], bold: bool = False) -> dict[str, Any]:
    """Copy the selected font (and bold face) into `work/fonts` for libass."""
    fonts = work / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    reg = regular_path(font)
    bold_file = bold_path(font) if bold else None
    info = {"family": font["family"], "file": "", "bold_file": "", "bold": False}
    if reg:
        dest = fonts / reg.name
        if not dest.exists() or dest.stat().st_size != reg.stat().st_size:
            try:
                import shutil

                shutil.copyfile(reg, dest)
            except OSError:
                pass
        info["file"] = reg.name
    if bold_file:
        dest = fonts / bold_file.name
        if not dest.exists() or dest.stat().st_size != bold_file.stat().st_size:
            try:
                import shutil

                shutil.copyfile(bold_file, dest)
            except OSError:
                pass
        info["bold_file"] = bold_file.name
        info["bold"] = True
    return info
