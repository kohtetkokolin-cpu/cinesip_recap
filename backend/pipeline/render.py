"""Cinesip Recap — render stage.

Perfect A/V sync design (no drift accumulation):
  - Each beat is rendered INDEPENDENTLY: its video is extended/trimmed to
    EXACTLY the measured VO duration, so beats can never drift apart.
  - Beats are then concatenated (all same fps/codec) — sync is structural.
  - Original movie audio is ALWAYS muted (-an on source inputs).
    Final audio = VO (per-beat) + royalty-free BGM ducked under VO.
  - Karaoke word-highlight captions from TTS word timings (edge-tts);
    beat-level timing fallback when the provider gives no timings.
  - User overlays (blur/logo/watermark/subtitle style/eq) applied last via
    the shared WYSIWYG overlay engine.
"""
import os
import subprocess

from .. import config
from . import overlays as ov
from .fairuse import beat_video_plan, freeze_snap

# ---------- output ratio / resolution ----------
RATIOS = ("16:9", "9:16", "original")
RESOLUTIONS = ("1080p", "720p", "480p")
_RES_H = {"1080p": 1080, "720p": 720, "480p": 480}


def _even(n: float) -> int:
    """Round to nearest even int (x264 requires even dims)."""
    return max(2, round(n / 2) * 2)


def qdur(dur: float, fps: int = 30) -> float:
    """Quantize a duration to an exact frame boundary (MRS frame-clock idea).

    Rounding 187.437s to the nearest 1/30s frame (187.433s) kills the
    ±16ms rounding drift that otherwise accumulates across dozens of beats
    and makes the final duration wander off target."""
    return round(round(dur * fps) / fps, 3)


def output_dims(ratio: str, resolution: str, src_w: int, src_h: int) -> tuple[int, int]:
    """Target (w, h) for the final video — always even (x264-safe)."""
    if ratio not in RATIOS:
        ratio = "16:9"
    if resolution not in _RES_H:
        resolution = "720p"
    if ratio == "original":
        return _even(src_w), _even(src_h)
    h = _RES_H[resolution]
    w = _even(h * 9 / 16) if ratio == "9:16" else _even(h * 16 / 9)
    return w, h


FILL_MODES = ("crop", "blur", "color")


