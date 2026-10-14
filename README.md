# Cinesip Recap 🎬

မြန်မာလို movie recap studio — YouTube long-form (၁-၂ နာရီ) + shorts (၁-၁၀ မိနစ်)။
Fair-use editing tools ပါဝင်ပြီး၊ Gemini free quota နဲ့ ကိုက်အောင် ဒီဇိုင်းထုတ်ထားတယ်။

> **Status: Phase 1** — project scaffold + WYSIWYG preview editor (upload → blur/logo/
> watermark/subtitle/video adjustments → 10s test render)။ AI pipeline (transcription,
> script, TTS, full render) က Phase 2-4 မှာ လာမယ်။ အသေးစိတ် workflow ကို
> [Recap Studio V2 Workflow] စာမျက်နှာမှာ ကြည့်ပါ။

## Windows laptop (RAM 4GB) မှာ run နည်း

1. **Python 3.11+** သွင်း — https://www.python.org/downloads/
   (install မှာ "Add python.exe to PATH" ကို ✅ ချိတ်ပါ)
2. **ffmpeg** သွင်း — https://www.gyan.dev/ffmpeg/builds/ → `ffmpeg-release-essentials.zip`
   ကို ဖြည် → `bin` folder ကို PATH ထဲ ထည့်။ စစ်ရန်: `ffmpeg -version`
3. ဒီ repo ကို clone/ဒေါင်းလုဒ် → folder ထဲ command prompt ဖွင့်:
   ```bat
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. `.env.example` ကို `.env` လို့ ကူးပြီး `GOOGLE_API_KEY=` ဖြည့် (Phase 2 အတွက်)
5. Run:
   ```bat
   uvicorn backend.main:app --host 0.0.0.0 --port 8000
   ```
6. Browser မှာ http://localhost:8000 ဖွင့်

## Project map

```
cinesip-recap/
├── backend/
│   ├── main.py            # FastAPI app + API routes (thin)
│   ├── config.py          # env, paths (4GB-laptop tuned)
│   └── pipeline/
│       ├── overlays.py    # ★ WYSIWYG overlay engine (preview = render)
│       ├── ingest.py      # Phase 2: upload / yt-dlp
│       ├── analyze.py     # Phase 2: whisper + scenedetect (parallel)
│       ├── script.py      # Phase 2: Gemini writers (quota-budgeted)
│       ├── voice.py       # Phase 2: edge-tts + duration fit
│       ├── fairuse.py     # Phase 2: auto transforms (≤5s clips, mirror/zoom/grade)
│       ├── render.py      # Phase 3: ffmpeg assembly
│       └── shorts.py      # Phase 4: shorts auto-cut
├── frontend/
│   └── index.html         # WYSIWYG preview editor (Burmese UI)
├── fonts/                 # Myanmar TTFs (Padauk recommended, OFL)
├── data/                  # runtime (gitignored): uploads, overlays, clips
├── requirements.txt       # Phase 1 (light)
├── requirements-ai.txt    # Phase 2+ (whisper, scenedetect, google-genai, yt-dlp)
└── Dockerfile
```

## WYSIWYG guarantee

Preview editor နဲ့ render က **တစ်ခုတည်းသော coordinate model** ကို သုံးတယ် —
အားလုံး video dimension ရဲ့ percentage (0-100):
- blur rects (x, y, w, h, strength) — 6 ခုထိ
- logo (x, y, width%, opacity)
- watermark text (x, y, size%, opacity, color)
- subtitle (center cx/cy, size%, max-width%, color)
- video (brightness, contrast, saturation)

`backend/pipeline/overlays.py::build_filter()` က ဒီ percentage တွေကို ffmpeg
`W`/`H` expression အဖြစ် ပြောင်းတယ် — ဒါကြောင့် preview မှာ ချထားတဲ့အတိုင်း
output video မှာ တစ်ထပ်တည်း ကျတယ်။ "Render 10s preview" နဲ့ အမြဲ တိုက်စစ်နိုင်။

## API (Phase 1)

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | preview editor UI |
| POST | `/api/upload` | video upload → video_id, dimensions |
| GET | `/api/video/{id}` | stream video |
| GET | `/api/frame/{id}?t=` | JPEG frame |
| GET/POST | `/api/fonts` | list / upload TTF |
| POST | `/api/logo/{id}` | upload logo image |
| GET/POST | `/api/overlays/{id}` | get / save overlay JSON |
| POST | `/api/render-preview/{id}?t=` | 10s clip with overlays burned |

## License

MIT — free forever. API keys live in `.env` (never committed).
