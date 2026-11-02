# Cinesip Recap

A standalone **Long Movie → Recap** app. It is a new project: it does not import
anything from the old Story Studio application.

- **Input:** 1–3 hour movie (MP4 / MOV / MKV / WEBM / M4V / AVI)
- **Output length:** 30–80% of source duration
- **Script:** Gemini analysis of 360p clips, 10-minute chunks
- **TTS:** Edge TTS, 20 Burmese narrator profiles (10 female, 10 male)
- **Fair use:** intensity presets 30 / 60 / 90 / 120 / 150
- **Languages:** Burmese, English, Thai, Khmer, Malay, Chinese, Vietnamese, Hindi, Indonesian, Japanese

## Status

**M1 — Upload + Ingest.** Implemented:

- chunked, resumable upload (8 MB chunks, 3 parallel workers, automatic retry)
- ffprobe validation on selection and after assembly
- 360p / 30 fps proxy with 2-second keyframes
- 16 kHz mono WAV audio extraction
- 10-minute Gemini chunks (configurable)
- thumbnail extraction
- live status, metadata, chunk list and log in the browser

**M2 — Script + Voice.** Implemented:

- **one chunk at a time, in order**: for each 10-minute chunk the app runs
  Gemini analysis, then Edge TTS for that chunk's lines, then moves to the next
  chunk. The first audio exists as soon as the first chunk is done.
- Gemini reads every 360p chunk with a length-controlled prompt (target %,
  style, language, chunk number) and replies with tagged lines
- `[N] …` narration and `[D mm:ss-mm:ss] …` dialogue lines are parsed, given
  source time windows, merged across chunks and saved as `script.json`
- **Gemini never stops the job**: a failed model falls through the chain
  (`gemini-3.8-flash` → `gemini-3.5-flash` → `gemini-3-flash-preview`), then
  the app waits with exponential backoff (capped by
  `LONGMOVIE_GEMINI_RETRY_MAX_WAIT`) and starts the chain again. Auth/config
  errors still stop, because waiting cannot fix an invalid key.
- Edge TTS retries forever per chunk with backoff, so a flaky network does not
  end the run; the UI keeps the retry message visible.
- **two-tier cache** under `script/chunks/`: `text_NNN.json` holds the Gemini
  result and `voice_NNN.json` holds audio paths, durations and a
  voice/rate/pitch signature. Changing the narrator re-records voice only;
  changing style/language/pct/ratio re-runs Gemini. A re-run resumes from the
  last finished chunk.
- after all chunks: per-line audio is merged into `narration.wav` with one
  bounded `atempo` fit to the target length. **Render is not started here** —
  the user adjusts settings / the editor first and presses Render when ready.
- narration WAV, `timeline.json` (audio time ↔ source time) and `subtitles.srt`
- Script + Voice card with progress, segment list, audio player and SRT download
- regenerating the script clears the old narration, so the voice is never stale

**M3 — Editor + Render.** Implemented:

- `edit.json` per project, seeded from the selected `.zippy` preset: canvas
  ratio/resolution, fit mode (`cover` fills and crops, `contain` letterboxes
  over the blurred backdrop), zoom, flip, background (blur / color / none),
  caption font/bold/outline/shadow/color/box/size/position, logo shape
  (rect/circle), size/opacity/position/border colour and width, text watermark,
  frame colour/width, and up to 8 draggable/resizable blur boxes
- Unicode captions: Noto Sans Myanmar (bundled Regular + Bold), Padauk and Noto
  Sans Khmer ship with the app; Windows Myanmar Text, Arial, Leelawadee UI,
  Microsoft YaHei, Nirmala UI and Yu Gothic are used in place when present.
  `fonts.py` resolves the id, copies the file into the render work dir and
  libass shapes Burmese correctly
- any ratio works for real: 16:9 ↔ 9:16 ↔ 1:1 recomputes the source fit, keeps
  the crop pan (`canvas.x/y`) and the blurred backdrop, both in the DOM preview
  and in the render
