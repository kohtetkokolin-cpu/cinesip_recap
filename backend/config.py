"""Cinesip Recap — configuration. Tuned for a low-spec Windows laptop (~4GB RAM)."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
OVERLAY_DIR = os.path.join(DATA_DIR, "overlays")
CLIP_DIR = os.path.join(DATA_DIR, "clips")
FONT_DIR = os.path.join(BASE_DIR, "fonts")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

for d in (DATA_DIR, UPLOAD_DIR, OVERLAY_DIR, CLIP_DIR):
    os.makedirs(d, exist_ok=True)
os.makedirs(FONT_DIR, exist_ok=True)

# Gemini (Phase 2: script writing). Free tier: ~50 req/day per key.
# Comma-separated keys rotate automatically.
GOOGLE_API_KEYS = [k.strip() for k in os.environ.get("GOOGLE_API_KEYS", "").split(",") if k.strip()]
_single = os.environ.get("GOOGLE_API_KEY", "").strip()
if _single and _single not in GOOGLE_API_KEYS:
    GOOGLE_API_KEYS.append(_single)

# Gemini text models for script writing (Phase 2). Google retires model names often,
# so the cascade is env-overridable: GEMINI_MODELS="gemini-3.5-flash-lite,gemini-3.7-flash"
# Verified Aug 2026: gemini-1.5-* and gemini-2.0-* are shut down (404). flash-lite
# has the biggest free quota (~500 req/day vs ~20 for others) — keep it first.
GEMINI_TEXT_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_MODELS",
    "gemini-3.5-flash-lite,gemini-3.7-flash,gemini-3.5-flash,gemini-2.5-flash"
).split(",") if m.strip()]

# Gemini TTS models (voice stage). Also env-overridable.
GEMINI_TTS_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_TTS_MODELS", "gemini-2.5-flash-preview-tts"
).split(",") if m.strip()]

# Whisper model for transcription (Phase 2). On 4GB RAM use "tiny" or "base".
# faster-whisper uses CTranslate2 (no PyTorch needed) with int8 quantization.
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "tiny").strip() or "tiny"

# Optional token gate for expensive endpoints
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip()

# ffmpeg / ffprobe binaries (must be on PATH; on Windows install from gyan.dev)
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
