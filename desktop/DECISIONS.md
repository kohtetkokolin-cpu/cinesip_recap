# Cinesip Recap — Confirmed Decisions

Date: 2026-10-30

## Product

- **Separate app** at `D:\Cinesip Recap`, port `7861`. Nothing is imported from
  the old Story Studio project; only the prompts, `.zippy` presets, logo and
  Padauk font were copied in.
- **Input:** 1–3 hour movie. **Output:** 30–80% of source duration
  (e.g. 2 h at 30% = 36 min, at 50% = 60 min). Selectable 30/40/50/60/70/80.
- **Gemini analysis input:** 360p proxy at 30 fps, keyframes every 2 s, sent in
  **10-minute chunks** (`LONGMOVIE_GEMINI_CHUNK_SEC=600`).
- **Fairuse = footage-editing recipe**, not just a filter number. The picker
  screenshots define which techniques each level uses:
  - `30` / `60`: zoom + fade (30 is lighter and cuts less often)
  - `90`: + freeze frame
  - `120`: + flip and a fade transition between scenes
  - `150`: + face-centred zoom and freeze → zoom out
  - All five `.zippy` files share the same `fx` block; only `fairUse` differs,
    so the number selects a recipe (`fairuse_recipe.py`) and the preset supplies
    the visual template.
  - Every level deletes 5-second blocks across the whole source (every 240 / 180
    / 120 / 90 / 60 s). A scene that overlaps a block is cut around it (jump
    cut); if more than 40% would be lost the scene is nudged past the block.
  - The 150% face zoom uses OpenCV YuNet on the 360p proxy, one frame per scene,
    cached in the project's `faces.json`; no face → centre of frame.
- **Watermark:** text, user-entered.
- **Default output** (from the `.zippy` presets):
  - canvas 16:9, zoom 1.2, flip true, blurred background
  - frame style 18
  - caption `#ffe600`, black outline, black box 0.7, size 0.044, y 0.8
  - logo top-right at x 0.84, y 0.1, width 0.11, opacity 1, circle/gold
  - color preset `none`, no extra texts
- **Recap styles:** `style_1_recap` (narration + some dialogue) and
  `style_2_dub` (all dialogue dubbed). Prompts live in `prompts/`.
- **Languages (10):** Burmese, English, Thai, Khmer, Malay, Chinese,
  Vietnamese, Hindi, Indonesian, Japanese.

## Voices

- 20 narrator profiles with Myanmar names: 10 female (F01–F10, Nilar base) and
  10 male (M01–M10, Thiha base).
- The mapping from each profile to a rate/pitch pair is ours; the user only
  picks a narrator. Duplicate pairs are checked at startup.

## Implementation notes (M1)

- Upload is chunked (8 MB) and resumable: `upload/init` returns the chunks
  already on disk; chunks are written as `*.part.tmp` then renamed.
- Ingest is a single background worker: probe → 360p proxy → 16 kHz mono WAV →
  Gemini chunks → thumbnail.
- Chunk cuts are snapped to keyframes, so `-c copy` splitting is exact and
  neighbouring chunks never share footage.
- If a cut cannot be verified within 0.5 s by ffprobe, that chunk is re-encoded
  with an accurate seek instead.
- Jobs interrupted by a server restart are marked `error` and can be retried
  from the UI.

## Implementation notes (M2)

- Script generation is one Gemini call per 360p chunk (`gemini-3.8-flash` by
  default). The chunk file is uploaded, polled until `ACTIVE`, analyzed, then
  deleted.
- The SDK's own retry loop is disabled (`attempts=1`); `gemini_client` instead
  walks a model chain (`gemini-3.8-flash` → `gemini-3.5-flash` →
  `gemini-3-flash-preview`) and retries transient 429/5xx/transport errors. In
  the chunk pipeline it runs in `infinite` mode: after every model fails it
  waits with exponential backoff (capped by `LONGMOVIE_GEMINI_RETRY_MAX_WAIT`)
  and tries the whole chain again, so an overloaded model never stops a run.
  Permanent errors (missing/invalid key, permission denied, 400) still raise.
