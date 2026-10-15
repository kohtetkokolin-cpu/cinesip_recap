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
import time
import wave

from .. import config
from .quota import QuotaManager

WAV_DIR = os.path.join(config.DATA_DIR, "wav")
os.makedirs(WAV_DIR, exist_ok=True)

# 10 named recap voices. Edge = unlimited free (rate/pitch/volume adjustable).
# Gemini = 30 preset voices, most human-like, shares script quota pool.
VOICE_PRESETS = [
    {"id": "thiha", "provider": "edge", "voice": "my-MM-ThihaNeural",
     "name": "ကိုသီဟ", "gender": "male",
     "desc": "နက်နဲတဲ့အသံ — recap narrator အတွက် အကောင်းဆုံး (အခမဲ့)"},
    {"id": "nilar", "provider": "edge", "voice": "my-MM-NilarNeural",
     "name": "မနီလာ", "gender": "female",
     "desc": "နွေးထွေးတဲ့အသံ — မိန်းကလေး narrator (အခမဲ့)"},
    {"id": "kore", "provider": "gemini", "voice": "Kore",
     "name": "ကိုအောင်", "gender": "male",
     "desc": "လူသံနဲ့အနီးစပ်ဆုံး — နက်နဲတည်ငြိမ်တဲ့ ကျားအသံ"},
    {"id": "charon", "provider": "gemini", "voice": "Charon",
     "name": "ကိုကျော်", "gender": "male",
     "desc": "အားကောင်းတဲ့ ကျားအသံ — action/thriller နဲ့လိုက်"},
    {"id": "fenrir", "provider": "gemini", "voice": "Fenrir",
     "name": "ကိုဖြိုး", "gender": "male",
     "desc": "ငယ်ရွယ်တက်ကြွတဲ့ ကျားအသံ — host စတိုင်နဲ့လိုက်"},
    {"id": "puck", "provider": "gemini", "voice": "Puck",
     "name": "ကိုပိုင်", "gender": "male",
     "desc": "ပေါ့ပေါ့ပါးပါး ကျားအသံ — ဟာသနဲ့လိုက်"},
    {"id": "leda", "provider": "gemini", "voice": "Leda",
     "name": "မသန်း", "gender": "female",
     "desc": "ရှင်းလင်းချိုသာတဲ့ မအသံ — narrator"},
    {"id": "aoede", "provider": "gemini", "voice": "Aoede",
     "name": "မအေး", "gender": "female",
     "desc": "နူးညံ့တဲ့မအသံ — drama/moral စတိုင်နဲ့လိုက်"},
    {"id": "zephyr", "provider": "gemini", "voice": "Zephyr",
     "name": "မဇင်", "gender": "female",
     "desc": "လန်းဆန်းတဲ့မအသံ — horror/comedy နဲ့လိုက်"},
    {"id": "sulafat", "provider": "gemini", "voice": "Sulafat",
     "name": "မဆု", "gender": "female",
     "desc": "တည်ငြိမ်တဲ့မအသံ — documentary စတိုင်နဲ့လိုက်"},
]

DEFAULT_PRESET = "thiha"  # free + most reliable

_PRESET_BY_ID = {p["id"]: p for p in VOICE_PRESETS}
_PRESET_BY_VOICE = {p["voice"]: p for p in VOICE_PRESETS}


def get_voice_presets() -> list:
    return [{"id": p["id"], "provider": p["provider"], "name": p["name"],
             "gender": p["gender"], "desc": p["desc"]} for p in VOICE_PRESETS]


def _preset(pid: str | None) -> dict:
    return _PRESET_BY_ID.get(pid or "") or _PRESET_BY_ID[DEFAULT_PRESET]


def _gender_match(provider: str, gender: str) -> dict:
    for p in VOICE_PRESETS:
        if p["provider"] == provider and p["gender"] == gender:
            return p
    return _PRESET_BY_ID[DEFAULT_PRESET]


def preset_for(role_or_voice: str, narrator_preset: str | None = None) -> dict:
    """Resolve a beat's voice value to a concrete preset.
    Accepts preset ids ('thiha'), raw voice names ('Kore', 'my-MM-ThihaNeural'),
    or legacy roles ('narrator'/'female'/'male')."""
    if not role_or_voice:
        return _preset(narrator_preset)
    if role_or_voice in _PRESET_BY_ID:
        return _PRESET_BY_ID[role_or_voice]
    if role_or_voice in _PRESET_BY_VOICE:
        return _PRESET_BY_VOICE[role_or_voice]
    base = _preset(narrator_preset)
    if role_or_voice in ("female", "male"):
        return _gender_match(base["provider"], role_or_voice)
    return base  # "narrator" or anything unknown → narrator preset


# legacy role → voice mapping, kept for back-compat
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
    models = config.GEMINI_TTS_MODELS or ["gemini-2.5-flash-preview-tts"]
    for model in models:
        # retry w/ backoff on rate-limit/overload — needed now that beats
        # synthesize in parallel threads (thundering herd on free tier)
        for attempt in range(3):
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
                break  # don't retry empty responses
            except Exception as e:
                last_err = e
                msg = str(e)
                if ("429" in msg or "503" in msg or "overloaded" in msg.lower()) and attempt < 2:
                    time.sleep(2 ** attempt + 1)
                    continue
                break
    return {"ok": False, "error": str(last_err)}


