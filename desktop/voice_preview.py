"""Narrator audition: synthesize a short sample so the user can compare voices.

Samples are cached by (voice, rate, pitch, language), so clicking a narrator
twice plays instantly. Edge TTS returns MP3; the short clip is throttled like a
real preview.
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import config
import tts

CACHE_DIR = config.DATA_DIR / "cache" / "voice"

SAMPLES = {
    "my": (
        "ဒီရုပ်ရှင်ထဲမှာ လူတစ်ယောက်ဟာ သူ့အတိတ်ကို ပြန်ရှာဖို့ ထွက်ခွာသွားတယ်။ "
        "ဒါပေမယ့် အမှန်တရားက သူထင်ထားတာထက် ပိုကြောက်စရာကောင်းနေတယ်။"
    ),
    "en": "This is how the narrator will sound in your recap. Listen carefully and pick the voice you like.",
}


def _text(language: str) -> str:
    return SAMPLES.get(str(language or "my"), SAMPLES["en"])


async def _say(text: str, voice: str, rate: int, pitch: int, out: Path) -> None:
    import edge_tts  # lazy: imported only when a preview is requested

    communicate = edge_tts.Communicate(
        text, voice, rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz"
    )
    await communicate.save(str(out))


def sample(settings: dict[str, Any]) -> tuple[Path, str]:
    """Return (file, media type) for this narrator + rate/pitch."""
    settings = settings or {}
    language = str(settings.get("language") or "my")
    narrator = str(settings.get("narrator_id") or "F01")
    rate_off = settings.get("voice_rate") or 0
    pitch_off = settings.get("voice_pitch") or 0
    voice, rate, pitch = tts.voice_for(language, narrator, rate_off, pitch_off)
    text = _text(language)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(
        f"{voice}|{rate}|{pitch}|{language}|{text}".encode("utf-8")
    ).hexdigest()[:20]
    out = CACHE_DIR / f"{key}.mp3"
    if out.exists() and out.stat().st_size > 512:
        return out, "audio/mpeg"

    tmp = out.with_suffix(out.suffix + ".tmp")
    asyncio.run(_say(text, voice, rate, pitch, tmp))
    if not tmp.exists() or tmp.stat().st_size < 512:
        raise RuntimeError("voice preview was not created")
    tmp.replace(out)
    return out, "audio/mpeg"


def info(settings: dict[str, Any]) -> dict[str, Any]:
    settings = settings or {}
    voice, rate, pitch = tts.voice_for(
        str(settings.get("language") or "my"),
        str(settings.get("narrator_id") or "F01"),
        settings.get("voice_rate") or 0,
        settings.get("voice_pitch") or 0,
    )
    return {"voice": voice, "rate": rate, "pitch": pitch}