- Only one server may own port `7861`: `app.py` checks the port before uvicorn
  starts, because uvicorn runs the lifespan (which marks running jobs as
  interrupted) *before* binding. Opening `run.bat` twice used to mark a live
  script job as interrupted.
- The prompt is built from `prompts/style_1_recap.txt` / `style_2_dub.txt` by
  substituting clip length, target length (30–80% of the clip), syllable/word
  budget and the 10-part schedule. Non-Burmese runs replace the language name
  and drop the Burmese-orthography block.
- Gemini answers with tagged lines only: `[N] …` narration and
  `[D mm:ss-mm:ss] …` dialogue, times relative to the chunk. `scriptgen` merges
  chunks, adds each chunk's offset and gives every line a source window.
- Narration lines are placed between the surrounding dialogue lines by estimated
  speaking weight, so every line has a source range for the editor/render.
- TTS uses one Edge voice per run: the narrator's Burmese base voice for
  Burmese, or the language's male/female Edge voice for other languages, with
  the narrator's rate/pitch. Every line is synthesized separately, normalized
  to mono 44.1 kHz, concatenated with a small gap.
- Length fit: if the first pass is off by more than 8%, a second pass adjusts
  the Edge rate inside the configured range; a final `atempo` (0.85–1.5) trims
  the remainder. The timeline is rescaled after the stretch.
- `timeline.json` maps audio time to source time for every line; `subtitles.srt`
  is derived from it. SRT files are written with `\n` line endings.
- Regenerating the script removes the narration/timeline/SRT metadata, so the UI
  cannot pair an old voice with a new script.
- There is no offline fake mode: analysis always calls real Gemini and voice
  always calls real Edge TTS. Tests monkeypatch those two boundaries instead.

## M2.1 — sequential per-chunk pipeline

- **Order matters.** The pipeline walks chunks in order and, for each one, runs
  Gemini analysis and then Edge TTS before touching the next chunk. The user
  sees finished audio for chunk 1 while chunk 2 is still being analyzed, instead
  of waiting for the whole film.
- **Gemini cannot halt the job.** In `infinite` mode a transient failure cycles
  every model, waits (exponential backoff, capped at
  `LONGMOVIE_GEMINI_RETRY_MAX_WAIT`, default 120 s), then cycles again. Uploads
  and activation polls are retried the same way, and the uploaded file is always
  deleted between tries. `on_status` keeps updating the UI with the remaining
  wait, so a long wait never looks frozen.
- **Edge TTS cannot halt the job.** `tts.synth_chunk()` calls
  `_synth_batch(..., max_attempts=None)`, so per-line synthesis retries with
  backoff until it succeeds. The chunk's voice message stays alive with the
  retry count.
- **Two caches per chunk**, under `script/chunks/`:
  - `text_NNN.json`: Gemini segments plus a signature of the chunk file
    (size/mtime), source offset/length, style, language, target % and ratio.
  - `voice_NNN.json`: audio paths + measured durations plus a signature of the
    text signature, voice, rate and pitch.
  Changing the narrator (voice/rate/pitch) re-records audio only; changing
  style/language/pct/ratio re-runs Gemini. Both caches are checked with file
  existence, so a deleted audio file is re-synthesized.
- **Merge once, at the end.** When every chunk is done, all per-line audio is
  passed to `tts.publish_narration(..., raw_paths=…)`, which writes
  `narration.wav`, `timeline.json` and `subtitles.srt` exactly like the manual
  voice pass. One whole-track `atempo` (clamped 0.85–1.5) still does the final
  target-length fit, so per-chunk audio is never needlessly re-synthesized.
- **No auto-render.** `jobs._run_script(pid, "analyze")` stops after the merge.
  The user reviews the script, adjusts Settings and the editor, and presses
  Render explicitly, so a render never starts from a half-configured project.
  The manual `/tts` route still only re-records the saved script.
- **Restart safety.** `db.mark_interrupted()` marks running rows as `error`, so
  a restart is visible; re-running the pipeline resumes from `script/chunks/`
  and only redoes unfinished chunks. The `script_status` is set to `ready` at
  the end, and the TTS columns report the merged track.

## Implementation notes (M3)

- `edit.json` is the single source of truth for the output. It is seeded from
  the selected `.zippy` preset, then merged and clamped on every write. The
  stage, the exact still and the final MP4 all read the same geometry.
