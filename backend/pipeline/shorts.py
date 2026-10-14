"""Cinesip Recap — shorts stage.

Cut 3-6 vertical (9:16) shorts from the finished long recap, each built
around the sharpest hooks:
  - score beats by hook strength (hook_line quality + payoff + position)
  - each short = 60-90s: cold-open hook beat + 1-2 following beats
  - 9:16 reframe (center crop), karaoke captions burned, BGM kept
"""
import os
import subprocess

from .. import config
from .render import RENDER_DIR


def score_beat(b: dict) -> float:
    s = 0.0
    hook = (b.get("hook_line") or "") + " " + (b.get("narration") or "")[:120]
    for kw in ["?", "!", "လျှို့ဝှက်", "သတိ", "မယုံ", "အံ့ဩ", "ကြောက်",
               "secret", "shocking", "never", "killer", "escape"]:
        if kw in hook:
            s += 1.0
    # early beats are better hooks; very late beats are closers
    idx = b.get("idx", 1)
    if idx <= 3:
        s += 1.5
    if b.get("payoff"):
        s += 0.5
    return s


def _vreframe_916(src: str, dst: str, w: int, h: int, t0: float, dur: float,
                  ass_path: str | None = None):
    # center crop to 9:16 then scale to 1080x1920
    vf = (f"crop=w=ih*9/16:h=ih:x=(iw-ih*9/16)/2:y=0,"
          f"scale=1080:1920:flags=lanczos")
    if ass_path:
        ap = ass_path.replace("\\", "/").replace(":", "\\:")
        vf += f",subtitles='{ap}'"
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-ss", str(t0),
                    "-t", str(dur), "-i", src, "-vf", vf,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                    "-c:a", "aac", dst], check=True)


def make_shorts(job_id: str, final_mp4: str, beats: list, w: int, h: int,
                n_shorts: int = 4, progress_cb=None) -> list:
    """Return [{mp4, title_hint}]."""
    workdir = os.path.join(RENDER_DIR, job_id, "shorts")
    os.makedirs(workdir, exist_ok=True)
    ranked = sorted(beats, key=score_beat, reverse=True)
    # cumulative beat start times in the final video
    t = 0.0
    starts = {}
    for b in beats:
        starts[b["idx"]] = t
        t += b.get("vo_dur", 0) or 0

    shorts = []
    used = set()
    for b in ranked:
        if len(shorts) >= n_shorts or b["idx"] in used:
            continue
        # take this beat + next beats until 60-90s
        seq, dur = [], 0.0
        for bb in beats:
            if bb["idx"] >= b["idx"] and bb["idx"] not in used:
                d = bb.get("vo_dur", 0) or 0
                if dur + d > 95:
                    break
                seq.append(bb); dur += d
                if dur >= 55:
                    break
        if dur < 45:
            continue
        for bb in seq:
            used.add(bb["idx"])
        t0 = starts[seq[0]["idx"]]
        out = os.path.join(workdir, f"short_{len(shorts)+1:02d}.mp4")
        _vreframe_916(final_mp4, out, w, h, t0, dur)
        shorts.append({"mp4": out, "title_hint": (seq[0].get("hook_line") or "")[:80],
                       "duration": round(dur, 1)})
        if progress_cb:
            progress_cb("shorts", len(shorts), n_shorts)
    return shorts