def ratio_filter(ratio: str, tw: int, th: int, fill: str = "crop",
                 fill_color: str = "black") -> str:
    """ffmpeg filter: fit source into target dims.
    16:9/original → scale-to-fit + letterbox pad (never crops content).
    9:16 → fill mode decides what happens to the empty bands:
      crop  — center-crop source to vertical (classic Shorts look)
      blur  — fit video, fill bands with blurred copy (trendy vertical look)
      color — fit video, fill bands with a solid color
    """
    if ratio == "9:16" and fill == "blur":
        return (f"split=2[bs1][bs2];"
                f"[bs1]scale={tw}:{th}:force_original_aspect_ratio=increase,"
                f"crop={tw}:{th},gblur=sigma=50[bg];"
                f"[bs2]scale={tw}:-2:flags=lanczos[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    if ratio == "9:16" and fill == "color":
        fc = fill_color if fill_color else "black"
        return (f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color={fc},setsar=1")
    if ratio == "9:16":
        return (f"crop=w='min(iw,ih*9/16)':h='min(ih,iw*16/9)',"
                f"scale={tw}:{th}:flags=lanczos,setsar=1")
    return (f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
            f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")

RENDER_DIR = os.path.join(config.DATA_DIR, "renders")
os.makedirs(RENDER_DIR, exist_ok=True)
BGM_DIR = os.path.join(config.DATA_DIR, "bgm")
os.makedirs(BGM_DIR, exist_ok=True)


def _run(cmd, log_path=None):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        if log_path:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(r.stderr[-4000:])
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-600:]}")
    return r


def _probe_dur(path: str, stream: str | None = None) -> float:
    """ffprobe duration of a file (or one stream). -1.0 when unreadable."""
    try:
        if stream:
            sel = ["-select_streams", stream, "-show_entries", "stream=duration"]
        else:
            sel = ["-show_entries", "format=duration"]
        out = subprocess.check_output(
            [config.FFPROBE, "-v", "error"] + sel +
            ["-of", "default=noprint_wrappers=1:nokey=1", path],
            text=True).strip()
        return float(out)
    except Exception:
        return -1.0


def find_bgm() -> str | None:
    for f in sorted(os.listdir(BGM_DIR)):
        if f.lower().endswith((".mp3", ".wav", ".m4a", ".ogg")):
            return os.path.join(BGM_DIR, f)
    return None


def render_beat(job_id: str, beat: dict, src_video: str, vo_wav: str,
               scenes: list, w: int, h: int, max_clip_sec: float,
               workdir: str, ratio: str = "16:9",
               src_w: int = 0, src_h: int = 0,
               fill: str = "crop", fill_color: str = "black") -> dict:
    """Render one beat: video exactly == VO duration. Returns {mp4, dur}.

    w/h are the TARGET output dims; ratio controls fit vs crop.
    src_w/src_h (source dims) are used for fair-use transforms so the
    transforms work at native resolution before the ratio fit.

    Chunked rendering: a beat can have hundreds of ≤5s clips, and putting
    them all in ONE ffmpeg command exceeds Windows' 32,767-char command
    line limit → [WinError 206]. So clips are rendered in small chunks
    (source dims, transforms only), then chunks are concatenated and the
    ratio conversion + exact-duration trim + VO mux happen once.
    """
    from .voice import wav_duration
    # frame-quantized: the beat's video lands on an exact frame boundary,
    # so no ±16ms drift accumulates across beats
    dur = qdur(wav_duration(vo_wav))
    plan = beat_video_plan(beat, scenes, src_w or w, src_h or h,
                           max_clip_sec=max_clip_sec)
    clips = plan["clips"]
    if not clips:  # degenerate: freeze first frame
        clips = [{"start": beat["footage_start"],
                  "end": min(beat["footage_end"], beat["footage_start"] + 2),
                  "transform": "plain", "filter": "null"}]

    # --- freeze-at-subtitle-boundary (Myanmar Recap Studio idea) ---
    # When the VO outruns the footage, the tail is held on a frozen frame.
    # Freezing mid-sentence looks broken, so snap the freeze start back to
    # the latest subtitle-row end (from word timings) and hold THAT frame
    # with a slow Ken Burns push instead of a dead still.
    fw, fh = src_w or w, src_h or h
    coverage = sum(c["end"] - c["start"] for c in clips)
    snap = None
    if coverage < dur - 0.05 and beat.get("words"):
        snap = freeze_snap(beat["words"], coverage)
    if snap:
        # snap is VO-relative (word timings are 0-based); the clips live on
        # the SOURCE timeline, so map it by walking the sequential clip plan
        acc, snap_src = 0.0, None
        for c in clips:
            d = c["end"] - c["start"]
            if snap <= acc + d:
                snap_src = c["start"] + (snap - acc)
                break
            acc += d
        if snap_src is None:
            snap_src = clips[-1]["end"] if clips else 0.0
        clips = [c for c in clips if c["start"] < snap_src]
        if clips:
            clips[-1]["end"] = min(clips[-1]["end"], snap_src)
        fpng = os.path.join(workdir, f"beat_{beat['idx']:03d}_freeze.png")
        _run([config.FFMPEG, "-y", "-v", "error",
              "-ss", f"{snap_src:.2f}", "-i", src_video,
              "-frames:v", "1", fpng])
        clips.append({"start": snap_src, "end": snap_src, "transform": "freeze",
                      "frame": fpng, "freeze_dur": round(dur - snap, 2),
                      "fw": fw, "fh": fh, "filter": "null"})

    # --- pass 1: render clip chunks (transforms at source dims) ---
    chunk_files = []
    for j in range(0, len(clips), _CHUNK_CLIPS):
        chunk = clips[j:j + _CHUNK_CLIPS]
        cf = os.path.join(workdir, f"beat_{beat['idx']:03d}_c{j//_CHUNK_CLIPS:02d}.mp4")
        _render_chunk(chunk, src_video, cf)
        chunk_files.append(cf)

    # --- pass 2: concat chunks -> ratio fit -> exact dur -> mux VO ---
    n = len(chunk_files)
    inputs, vmaps = [], []
    for i, cf in enumerate(chunk_files):
        inputs += ["-i", cf]
        vmaps.append(f"[{i}:v]")
    inputs += ["-i", vo_wav]
    vcat = "".join(vmaps)

    # ratio conversion AFTER concat (once, not per clip)
    tail_sync = (f"tpad=stop_mode=clone:stop_duration=60,"
                 f"trim=duration={dur:.3f},setpts=PTS-STARTPTS[vcat]")
    if ratio == "9:16" and fill == "blur":
        fc = (f"{vcat}concat=n={n}:v=1:a=0[cc];"
              f"[cc]split=2[bs1][bs2];"
              f"[bs1]scale={w}:{h}:force_original_aspect_ratio=increase,"
              f"crop={w}:{h},gblur=sigma=50[bg];"
              f"[bs2]scale={w}:-2:flags=lanczos[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,{tail_sync}")
    else:
        rf = ratio_filter(ratio, w, h, fill=fill, fill_color=fill_color)
        # (blur handled in the branch above; crop/color rf are single-chain)
        fc = f"{vcat}concat=n={n}:v=1:a=0,{rf},{tail_sync}"
    # VO exactly dur (pad tiny gaps, trim overruns)
    fc_a = (f"[{n}:a]atrim=duration={dur:.3f},apad=whole_dur={dur:.3f},"
            f"atrim=duration={dur:.3f},asetpts=PTS-STARTPTS[aout]")

    out = os.path.join(workdir, f"beat_{beat['idx']:03d}.mp4")
    # -threads capped: beat renders now run in parallel workers (see jobs.py),
    # so each ffmpeg must not grab all CPU cores (default is auto = all cores).
    cmd = [config.FFMPEG, "-y", "-v", "error", "-threads", str(config.RENDER_THREADS)] + inputs + \
        ["-filter_complex", fc + ";" + fc_a,
         "-map", "[vcat]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-shortest", out]
    _run(cmd)
    # clean up chunk files (keep workdir tidy)
    for cf in chunk_files:
        try:
            os.remove(cf)
        except OSError:
            pass
    return {"mp4": out, "dur": dur, "beat_idx": beat["idx"]}


# max clips per ffmpeg invocation — keeps the command line far below
# Windows' 32,767-char CreateProcess limit (see render_beat).
_CHUNK_CLIPS = 15


def _render_chunk(clips: list, src_video: str, out: str) -> None:
    """Render a small group of clips to an intermediate MP4 (source dims,
    fair-use transforms only, no audio)."""
    inputs, fc = [], []
    for i, c in enumerate(clips):
        if c.get("transform") == "freeze":
            # held frame with a slow Ken Burns push (MRS out_z): the single
            # extracted frame is looped for freeze_dur at 30fps, then gently
            # pushed in — reads as intentional, not a dead still.
            # (zoompan must NOT get setpts/fps appended — same hang rule as
            # zoom_punch; its own fps=30 normalizes the output.)
            fd = max(0.1, float(c.get("freeze_dur", 1.0)))
            inputs += ["-loop", "1", "-framerate", "30",
                       "-t", f"{fd:.2f}", "-i", c["frame"]]
            zf = (f"scale=iw*1.3:ih*1.3,"
                  f"zoompan=z='min(1.0+0.0012*on,1.25)':x='iw/2-(iw/zoom/2)':"
                  f"y='ih/2-(ih/zoom/2)':d=1:s={c['fw']}x{c['fh']}:fps=30")
            fc.append(f"[{i}:v]{zf},setsar=1[v{i}]")
            continue
        # Clip extraction: fast input seek + precise trim filter.
        # (2026-10-16 e2e found: "-ss X -t D" as INPUT options + concat HANGS
        #  ffmpeg forever on some clips — EOF never reaches concat. Fixed by
        #  seeking without -t and cutting precisely with trim instead.)
        dur_c = round(c["end"] - c["start"], 2)
        seek = max(0.0, round(c["start"] - 2.0, 2))  # keyframe seek, fast
        off = round(c["start"] - seek, 2)
        inputs += ["-ss", f"{seek:.2f}", "-i", src_video]
        trim = f"trim=start={off:.2f}:end={off + dur_c:.2f},setpts=PTS-STARTPTS,"
        # zoom_punch (zoompan) must NOT get fps appended after itself — it
        # hangs (infinite loop); its own fps=30 already normalizes output.
        # Every clip gets setsar=1: zoompan alters SAR and concat requires
        # identical params on all inputs (else "Error reinitializing filters").
        tail = ",setsar=1" if c["transform"] == "zoom_punch" else ",fps=30,setsar=1"
        fc.append(f"[{i}:v]{trim}{c['filter']}{tail}[v{i}]")
    vcat = "".join(f"[v{i}]" for i in range(len(clips)))
    fc.append(f"{vcat}concat=n={len(clips)}:v=1:a=0[vout]")
    cmd = [config.FFMPEG, "-y", "-v", "error", "-threads", str(config.RENDER_THREADS)] + inputs + \
        ["-an", "-filter_complex", ";".join(fc),
         "-map", "[vout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         out]
    _run(cmd)


def write_karaoke_ass(ass_path: str, w: int, h: int, beats: list,
                      overlay_cfg: dict, font_name: str = "Padauk.ttf"):
    """Word-highlight (karaoke) captions. Falls back to beat-level timing
    when a beat has no word timings."""
    from .overlays import SUB_COLORS
    cfg = ov.sanitize_overlays(overlay_cfg)
    sb = cfg["sub"]
    fontsize = max(10, int(h * sb["size"] / 100))
    x, y = int(w * sb["cx"] / 100), int(h * sb["cy"] / 100)
    color = SUB_COLORS.get(sb["color"], "FFFF00")
    # highlight color: white sweep over yellow base
    base_c = f"&H00{color[4:6]}{color[2:4]}{color[0:2]}"

    def ts(sec):
        sec = max(0, sec); cs = int(sec * 100) % 100
        return f"{int(sec//3600)}:{int(sec//60)%60:02d}:{int(sec)%60:02d}.{cs:02d}"

    head = ("[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {w}\nPlayResY: {h}\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
            "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
            "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Cinesip,{font_name},{fontsize},{base_c},&H000019FF,&H80000000,"
            f"&H80000000,0,0,0,0,100,100,0,0,1,2,1,7,20,20,20,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n")
    body = []
    t = 0.0
    for b in beats:
        dur = b.get("vo_dur", 0) or 0
        words = b.get("words") or []
        if words:
            # karaoke sweep
            parts = []
            base = t
            for wd in words:
                k = max(1, int((wd["end"] - wd["start"]) * 100))
                parts.append(f"{{\\k{k}}}{wd['w']}")
            txt = " ".join(parts)
            body.append(f"Dialogue: 0,{ts(t)},{ts(t+dur)},Cinesip,,0,0,0,,"
                        f"{{\\pos({x},{y})\\an5}}{txt}")
        else:
            body.append(f"Dialogue: 0,{ts(t)},{ts(t+dur)},Cinesip,,0,0,0,,"
                        f"{{\\pos({x},{y})\\an5\\fad(200,200)}}{b.get('narration','')[:400]}")
        t += dur
    with open(ass_path, "w", encoding="utf-8-sig") as f:
        f.write(head + "\n".join(body) + "\n")


def write_srt(srt_path: str, beats: list):
    def ts(sec):
        sec = max(0, sec); ms = int(sec * 1000) % 1000
        return f"{int(sec//3600):02d}:{int(sec//60)%60:02d}:{int(sec)%60:02d},{ms:03d}"
    out, t, n = [], 0.0, 1
    for b in beats:
        dur = b.get("vo_dur", 0) or 0
        if dur <= 0 or not b.get("narration"):
            continue
        out.append(f"{n}\n{ts(t)} --> {ts(t+dur)}\n{b['narration'].strip()}\n")
        t += dur; n += 1
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def assemble_final(job_id: str, beat_files: list, beats: list, w: int, h: int,
                   overlay_cfg: dict, logo_path: str | None,
                   progress_cb=None) -> dict:
    """Concat beats + BGM duck + karaoke captions + user overlays."""
    workdir = os.path.join(RENDER_DIR, job_id)
    os.makedirs(workdir, exist_ok=True)

    # karaoke ASS from word timings
    ass_path = os.path.join(workdir, "karaoke.ass")
    cfg = ov.sanitize_overlays(overlay_cfg)
    write_karaoke_ass(ass_path, w, h, beats, overlay_cfg,
                      font_name=cfg["sub"].get("font") or "Padauk.ttf")
    srt_path = os.path.join(workdir, "final.srt")
    write_srt(srt_path, beats)

    n = len(beat_files)
    # --- per-beat mapping gate (MRS safety net) ---
    # Each beat file's duration must equal its VO duration; repair stragglers
    # here so one bad beat can't poison the final concat.
    for bf, b in zip(beat_files, beats):
        exp = qdur(b.get("vo_dur", 0) or 0)
        if exp <= 0:
            continue
        vd = _probe_dur(bf["mp4"])
        if vd > 0 and abs(vd - exp) > 0.05:
            fixed = bf["mp4"] + ".gate.mp4"
            _run([config.FFMPEG, "-y", "-v", "error", "-i", bf["mp4"],
                  "-t", f"{exp:.3f}", "-c", "copy", fixed])
            os.replace(fixed, bf["mp4"])
    inputs, maps = [], []
    for i, bf in enumerate(beat_files):
        inputs += ["-i", bf["mp4"]]
        maps += [f"[{i}:v]", f"[{i}:a]"]
    vcat = "".join(maps[0::2]); acat = "".join(maps[1::2])
    fc = [f"{vcat}concat=n={n}:v=1:a=0[vcat]", f"{acat}concat=n={n}:v=0:a=1[acat]"]

    bgm = find_bgm()
    # SoloRecap lesson: loud, normalized voice-only audio needs no BGM.
    # loudnorm gives broadcast-consistent loudness on every output.
    if bgm:
        inputs += ["-i", bgm]
        # loop BGM, duck under VO via sidechain, then mix, then normalize
        fc += [f"[{n}:a]aloop=loop=-1:size=2e9,volume=0.14[bg]",
               "[bg][acat]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=500[bgd]",
               "[acat][bgd]amix=inputs=2:duration=first:dropout_transition=0[mx]",
               "[mx]loudnorm=I=-16:TP=-1.5:LRA=11[aout]"]
    else:
        fc.append("[acat]loudnorm=I=-16:TP=-1.5:LRA=11[aout]")

    # user overlays (WYSIWYG) incl. burned karaoke subtitles
    vf_tail = ov.build_filter(w, h, cfg, logo_path=logo_path,
                              font_dir=config.FONT_DIR, ass_path=ass_path)
    # build_filter starts at 0:v — re-root it onto [vcat]
    vf_tail = vf_tail.replace("[0:v]", "[vcat]", 1)
    fc.append(vf_tail)

    out = os.path.join(workdir, "final.mp4")
    cmd = ([config.FFMPEG, "-y", "-v", "error"] + inputs +
           ["-filter_complex", ";".join(fc),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out])
    _run(cmd)
    if progress_cb:
        progress_cb("assemble", 1, 1)

    # --- verify + repair (MRS safety-net idea): the final file must equal
    # the sum of beat VO durations. If ffprobe disagrees by >80ms, re-trim.
    expected = qdur(sum(b.get("vo_dur", 0) or 0 for b in beats))
    try:
        actual = float(subprocess.check_output(
            [config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", out], text=True).strip())
    except Exception:
        actual = expected
    if abs(actual - expected) > 0.08 and expected > 0:
        fixed = os.path.join(workdir, "final_fixed.mp4")
        _run([config.FFMPEG, "-y", "-v", "error", "-i", out,
              "-t", f"{expected:.3f}", "-c", "copy", fixed])
        os.replace(fixed, out)
        actual = expected
    # --- final A/V mapping gate (MRS): video and audio streams must agree ---
    # (Repair, don't fail — failing here would discard a finished render.)
    vd = _probe_dur(out, "v:0")
    ad = _probe_dur(out, "a:0")
    if vd > 0 and ad > 0 and abs(vd - ad) > 0.10:
        fixed = os.path.join(workdir, "final_av.mp4")
        _run([config.FFMPEG, "-y", "-v", "error", "-i", out,
              "-c:v", "copy",
              "-af", f"apad,atrim=0:{vd:.3f}",
              "-t", f"{vd:.3f}", "-c:a", "aac", "-b:a", "160k", fixed])
        os.replace(fixed, out)
        actual = vd
    return {"video": out, "srt": srt_path, "ass": ass_path,
            "duration": round(actual, 1)}