- Geometry: the source is fit to the canvas with "cover" rules
  (`fit_w/fit_h`), then multiplied by `zoom`. `canvas.x/y` (0–1) place the
  overlay; the background layer is the same source scaled to cover, cropped and
  blurred, a solid color, or black.
- Timeline mapping: output segment duration is the gap between consecutive
  `audio_start` values (the last segment runs to the narration end), so TTS gaps
  are preserved. Frames are `round(cumulative_seconds × 30)`; the sum is the
  exact output frame count.
- Every segment is encoded separately with libx264 (veryfast, CRF 20) and an
  exact `-frames:v`; segments are concatenated with `-c copy`. The narration is
  muxed with `apad` and `-t total` so video and audio end together. There is one
  encode pass, so frames never drift.
- The render uses the **original source** for quality; the editor uses the 360p
  proxy for fast seeking. The exact still is rendered from the original source.
- Captions are burned as ASS (`PlayResX/Y`, `\an5\pos`, BorderStyle 3 for the
  box). Burmese uses bundled Padauk, Khmer uses bundled Noto Sans Khmer, other
  languages use the matching Windows font; the file is copied into
  `render/fonts` and loaded with `fontsdir`, avoiding Windows path escaping.
- Filter graphs are written to per-segment script files and ffmpeg runs with
  `cwd=render/`, so all paths (`fonts/…`, `cap_*.ass`, `segments/…`) are
  relative and drive letters never appear inside a filter.
- Frame style 18 is a solid border: `drawbox` with `t = width × short_side`,
  default gold `#f5c542` at 0.006.
- Logo defaults to `assets/preset_logo.png` at x 0.84 / y 0.10 / w 0.11. The
  watermark is server-side `drawtext` from a text file (no escaping issues).
- Render cache: `render/parts.json` plus `render/segments/`. The hash covers the
  edit, source path, FPS, planned frames, fair-use level, cut blocks, every
  source window/span, face focus and effect settings; a changed edit clears the
  segments. Interrupted renders keep finished segments and can be restarted
  from the UI.
- The exact still runs the same segment graph (trim/concat of kept spans,
  stretch, freeze, animated zoom, fade, overlays, captions) and selects the
  requested output frame with `select=eq(n,…)`, so it cannot drift from the
  rendered MP4.
- Each segment ends with `tpad=stop_mode=clone:stop_duration=2` plus
  `trim=end_frame=N`: trimming to frame boundaries can lose 1–2 frames at a
  24 fps source; the clone pad fills them so every segment and the final output
  have exactly the planned frame count.
- A render is marked **stale** when the edit hash no longer matches the saved
  output; the UI shows a warning and the button switches to "render again".
- Output ratio/resolution come from Settings (`16:9 | 9:16 | 1:1`,
  `854:480 | 1280:720 | 1920:1080`) and are copied into `edit.json`; the stage
  and renderer support all three ratios.

## M3.1 — narrator, blur, ratio, fonts, logo, presets, render speed

- **Render speed fix.** Scene length is now driven by the picture, not by the
  narration track: `out_dur = max(scene_sec (5 s), narration + 0.12)`. The
  renderer then takes `out_dur − freeze` seconds of footage and plays it at
  1.0x. Previously the whole narration-fitted window was squeezed into the
  scene, which is what made the output run ~2x. `fairuse_recipe.scene_spans()`
  walks forward from the scene midpoint, skips removed blocks, and jumps back
  only when it reaches the end of the film, so `kept == out_dur − freeze`
  exactly (verified to 0.0007 s over the 50-scene test project).
- **Narration re-timing.** Because scenes no longer follow narration time,
  `render.aligned_narration()` slices `narration.wav` per line
  (`audio_start → audio_end`) and writes each slice at its scene's
  `out_start`, zero-padding the rest. No `atempo`, so the voice keeps its
  natural pitch and speed; the track length equals `plan.duration` exactly.
  The result is cached with a signature of the timeline + scene starts.
- **Colour grade.** The fair-use presets gain a colour technique (sat/contrast/
  gamma/hue, increasing with level). It is applied to the footage before the
  logo/captions are composited, so the overlays keep their exact colours.
