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
from .fairuse import beat_video_plan

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


def find_bgm() -> str | None:
    for f in sorted(os.listdir(BGM_DIR)):
        if f.lower().endswith((".mp3", ".wav", ".m4a", ".ogg")):
            return os.path.join(BGM_DIR, f)
    return None


def render_beat(job_id: str, beat: dict, src_video: str, vo_wav: str,
               scenes: list, w: int, h: int, max_clip_sec: float,
               workdir: str) -> dict:
    """Render one beat: video exactly == VO duration. Returns {mp4, dur}."""
    from .voice import wav_duration
    dur = wav_duration(vo_wav)
    plan = beat_video_plan(beat, scenes, w, h, max_clip_sec=max_clip_sec)
    clips = plan["clips"]
    if not clips:  # degenerate: freeze first frame
        clips = [{"start": beat["footage_start"],
                  "end": min(beat["footage_end"], beat["footage_start"] + 2),
                  "transform": "plain", "filter": "null"}]

    n = len(clips)
    fc = []
    inputs = []
    for i, c in enumerate(clips):
        inputs += ["-ss", str(c["start"]), "-t", str(round(c["end"] - c["start"], 2)),
                   "-i", src_video]
        fc.append(f"[{i}:v]{c['filter']},setpts=PTS-STARTPTS,"
                  f"fps=30,scale={w}:{h}:flags=lanczos,setsar=1[v{i}]")
    vcat = "".join(f"[v{i}]" for i in range(n))
    # extend with frozen last frame OR trim — to EXACTLY dur
    fc.append(f"{vcat}concat=n={n}:v=1:a=0,"
              f"tpad=stop_mode=clone:stop_duration=60,"
              f"trim=duration={dur:.3f},setpts=PTS-STARTPTS[vcat]")
    # VO exactly dur (pad tiny gaps, trim overruns)
    inputs += ["-i", vo_wav]
    fc.append(f"[{n}:a]atrim=duration={dur:.3f},apad=whole_dur={dur:.3f},"
              f"atrim=duration={dur:.3f},asetpts=PTS-STARTPTS[aout]")

    out = os.path.join(workdir, f"beat_{beat['idx']:03d}.mp4")
    cmd = [config.FFMPEG, "-y", "-v", "error"] + inputs + \
        ["-an", "-filter_complex", ";".join(fc),
         "-map", "[vcat]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-shortest", out]
    # note: -an drops source audio from the -ss inputs; VO mapped explicitly
    _run(cmd)
    return {"mp4": out, "dur": dur, "beat_idx": beat["idx"]}


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
    inputs, maps = [], []
    for i, bf in enumerate(beat_files):
        inputs += ["-i", bf["mp4"]]
        maps += [f"[{i}:v]", f"[{i}:a]"]
    vcat = "".join(maps[0::2]); acat = "".join(maps[1::2])
    fc = [f"{vcat}concat=n={n}:v=1:a=0[vcat]", f"{acat}concat=n={n}:v=0:a=1[acat]"]

    bgm = find_bgm()
    if bgm:
        inputs += ["-i", bgm]
        # loop BGM, duck under VO via sidechain, then mix
        fc += [f"[{n}:a]aloop=loop=-1:size=2e9,volume=0.14[bg]",
               "[bg][acat]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=500[bgd]",
               "[acat][bgd]amix=inputs=2:duration=first:dropout_transition=0[aout]"]
    else:
        fc.append("[acat]anull[aout]")

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
    return {"video": out, "srt": srt_path, "ass": ass_path,
            "duration": sum(b.get("vo_dur", 0) for b in beats)}