# ---------- provider 2: edge-tts ----------
async def _edge_stream(text: str, voice: str, out_mp3: str,
                       rate: str = "+0%", pitch: str = "+0Hz", volume: str = "+0%"):
    """Single network pass: save audio AND collect word boundaries.
    (Previously this took 2 passes — synth + a separate timing stream.)"""
    import edge_tts
    from edge_tts import SubMaker
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume)
    sub = SubMaker()
    with open(out_mp3, "wb") as f:
        async for chunk in comm.stream():
            t = chunk.get("type")
            if t == "audio":
                f.write(chunk["data"])
            elif t == "WordBoundary":
                sub.feed(chunk)
    return sub.cues


def _cues_to_words(cues) -> list:
    return [{"w": c.content, "start": round(c.start.total_seconds(), 2),
             "end": round(c.end.total_seconds(), 2)} for c in cues]


def _edge_params(voice_opts: dict | None) -> tuple[str, str, str]:
    """UI sliders → edge-tts SSML params.
    speed 0.7–1.3 → rate percent; pitch -12..+12 → Hz; volume 50–150 → percent."""
    o = voice_opts or {}
    try:
        speed = float(o.get("speed", 1.0))
    except (TypeError, ValueError):
        speed = 1.0
    try:
        pitch = int(o.get("pitch", 0))
    except (TypeError, ValueError):
        pitch = 0
    try:
        volume = int(o.get("volume", 100))
    except (TypeError, ValueError):
        volume = 100
    speed = min(1.3, max(0.7, speed))
    pitch = min(12, max(-12, pitch))
    volume = min(150, max(50, volume))
    return (f"{(speed - 1) * 100:+.0f}%", f"{pitch:+d}Hz", f"{volume - 100:+d}%")


def synth_edge(text: str, voice: str, out_wav: str,
               voice_opts: dict | None = None) -> dict:
    import asyncio
    rate, pitch, volume = _edge_params(voice_opts)
    mp3 = out_wav + ".mp3"
    # one pass: audio + word timings together (was: 2 separate network streams)
    cues = asyncio.run(_edge_stream(text, voice, mp3, rate, pitch, volume))
    # convert to wav 24k mono
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", mp3,
                    "-ac", "1", "-ar", "24000", out_wav], check=True)
    try:
        os.remove(mp3)
    except OSError:
        pass
    return {"ok": True, "provider": "edge", "wav": out_wav,
            "words": _cues_to_words(cues)}


# NOTE: word timings now come from the single-pass _edge_stream above.
# _edge_word_timings removed (it did a wasteful 2nd network pass).


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
               style_hint: str = "",
               voice_preset: str | None = None,
               voice_opts: dict | None = None) -> dict:
    """Try providers in order. Returns synth dict (ok True/False).

    role: preset id ('thiha'), raw voice name ('Kore'), or legacy role
    ('narrator'/'female'/'male'). voice_preset: the job's narrator preset.
    voice_opts: {'speed': 0.7-1.3, 'pitch': -12..12, 'volume': 50-150}
    (native for edge-tts; speed also applied to Gemini via atempo)."""
    q = quota or QuotaManager()
    preset = preset_for(role, voice_preset)
    prov = preset["provider"]
    # honor an explicit provider preference only when the preset allows it
    order = [prov]
    for p in ("gemini", "edge", "piper"):
        if p not in order:
            order.append(p)
    if prefer == "edge" and "edge" not in order[:1]:
        order = ["edge"] + [p for p in order if p != "edge"]
    errors = []
    for p in order:
        try:
            if p == "gemini":
                gp = preset if preset["provider"] == "gemini" else _gender_match(
                    "gemini", preset["gender"])
                r = synth_gemini(text, gp["voice"], out_wav, q, style_hint)
                if r.get("ok") and voice_opts:
                    r = _apply_speed(r, out_wav, voice_opts)
            elif p == "edge":
                ep = preset if preset["provider"] == "edge" else _gender_match(
                    "edge", preset["gender"])
                r = synth_edge(text, ep["voice"], out_wav, voice_opts)
            else:
                r = synth_piper(text, out_wav)
            if r.get("ok"):
                return r
            errors.append(f"{p}: {r.get('error')}")
        except Exception as e:
            errors.append(f"{p}: {e}")
    return {"ok": False, "error": " | ".join(errors)}


def _apply_speed(r: dict, out_wav: str, voice_opts: dict | None) -> dict:
    """Apply UI speed slider to non-edge audio via atempo (pitch preserved)."""
    try:
        speed = float((voice_opts or {}).get("speed", 1.0))
    except (TypeError, ValueError):
        return r
    speed = min(1.3, max(0.7, speed))
    if abs(speed - 1.0) < 0.01:
        return r
    out = out_wav.replace(".wav", "_spd.wav")
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", out_wav,
                    "-filter:a", f"atempo={speed:.3f}", out], check=True)
    r["wav"] = out
    return r


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