- **Blur masks.** `edit.blurs` holds up to 8 boxes in output-normalised
  coordinates. The renderer does `split → crop → gblur → overlay`, i.e. a
  feathered-free hard box over the real pixels, and the stage draws the same
  boxes with a CSS backdrop blur. Boxes are moved/resized by the same pointer
  drag code as the logo.
- **Ratio reframing.** `canvas.fit` adds `cover` (crop to fill, `canvas.x/y`
  pans) and `contain` (show the whole frame over the blurred backdrop).
  `geometry()` computes `fit_width/height` from the *current* ratio, so
  switching 16:9 ↔ 9:16 ↔ 1:1 recomputes the crop instead of stretching.
  The editor exposes ratio + resolution + fit and saves immediately.
- **Unicode fonts.** `fonts.py` is the single registry: stable ids, family,
  Regular/Bold files, and a per-language default (my → Noto Sans Myanmar).
  Static Noto Sans Myanmar Regular/Bold were instanced from the variable font
  with `tools/make_static_fonts.py`; libass shapes Burmese correctly. The
  editor lists every font whose file exists, so a machine without (say)
  Yu Gothic simply does not offer it.
- **Logo shape + border.** `circle` masks the logo with a `geq` alpha disc
  (square centre crop first), and the border is a `geq` ring underneath, so
  the ring is a true circle rather than a box. `rect` keeps the drawbox border
  inside the image edge. `border_width` is a fraction of the short side.
- **Saved presets.** A `presets` table stores name + settings + edit snapshot.
  Apply writes both back to a project (or just the settings form when no
  project is open), so ratio/fairuse/font/blur settings travel together.
- **Narrator audition.** `GET /api/voice/preview` synthesizes one sentence with
  the selected narrator plus `voice_rate`/`voice_pitch` offsets and caches the
  MP3 under `data/cache/voice`. The offsets are added on top of the profile's
  base rate/pitch in `tts.voice_for()` and are reused for the real narration.

## M3.2 — project management + fair-use visibility

- **Delete project.** The recent-projects list has a per-row `✕ ဖျက်` button.
  It asks for confirmation in plain language and reminds the user that
  cancelling keeps the project. The existing `DELETE /api/projects/{id}` now
  refuses (409) while ingest/script/voice/render is running, so the worker
  never loses files mid-job; otherwise it removes the project directory and the
  SQLite row. Deleting the project that is currently open reloads the UI.
- **Fair-use explanation.** `/api/config` now returns `recipe`, `cut_sec` and
  `cut_every` for every level. The Settings card prints what the selected level
  does right under the select: how often a 5-second block is removed, the
  estimated removed seconds/percentage for the current source, zoom %, fades,
  freeze, flip, transition, face zoom and colour adjustments. The editor shows
  the same summary next to the fairuse preset label, so the applied recipe is
  visible before rendering.
- **Settings reach the render.** `PUT /settings` copies `ratio`, `resolution`
  and `fairuse` into `edit.json` when it exists, and the UI reloads the editor
  after such a change. Before this, changing Fairuse in Settings and pressing
  Render still rendered the previous level, because the renderer reads
  `edit.json`, not `settings_json`.
- **Cheap stale check.** `render.is_stale` used to rebuild the whole plan, which
  runs OpenCV face detection, on every project poll — `/api/projects` took tens
  of seconds and the face cache was rewritten mid-render. Each render now stores
  a `sig` of edit + source + timeline, and the check only reads those files.
  Renders made before this change fall back to comparing edit/output mtimes.

## Still to confirm (M3+)

1. How much of the original audio bed sits under narration per fairuse level.
   The current render uses narration only.
2. Caption line breaks for scripts without spaces (Thai/Khmer/Chinese/Japanese):
   the Unicode-aware wrap is implemented, but Thai/Khmer word breaking is
   approximate.
3. Whether to bundle Noto fonts for Thai/Chinese/Japanese/Hindi instead of using
   the Windows fonts.
4. Scene gap feel: scenes are a fixed 5 s, so short narration lines leave
   ~1 s of silence over the footage. `scene_sec` can be lowered to 3-4 s if the
   pacing should be tighter.
