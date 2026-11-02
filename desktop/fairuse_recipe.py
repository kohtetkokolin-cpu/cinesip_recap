"""Fair-use edit recipe for each preset level.

The five Zippy presets carry the same visual template; `fairUse` selects how
much of the footage is transformed. The picker screens define the recipe:

    30 / 60   zoom + fade
    90        zoom + fade + freeze
    120       + transition + full-frame flip
    150       + face-centred zoom and freeze → zoom out

Every level also removes short 5-second blocks from the source timeline before
the scripted scenes are reassembled, so no long continuous copyrighted stretch
ever survives.
"""
from __future__ import annotations

CUT_SEC = 5.0

RECIPES: dict[int, dict] = {
    30: {
        "label": "ရိုးရိုး",
        "zoom": 0.03,
        "fade_in": 0.20,
        "fade_out": 0.20,
        "freeze": 0.0,
        "flip": False,
        "transition": False,
        "face_zoom": False,
        "zoom_out": False,
        "cut_every": 240.0,
        "colour": {},
    },
    60: {
        "label": "အလယ်အရပ်",
        "zoom": 0.06,
        "fade_in": 0.25,
        "fade_out": 0.25,
        "freeze": 0.0,
        "flip": False,
        "transition": False,
        "face_zoom": False,
        "zoom_out": False,
        "cut_every": 180.0,
        "colour": {"sat": 1.06, "con": 1.02},
    },
    90: {
        "label": "အလယ်",
        "zoom": 0.08,
        "fade_in": 0.25,
        "fade_out": 0.25,
        "freeze": 0.50,
        "flip": False,
        "transition": False,
        "face_zoom": False,
        "zoom_out": False,
        "cut_every": 120.0,
        "colour": {"sat": 1.10, "con": 1.04, "gamma": 1.02},
    },
    120: {
        "label": "အကြိမ်",
        "zoom": 0.10,
        "fade_in": 0.18,
        "fade_out": 0.0,
        "freeze": 0.60,
        "flip": True,
        "transition": True,
        "face_zoom": False,
        "zoom_out": False,
        "cut_every": 90.0,
        "colour": {"sat": 1.16, "con": 1.06, "gamma": 1.02, "hue": 4.0},
    },
    150: {
        "label": "အကြိမ်းဆုံး",
        "zoom": 0.12,
        "fade_in": 0.18,
        "fade_out": 0.0,
        "freeze": 0.80,
        "flip": True,
        "transition": True,
        "face_zoom": True,
        "zoom_out": True,
        "cut_every": 60.0,
        "colour": {"sat": 1.22, "con": 1.08, "gamma": 1.03, "hue": 8.0},
    },
}


def recipe(level: int) -> dict:
    return RECIPES.get(int(level), RECIPES[30])


def techniques(level: int) -> list[str]:
    """Human-readable technique chips for the UI (matches the picker screens)."""
    r = recipe(level)
    out = []
    if r.get("zoom"):
        out.append("Zoom")
    if r.get("fade_in") or r.get("fade_out"):
        out.append("Fade")
    if r.get("freeze"):
        out.append("Freeze")
    if r.get("flip"):
        out.append("Flip")
    if r.get("transition"):
        out.append("Transition")
    if r.get("face_zoom"):
        out.append("Face zoom")
    if r.get("colour"):
        out.append("Colour")
    return out


def cut_blocks(duration: float, level: int) -> list[tuple[float, float]]:
    """5-second source blocks removed across the whole film."""
    every = float(recipe(level).get("cut_every") or 0.0)
    if every <= CUT_SEC:
        return []
    out: list[tuple[float, float]] = []
    t = every
    while t + CUT_SEC <= duration:
        out.append((round(t, 3), round(t + CUT_SEC, 3)))
        t += every
    return out


def subtract(ss: float, se: float, blocks: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Kept parts of [ss, se] after removing the blocks (absolute seconds)."""
    spans = [(ss, se)]
    for a, b in blocks:
        if b <= ss or a >= se:
            continue
        nxt: list[tuple[float, float]] = []
        for x, y in spans:
            if b <= x or a >= y:
                nxt.append((x, y))
                continue
            if a > x:
                nxt.append((x, a))
            if b < y:
                nxt.append((b, y))
        spans = nxt
    return [(a, b) for a, b in spans if b - a >= 0.15]


def window_spans(ss: float, se: float, blocks: list[tuple[float, float]],
                 src_dur: float) -> list[tuple[float, float]]:
    """Kept spans for one scripted scene.

    A block that cuts a scene in half becomes a jump cut. When that would leave
    too little footage (slow-motion mush), the scene is nudged past the block
    instead so the narration still has a continuous shot to sit on.
    """
    target = max(0.05, se - ss)
    best = subtract(ss, se, blocks)
    best_total = sum(b - a for a, b in best)
    if best_total >= target * 0.6:
        return best

    cur_ss, cur_se = ss, se
    for _ in range(3):
        hit = next(((a, b) for a, b in blocks if a < cur_se and b > cur_ss), None)
        if not hit:
            break
        a, b = hit
        if b + target <= src_dur:
            cur_ss, cur_se = b, b + target
        elif a - target >= 0:
            cur_ss, cur_se = a - target, a
        else:
            break
        spans = subtract(cur_ss, cur_se, blocks)
        if sum(y - x for x, y in spans) >= target * 0.6:
            return spans

    return best or [(ss, se)]


def _next_block_start(cursor: float, blocks: list[tuple[float, float]]) -> float:
    starts = [a for a, _b in blocks if a > cursor]
    return min(starts) if starts else float("inf")


def _prev_block_end(cursor: float, blocks: list[tuple[float, float]]) -> float:
    ends = [b for _a, b in blocks if b < cursor]
    return max(ends) if ends else 0.0


def scene_spans(center: float, need: float, blocks: list[tuple[float, float]],
                src_dur: float) -> list[tuple[float, float]]:
    """`need` seconds of footage at natural speed, skipping removed blocks.

    The window is centred on the scripted scene, then extended to the right
    (and, at the end of the film, to the left). A 5-second block inside the
    window becomes a jump cut instead of being stretched into slow motion.
    """
    need = max(0.2, min(float(need), max(0.2, src_dur)))
    start = max(0.0, min(src_dur - need, float(center) - need / 2.0))
    spans: list[tuple[float, float]] = []
    acc = 0.0
    cursor = start

    while acc < need - 1e-6 and cursor < src_dur - 1e-6:
        inside = next(((a, b) for a, b in blocks if a <= cursor < b), None)
        if inside:
            cursor = inside[1]
            continue
        limit = min(src_dur, cursor + (need - acc), _next_block_start(cursor, blocks))
        if limit - cursor < 0.05:
            nxt = _next_block_start(cursor, blocks)
            if nxt == float("inf"):
                break
            cursor = nxt
            continue
        spans.append((cursor, limit))
        acc += limit - cursor
        cursor = limit

    if acc < need - 1e-6:
        cursor = start
        while acc < need - 1e-6 and cursor > 1e-6:
            inside = next(((a, b) for a, b in blocks if a < cursor <= b), None)
            if inside:
                cursor = inside[0]
                continue
            limit = max(0.0, cursor - (need - acc), _prev_block_end(cursor, blocks))
            if cursor - limit < 0.05:
                prev = _prev_block_end(cursor, blocks)
                if prev <= 0.05:
                    break
                cursor = prev
                continue
            spans.insert(0, (limit, cursor))
            acc += cursor - limit
            cursor = limit

    return [(round(a, 3), round(b, 3)) for a, b in spans if b - a > 0.02]
