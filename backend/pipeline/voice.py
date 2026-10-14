"""Cinesip Recap — voice stage.

Provider abstraction (priority order):
  1. Gemini TTS (AI Studio) — 30 preset voices, Burmese confirmed, free tier,
     style tags + 2-speaker dialogue. Shares quota pool with script calls.
  2. edge-tts — 2 Burmese voices (Nilar/Thiha), unlimited free. Word timings
     available → karaoke captions.
  3. Piper (offline ONNX) — last resort, robotic. Stub: enabled when installed.

Every synthesis returns {"ok", "provider", "wav", "words": [(w,start,end)]}.
The render stage uses word timings for karaoke captions when available.
"""
import base64
import os
import struct
import subprocess
import wave

from .. import config
from .quota import QuotaManager

WAV_DIR = os.path.join(config.DATA_DIR, "wav")
os.makedirs(WAV_DIR, exist_ok=True)

# role → voice mapping (Gemini preset voices; edge-tts fallback in parens)
VOICES = {
    "narrator": {"gemini": "Kore", "edge": "my-MM-ThihaNeural"},
    "female": {"gemini": "Leda", "edge": "my-MM-NilarNeural"},
    "male": {"gemini": "Charon", "edge": "my-MM-ThihaNeural"},
}
ROLE_FIX = {"narrator": "narrator", "female": "female", "male": "male"}


def _write_wav_pcm16(path: str, pcm: bytes, rate: int = 24000):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def wav_duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / w.getframerate()


# ---------- provider 1: Gemini TTS ----------
def synth_gemini(text: str, voice_name: str, out_wav: str,
                 quota: QuotaManager, style_hint: str = "") -> dict:
    from google import genai
    key = quota.next_key()
    if not key:
        return {"ok": False, "error": "quota exhausted"}
    last_err = None
    for model in ("gemini-2.5-flash-preview-tts", "gemini-2.0-flash-preview-tts"):
        try:
            client = genai.Client(api_key=key)
            contents = (style_hint + "\n" + text) if style_hint else text
            resp = client.models.generate_content(
                model=model, contents=contents,
                config={"response_modalities": ["AUDIO"],
                        "speech_config": {"voice_config": {
                            "prebuilt_voice_config": {"voice_name": voice_name}}}})
            quota.record(key)
            # find inline audio bytes
            for cand in (resp.candidates or []):
                for part in (cand.content.parts or []):
                    blob = getattr(part, "inline_data", None)
                    if blob and blob.data:
                        raw = blob.data if isinstance(blob.data, bytes) else base64.b64decode(blob.data)
                        _write_wav_pcm16(out_wav, raw)
                        return {"ok": True, "provider": "gemini", "wav": out_wav, "words": []}
            last_err = "no audio in response"
        except Exception as e:
            last_err = e
            if "429" in str(e) or "503" in str(e):
                continue
            break
    return {"ok": False, "error": str(last_err)}


# ---------- provider 2: edge-tts ----------
async def _edge_synth(text: str, voice: str, out_mp3: str):
    import edge_tts
    comm = edge_tts.Communicate(text, voice)
    await comm.save(out_mp3)


def synth_edge(text: str, voice: str, out_wav: str) -> dict:
    import asyncio
    mp3 = out_wav + ".mp3"
    asyncio.run(_edge_synth(text, voice, mp3))
    # convert to wav 24k mono
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", mp3,
                    "-ac", "1", "-ar", "24000", out_wav], check=True)
    words = _edge_word_timings(text, voice)
    try:
        os.remove(mp3)
    except OSError:
        pass
    return {"ok": True, "provider": "edge", "wav": out_wav, "words": words}


def _edge_word_timings(text: str, voice: str) -> list:
    """Word-level timings via edge-tts submaker. Best-effort; [] on failure."""
    import asyncio
    try:
        import edge_tts
        from edge_tts import SubMaker
    except Exception:
        return []

    async def _run():
        comm = edge_tts.Communicate(text, voice)
        sub = SubMaker()
        async for chunk in comm.stream():
            if chunk["type"] == "WordBoundary":
                sub.feed(chunk)
        return sub.cues  # [Subtitle(index, start: timedelta, end: timedelta, content)]

    try:
        cues = asyncio.run(_run())
        return [{"w": c.content, "start": round(c.start.total_seconds(), 2),
                 "end": round(c.end.total_seconds(), 2)} for c in cues]
    except Exception:
        return []


# ---------- provider 3: Piper (offline stub) ----------
def synth_piper(text: str, out_wav: str) -> dict:
    import shutil
    if not shutil.which("piper"):
        return {"ok": False, "error": "piper not installed"}
    # voice model must be configured by the user; see fonts/FONTS.md style docs
    model = os.environ.get("PIPER_MY_MODEL", "")
    if not model or not os.path.isfile(model):
        return {"ok": False, "error": "PIPER_MY_MODEL not set"}
    p = subprocess.run(["piper", "--model", model, "--output_file", out_wav],
                       input=text.encode("utf-8"), capture_output=True)
    if p.returncode != 0 or not os.path.isfile(out_wav):
        return {"ok": False, "error": p.stderr.decode()[-300:]}
    return {"ok": True, "provider": "piper", "wav": out_wav, "words": []}


# ---------- unified entry ----------
def synthesize(text: str, role: str, out_wav: str,
               quota: QuotaManager | None = None,
               prefer: str = "gemini",
               style_hint: str = "") -> dict:
    """Try providers in order. Returns synth dict (ok True/False)."""
    role = ROLE_FIX.get(role, "narrator")
    q = quota or QuotaManager()
    order = {"gemini": ["gemini", "edge", "piper"],
             "edge": ["edge", "gemini", "piper"]}.get(prefer, ["gemini", "edge", "piper"])
    errors = []
    for prov in order:
        try:
            if prov == "gemini":
                v = VOICES[role]["gemini"]
                r = synth_gemini(text, v, out_wav, q, style_hint)
            elif prov == "edge":
                r = synth_edge(text, VOICES[role]["edge"], out_wav)
            else:
                r = synth_piper(text, out_wav)
            if r.get("ok"):
                return r
            errors.append(f"{prov}: {r.get('error')}")
        except Exception as e:
            errors.append(f"{prov}: {e}")
    return {"ok": False, "error": " | ".join(errors)}


def fit_beat_wav(wav_path: str, max_sec: float) -> tuple[str, float]:
    """If wav is longer than max_sec, speed it up slightly (≤1.15x).
    Returns (final_wav, duration). Never slows down."""
    dur = wav_duration(wav_path)
    if dur <= max_sec or dur <= 0:
        return wav_path, dur
    rate = min(1.15, dur / max_sec)
    out = wav_path.replace(".wav", "_fit.wav")
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", wav_path,
                    "-filter:a", f"atempo={rate:.3f}", out], check=True)
    return out, wav_duration(out)
