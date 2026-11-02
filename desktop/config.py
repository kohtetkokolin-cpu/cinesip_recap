"""Cinesip Recap — configuration.

This is a new, standalone Long Movie app. It does not import anything from the
old Story Studio project.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    """Read KEY=VALUE lines from a local .env file.

    Tiny on purpose: no extra dependency, and a variable that is already set in
    the real environment always wins, so `setx` / `set` still overrides the file.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.environ.get("LONGMOVIE_DATA", str(BASE_DIR / "data"))).resolve()
PROJECTS_DIR = DATA_DIR / "projects"
DB_PATH = DATA_DIR / "longmovie.db"

STATIC_DIR = BASE_DIR / "static"
FAIRUSE_DIR = BASE_DIR / "fairuse" / "presets"
PROMPTS_DIR = BASE_DIR / "prompts"
ASSETS_DIR = BASE_DIR / "assets"

APP_PORT = int(os.environ.get("LONGMOVIE_PORT", "7861"))

# 8 MB chunks: small enough to resume on a flaky mobile connection, large
# enough that a 3-hour movie is not tens of thousands of requests.
CHUNK_SIZE = int(os.environ.get("LONGMOVIE_CHUNK_SIZE", str(8 * 1024 * 1024)))
MAX_UPLOAD_BYTES = int(os.environ.get("LONGMOVIE_MAX_UPLOAD_GB", "20")) * 1024**3

PROXY_HEIGHT = int(os.environ.get("LONGMOVIE_PROXY_HEIGHT", "360"))
PROXY_FPS = int(os.environ.get("LONGMOVIE_PROXY_FPS", "30"))
GEMINI_CHUNK_SEC = int(os.environ.get("LONGMOVIE_GEMINI_CHUNK_SEC", "600"))

TARGET_PCTS = (30, 40, 50, 60, 70, 80)
FAIRUSE_LEVELS = (30, 60, 90, 120, 150)

# ---- Gemini (script analysis) ----
GEMINI_API_KEY = (
    os.environ.get("GEMINI_API_KEY")
    or os.environ.get("GOOGLE_API_KEY")
    or ""
).strip()
GEMINI_MODEL = os.environ.get("LONGMOVIE_GEMINI_MODEL", "gemini-3.8-flash").strip()
# Google's newest flash models are occasionally overloaded (503). Try the
# primary first, then these in order, instead of failing the whole script.
GEMINI_FALLBACKS = [
    m.strip()
    for m in os.environ.get(
        "LONGMOVIE_GEMINI_FALLBACKS", "gemini-3.5-flash,gemini-3-flash-preview"
    ).split(",")
    if m.strip()
]
GEMINI_CALL_TIMEOUT_SEC = int(os.environ.get("LONGMOVIE_GEMINI_CALL_TIMEOUT", "150"))
GEMINI_UPLOAD_TIMEOUT_SEC = int(os.environ.get("LONGMOVIE_GEMINI_UPLOAD_TIMEOUT", "900"))
GEMINI_CHUNK_BUDGET_SEC = int(os.environ.get("LONGMOVIE_GEMINI_CHUNK_BUDGET", "900"))
GEMINI_RETRY_ROUNDS = int(os.environ.get("LONGMOVIE_GEMINI_RETRY_ROUNDS", "3"))
GEMINI_RETRY_DELAY_SEC = float(os.environ.get("LONGMOVIE_GEMINI_RETRY_DELAY", "8"))
# The chunk pipeline retries transient Gemini errors forever: try every model,
# wait, then try the whole chain again. This caps the wait between rounds.
GEMINI_RETRY_MAX_WAIT_SEC = float(
    os.environ.get("LONGMOVIE_GEMINI_RETRY_MAX_WAIT", "120")
)
FAKE_GEMINI = os.environ.get("LONGMOVIE_FAKE_GEMINI", "0") == "1"
GEMINI_TIMEOUT_SEC = int(os.environ.get("LONGMOVIE_GEMINI_TIMEOUT", "600"))

# ---- TTS ----
FAKE_TTS = os.environ.get("LONGMOVIE_FAKE_TTS", "0") == "1"
TTS_GAP_SEC = float(os.environ.get("LONGMOVIE_TTS_GAP", "0.12"))
TTS_RATE_MIN = int(os.environ.get("LONGMOVIE_TTS_RATE_MIN", "-25"))
TTS_RATE_MAX = int(os.environ.get("LONGMOVIE_TTS_RATE_MAX", "35"))
TTS_SAMPLE_RATE = 44100

STYLES = {
    "style_1_recap": {
        "label": "Recap + ဇာတ်ကောင်စကား",
        "short": "Narration + အနည်းငယ် [D] စကား",
        "prompt": "style_1_recap.txt",
    },
    "style_2_dub": {
        "label": "Dialogue အကုန် (Dub)",
        "short": "မူရင်းစကားအကုန် မြန်မာလို",
        "prompt": "style_2_dub.txt",
    },
}
RATIOS = ("16:9", "9:16", "1:1")
RESOLUTIONS = ("854:480", "1280:720", "1920:1080")
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def ensure_dirs() -> None:
    for path in (DATA_DIR, PROJECTS_DIR, STATIC_DIR, FAIRUSE_DIR, PROMPTS_DIR, ASSETS_DIR):
        path.mkdir(parents=True, exist_ok=True)
