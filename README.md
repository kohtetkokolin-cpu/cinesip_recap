# Cinesip Recap 🎬

မြန်မာလို movie recap studio — YouTube long-form (၁-၂ နာရီ) + shorts (၁-၁၀ မိနစ်)။
Fair-use editing tools ပါဝင်ပြီး၊ Gemini free quota နဲ့ ကိုက်အောင် ဒီဇိုင်းထုတ်ထားတယ်။

> **Status: Phase 2 complete** — full AI pipeline wired: parallel Whisper analysis →
> Gemini script (bible/outline/beats, hook + commentary rules) → script review gate →
> voice (Gemini TTS → edge-tts → Piper) → fair-use auto-edit render → shorts →
> upload metadata. Premium dark-gold Studio UI (dashboard, wizard, job tracker,
> script review, preview studio).
>
> Fair-use tools reduce Content ID risk (clip caps, transforms, muted source
> audio, commentary checks) — they do NOT guarantee copyright clearance.
> Human review is required before publishing.

## Full pipeline (Phase 2)

1. **Upload** — video in → `POST /api/upload`.
2. **Preview Studio** — WYSIWYG overlays (blur, subtitle, logo, watermark,
   brightness/contrast/saturation, fonts) + 10s test render.
3. **New Recap wizard** — mode (long/short/both), target minutes, narration
   style, voice provider. **Quota pre-check** blocks the job if Gemini calls
   won't fit the remaining free-tier budget (~16 calls for a 2h movie).
4. **Analyze** (local, free) — faster-whisper `tiny` int8, 10-min parallel
   chunks; PySceneDetect cuts; evidence cached by file hash.
5. **Script** (Gemini) — story bible → outline → beats in parallel batches.
   Hook-first cold open, ≥20% commentary rule, open loops. Low-commentary
   beats are flagged.
6. **Review gate** — read/edit every beat in the UI, preview each beat's
   voice, then Approve.
7. **Voice** — Gemini TTS (30 voices) → edge-tts (Nilar/Thiha, unlimited) →
   Piper (offline). Word timings → karaoke captions. Over-long VO is
   rewritten shorter or sped ≤1.15x.
8. **Render** — each beat's video is cut to ≤5s clips (mirror/zoom/reframe/
   speed/grade transforms) and extended/trimmed to EXACTLY the VO duration —
   sync is structural, never drifts. Source audio always muted; BGM ducked
   under VO. Karaoke subtitles + your overlays burned in.
9. **Shorts** — 4 vertical 9:16 cuts from the strongest hooks.
10. **Metadata** — titles, description, tags, hashtags (copy buttons).

## Windows laptop (RAM 4GB) မှာ run နည်း — အလွယ်ဆုံး

1. **`install.bat` ကို double-click** — Python စစ်၊ portable ffmpeg auto-download
   (admin မလို), venv + packages အကုန် install, `.env` ပြင်‌ပေးမယ်။
2. `.env` ဖွင့်ပြီး `GOOGLE_API_KEY=` မှာ AI Studio key (free) ထည့်။
3. **`run.bat` ကို double-click** — browser auto-pwmht http://localhost:8000

ဒါပဲ! Manual install လိုချင်ရင်သာ အောက်ကအဆင့်တွေ လိုက်ပါ။

<details>
<summary>Manual install (advanced)</summary>

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
4. `.env.example` ကို `.env` လို့ ကူးပြီး `GOOGLE_API_KEY=` ဖြည့်
   (AI Studio key, free) — script + Gemini TTS အတွက်။ Key အများအပြားရှိရင်
   `GOOGLE_API_KEYS=k1,k2,k3` လို့ ကော်မာခြားထည့်ပါ (quota auto-rotate)။
5. AI pipeline (recap အပြည့်ထုတ်ရန်):
   ```bat
   pip install -r requirements-ai.txt
   ```
   ပြီးရင် browser မှာ **New Recap** → video ရွေး → mode/target/voice ရွေး →
   Start → script review → Approve → render တက်လာမယ်။
6. Run:
   ```bat
   uvicorn backend.main:app --host 0.0.0.0 --port 8000
   ```
7. Browser မှာ http://localhost:8000 ဖွင့်

</details>

## Project map

```
cinesip-recap/
├── backend/
│   ├── main.py            # FastAPI app + API routes (thin)
│   ├── config.py          # env, paths (4GB-laptop tuned)
│   └── pipeline/
│       ├── overlays.py    # ★ WYSIWYG overlay engine (preview = render)
│       ├── quota.py       # Gemini multi-key rotation + per-job budget
│       ├── analyze.py     # parallel whisper (tiny int8) + scenedetect + cache
│       ├── script.py      # Gemini bible/outline/beats (hook + commentary rules)
│       ├── voice.py       # Gemini TTS → edge-tts → Piper + word timings + fit
│       ├── fairuse.py     # ≤5s clip cap, auto transforms, commentary check
│       ├── render.py      # beat-synced A/V assembly, BGM duck, karaoke ASS
│       ├── shorts.py      # 9:16 shorts auto-cut from top hooks
│       ├── metadata.py    # titles/description/tags/hashtags
│       └── jobs.py        # background job orchestrator + script review gate
├── frontend/
│   └── index.html         # ★ Cinesip Studio SPA — dashboard, wizard, job
│                          #   tracker, script review, preview studio (Burmese)
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
