"""Cinesip Recap — fair-use edit engine.

Automatic transformative editing applied to EVERY recap (research-backed):
  1. Clip cap: no continuous source clip longer than max_clip_sec (default 5s).
     Clips are cut at scene boundaries from the evidence package.
  2. Per-clip visual transform (seeded random pick): mirror / zoom punch /
     reframe crop / speed ramp / color grade. Breaks fingerprinting, keeps
     the story readable.
  3. Original audio is ALWAYS muted — final audio is VO + royalty-free BGM only.
     (Music Content ID is a major claim vector; never use the film's score.)
  4. Commentary density check: narration that merely describes visible action
     ("he opens the door") with no opinion/analysis is flagged for rewrite —
     YouTube's reused-content policy requires transformative commentary.

These are editing features that reduce risk, not legal guarantees. The app
never claims a video is "copyright free".
"""
import random

# opinion/analysis markers (Burmese + English) used by the density heuristic
_COMMENTARY_MARKERS = [
    "ဘာကြောင့်", "ဆိုလိုတာ", "အဓိပ္ပာယ်", "ထင်တယ်", "မြင်တယ်",
    "စိတ်ဝင်စား", "သတိထား", "နောက်ကွယ်", "အမှန်", "တကယ်",
    "why", "means", "really", "actually", "think", "interesting",
    "!", "?", "…",
]


def commentary_score(narration: str) -> float:
    """Heuristic 0..1: how much opinion/analysis vs pure description."""
    if not narration:
        return 0.0
    words = narration.split()
    if not words:
        return 0.0
    hits = sum(1 for m in _COMMENTARY_MARKERS if m in narration)
    # normalize: ~1 marker per 25 words ≈ good density
    return min(1.0, hits / max(1, len(words) / 25))


def needs_commentary_rewrite(narration: str, threshold: float = 0.35) -> bool:
    return commentary_score(narration) < threshold


def plan_clips(footage_start: float, footage_end: float, scenes: list,
               max_clip_sec: float = 5.0, seed: int = 0) -> list:
    """Split a beat's footage window into ≤max_clip_sec clips at scene cuts.

    Returns [{start, end, transform}] where transform is one of the
    visual transforms below.
    """
    rng = random.Random(seed)
    cuts = [footage_start]
    for a, b in scenes:
        if footage_start < a < footage_end:
            cuts.append(a)
        if footage_start < b < footage_end:
            cuts.append(b)
    cuts.append(footage_end)
    cuts = sorted(set(cuts))

    clips = []
    for s, e in zip(cuts, cuts[1:]):
        # further split long stretches
        t = s
        while e - t > max_clip_sec:
            clips.append((t, t + max_clip_sec))
            t += max_clip_sec
        if e - t > 0.4:
            clips.append((t, e))
    transforms = ["zoom_punch", "reframe", "grade_warm", "grade_cool",
                  "speed_ramp", "mirror", "plain"]
    out = []
    for i, (s, e) in enumerate(clips):
        # never two identical transforms in a row; keep some plain for readability
        tr = rng.choice(transforms)
        if out and out[-1]["transform"] == tr:
            tr = rng.choice([x for x in transforms if x != tr])
        out.append({"start": round(s, 2), "end": round(e, 2), "transform": tr})
    return out


def transform_filter(tr: str, w: int, h: int, seed: int = 0) -> str:
    """ffmpeg video filter snippet for one clip's transform."""
    rng = random.Random(seed)
    if tr == "mirror":
        return "hflip"
    if tr == "zoom_punch":
        # slow push-in 1.0 → ~1.25 over the clip (d=1: one output frame per input).
        # NOTE: zoompan HANGS if followed by setpts/fps (infinite loop), so the
        # caller must NOT append ",setpts=PTS-STARTPTS,fps=30" for this transform —
        # zoompan's own fps=30 already normalizes the output. See _render_chunk.
        return ("scale=iw*1.3:ih*1.3,"
                "zoompan=z='min(1.0+0.0012*on,1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d=1:s={w}x{h}:fps=30")
    if tr == "reframe":
        zx = rng.uniform(0.82, 0.95)
        dx = rng.uniform(0, 1 - zx)
        return (f"crop=w=iw*{zx:.3f}:h=ih*{zx:.3f}:x=iw*{dx:.3f}:y=ih*{dx:.3f},"
                f"scale={w}:{h}")
    if tr == "speed_ramp":
        sp = rng.choice([0.92, 1.08, 1.12])
        return f"setpts={1/sp:.4f}*PTS"
    if tr == "grade_warm":
        return "eq=contrast=1.06:saturation=1.1,colorbalance=rs=0.10:gs=0.03:bs=-0.10"
    if tr == "grade_cool":
        return "eq=contrast=1.05:saturation=1.05,colorbalance=rs=-0.08:bs=0.12"
    return "null"


def beat_video_plan(beat: dict, scenes: list, w: int, h: int,
                    max_clip_sec: float = 5.0) -> dict:
    """Full fair-use plan for one beat: clips + per-clip filters."""
    clips = plan_clips(beat["footage_start"], beat["footage_end"], scenes,
                       max_clip_sec=max_clip_sec, seed=beat["idx"])
    for i, c in enumerate(clips):
        c["filter"] = transform_filter(c["transform"], w, h, seed=beat["idx"] * 100 + i)
    return {"clips": clips,
            "n_clips": len(clips),
            "coverage_sec": round(sum(c["end"] - c["start"] for c in clips), 2)}