- fair-use edit recipe per level (see the table below): 5-second source blocks
  are removed across the whole film, each scene shows `scene_sec` (default 5 s)
  of footage **at natural 1x speed** around the scripted window — a block inside
  the window becomes a jump cut, never slow motion — and the scene gets animated
  zoom, fade, freeze, flip, fade transition, colour grade and (at 150%) a
  face-centred zoom
- narration is re-timed scene by scene: `narration_aligned.wav` slices the
  original per-line audio onto the new scene starts and pads with silence, so
  picture and sound share the same frame-accurate timeline with no `atempo`
- live DOM stage built from the same geometry as the renderer; drag the video,
  caption, logo, watermark and each blur box, drag the corner handles to resize
- autosave (debounced PUT) plus preset reset; every change marks the previous
  render **stale**
- **exact frame**: a still rendered on the server from the exact same
  trim/concat/zoom/freeze/fade/blur/colour filter graph and selected output
  frame, so the preview and the final MP4 match
- frame-accurate render: one libx264 pass per timeline segment with an exact
  `-frames:v` count, stream-copy concat, aligned narration muxed with `apad` and
  an exact `-t`; A/V length equals the planned scene timeline
- resumable renders: per-segment cache keyed by a hash of the edit, source,
  timeline and fair-use spans; an interrupted render resumes the missing
  segments
- output playback, metadata (duration, frames, resolution, size) and download
- narrator audition: a short sample is synthesized for the current narrator,
  speed and pitch offsets, cached under `data/cache/voice`; `voice_rate` and
  `voice_pitch` are applied on top of the profile's base values
- named presets: save the current settings (and editor state when a project is
  open) to the `presets` table, then re-apply it to the form or to any project
- fair-use visibility: the Settings card and the editor print what the selected
  level actually does (5-second cut cadence, estimated removed footage, zoom,
  fades, freeze, flip, transition, face zoom, colour); changing ratio,
  resolution or fairuse in Settings is copied into `edit.json` immediately
- project management: each row in the recent-projects list has `✕ ဖျက်` with a
  confirm dialog; cancelling keeps the project, and deleting is refused while a
  job is running
- **no auto-render**: Script + Voice finishes with the narration merged and
  stops; adjust Settings / the editor and press Render when ready

### Fair-use recipe

| Level | Zoom | Fade | Freeze | Flip | Transition | Face zoom | Colour | 5 s cut cadence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 30 | 3% push-in | 0.20 s in/out | — | — | — | — | — | every 240 s |
| 60 | 6% push-in | 0.25 s in/out | — | — | — | — | sat 1.06 · con 1.02 | every 180 s |
| 90 | 8% push-in | 0.25 s in/out | 0.5 s | — | — | — | sat 1.10 · con 1.04 · gamma 1.02 | every 120 s |
| 120 | 10% push-in | 0.18 s in | 0.6 s | ✓ | ✓ | — | sat 1.16 · con 1.06 · hue 4 | every 90 s |
| 150 | 12% zoom out | 0.18 s in | 0.8 s | ✓ | ✓ | ✓ | sat 1.22 · con 1.08 · hue 8 | every 60 s |

`fairuse_recipe.py` owns the table, `faces.py` finds the largest face with
OpenCV YuNet (one frame per scene, cached in `faces.json`), and the renderer
applies the same recipe to the server preview.

## Requirements

- Windows 10/11
- Python 3.11+
- `ffmpeg` / `ffprobe` on `PATH` (`choco install ffmpeg`)
- a Gemini API key for the script step (`GEMINI_API_KEY` or `GOOGLE_API_KEY`,
  usually set in `.env` — see below);
  Edge TTS and the render's caption fonts need the local machine (Windows fonts)
- `libx264`, `libass` and `freetype` in the installed ffmpeg (the default
  `choco install ffmpeg` build has them)

## Setup

```bat
setup.bat
```

or manually:

```bat
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Then add your Gemini key:

```bat
copy .env.example .env
notepad .env
```

Put the key after `GEMINI_API_KEY=` and restart `run.bat`.

## Run

```bat
run.bat
```

Open <http://127.0.0.1:7861>.

## Layout

```
app.py            FastAPI app, upload protocol, static serving
config.py         paths, limits, target/fairuse constants, style map
db.py             SQLite schema, project helpers, named presets
ingest.py         probe → proxy → audio → chunks → thumbnail
jobs.py           ingest queue + script/TTS queue
gemini_client.py  lazy google-genai upload/analyze wrapper
scriptgen.py      per-chunk pipeline (Gemini → voice), cache, script.json
tts.py            Edge TTS synth per chunk, merge, timeline.json + SRT
voice_preview.py  narrator audition sample (cached)
render.py         edit.json, geometry, ASS captions, segment render, mux
fonts.py          Unicode font registry + per-render font copying
fairuse_recipe.py per-level edit recipe, 5 s cut blocks, scene spans
faces.py          YuNet face-centre detection for the 150% zoom (cached)
ffmpeg_utils.py   ffprobe parsing, ffmpeg progress runner (supports cwd)
fairuse.py        .zippy preset loader, intensity scaling
voices.py         20 narrator profiles
languages.py      10 output languages + Edge voices
fairuse/presets/  fairuse_30/60/90/120/150.json
assets/faces/     face_detection_yunet_2023mar.onnx (bundled model)
prompts/          style_1_recap.txt, style_2_dub.txt
static/           UI (index.html, style.css, app.js, fonts)
static/fonts/     Noto Sans Myanmar (Regular/Bold), Padauk, Noto Sans Khmer
assets/           favicon + preset_logo.png (default logo overlay)
DECISIONS.md      confirmed product decisions
data/             runtime projects + SQLite DB (git-ignored)
```

## Upload protocol

```
POST /api/projects                        create project
POST /api/projects/{id}/upload/init       declare filename + size
PUT  /api/projects/{id}/upload/chunk/{i}  raw bytes, 0-based
POST /api/projects/{id}/upload/complete   verify + assemble + enqueue ingest
```

`upload/init` returns the chunks already stored, so a dropped connection resumes
where it stopped. Chunks are written to `*.part.tmp` and atomically renamed.

## Script + voice protocol

```
POST /api/projects/{id}/script      body {"force": false} → run the chunk pipeline
POST /api/projects/{id}/tts         queue Edge TTS for the saved script only
GET  /api/projects/{id}/script      script + tts status, segments, audio/SRT links
GET  /api/projects/{id}/file/script | timeline | narration | srt
```

The script is stored in `data/projects/<id>/script/script.json`; narration,
`timeline.json` and `subtitles.srt` live in `script/tts/`. Per-chunk caches are
in `script/chunks/` (`text_NNN.json`, `voice_NNN.json`). The `/script` route
runs Gemini → voice for each 10-minute chunk in order and merges the narration.
It does **not** render: the user can change settings or edit layers and then
call `/render` explicitly. `/tts` only re-records the saved script (e.g. after
changing the narrator) and does not re-run Gemini.

```
DELETE /api/projects/{id}           remove a project and all of its files
```

Deleting is refused with 409 while ingest, script, voice or render is running
for that project, so a live job cannot be pulled out from under the worker. The
UI shows a confirm dialog; cancelling leaves the project untouched.

## Editor + render protocol

```
GET  /api/projects/{id}/edit          edit state + geometry + timeline plan
PUT  /api/projects/{id}/edit          merge a partial edit, clamp, save
POST /api/projects/{id}/edit/reset    back to the fairuse preset defaults
PUT  /api/projects/{id}/settings      update project settings (scene_sec, voice…)
GET  /api/voice/preview?narrator_id=  audition sample (language/rate/pitch)
GET  /api/presets                     saved named presets
POST /api/presets                     save settings (+ edit) under a name
POST /api/presets/{id}/apply          apply a preset to a project
DELETE /api/presets/{id}              delete a preset
POST /api/projects/{id}/render        queue a render
GET  /api/projects/{id}/render        render status, output URL, frame check
GET  /api/projects/{id}/preview?t=..  exact still at output time `t`
GET  /api/projects/{id}/file/output   rendered MP4 (Range supported, ?download=1)
```

`render.py` maps the script to output time. Each scene lasts
`max(scene_sec, narration_duration + 0.12)` — default 5 s — and takes exactly
`out_duration − freeze` seconds of source footage at 1x, jumping over the
removed 5 s blocks. Frames are `round(cumulative seconds × 30)` and the segment
count is fixed, so the rendered picture is frame-exact. `aligned_narration()`
slices `narration.wav` onto those scene starts and pads with silence: audio and
video share one timeline, at natural speech speed. Captions are burned as ASS
with the chosen Unicode font (Noto Sans Myanmar by default).

## Environment variables

The easiest place for secrets is a local `.env` file next to `app.py`
(copy `.env.example` to `.env` and paste your Gemini key). It is git-ignored,
and a real environment variable with the same name always takes precedence.

| Variable | Default | Meaning |
| --- | --- | --- |
| `LONGMOVIE_PORT` | `7861` | HTTP port |
| `LONGMOVIE_DATA` | `./data` | database + project storage |
| `LONGMOVIE_CHUNK_SIZE` | `8388608` | upload chunk bytes |
| `LONGMOVIE_MAX_UPLOAD_GB` | `20` | upload size ceiling |
| `LONGMOVIE_PROXY_HEIGHT` | `360` | proxy height |
| `LONGMOVIE_PROXY_FPS` | `30` | proxy framerate |
| `LONGMOVIE_GEMINI_CHUNK_SEC` | `600` | Gemini chunk length |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | — | Gemini key (script step) |
| `LONGMOVIE_GEMINI_MODEL` | `gemini-3.8-flash` | Gemini model |
| `LONGMOVIE_GEMINI_FALLBACKS` | `gemini-3.5-flash,gemini-3-flash-preview` | models tried when the primary is overloaded (503) |
| `LONGMOVIE_GEMINI_CALL_TIMEOUT` | `150` | seconds per analysis request |
| `LONGMOVIE_GEMINI_RETRY_ROUNDS` | `3` | passes over the model chain when `infinite` retry is off |
| `LONGMOVIE_GEMINI_RETRY_MAX_WAIT` | `120` | longest wait (seconds) between retry rounds in the chunk pipeline |
| `LONGMOVIE_GEMINI_TIMEOUT` | `600` | seconds to wait for file activation |
| `LONGMOVIE_FAKE_GEMINI` | `0` | `1` = deterministic fake script (offline tests) |
| `LONGMOVIE_FAKE_TTS` | `0` | `1` = quiet tone instead of Edge TTS |
| `LONGMOVIE_TTS_GAP` | `0.12` | silence inserted between lines |
| `LONGMOVIE_TTS_RATE_MIN` | `-25` | fastest fitting rate (%) |
| `LONGMOVIE_TTS_RATE_MAX` | `35` | slowest fitting rate (%) |

Fake modes are for development only; the UI shows a warning when they are on.

## Troubleshooting

- **"port 7861 is already in use" on startup** — the app is already running in
  another window. Use that window instead of opening `run.bat` twice; a second
  instance would otherwise restart-mark live jobs as interrupted.
- **Script step says "Gemini က ပြန်မဖြေပါ"** — this should no longer happen in
  the chunk pipeline: transient errors cycle the model chain and retry with
  backoff forever. If it appears, the key is invalid or the model is not
  available to that key (a permanent error), so check `.env`. A re-run resumes
  from `script/chunks/` and only redoes unfinished chunks.
- **Key added but the script step is still disabled** — the key is read at
  startup; close the server window and run `run.bat` again.
- **Edge TTS errors** — voice generation needs internet access to Microsoft's
  endpoint; retry the voice step.
