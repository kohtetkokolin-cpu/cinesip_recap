"""Cinesip Recap — metadata stage (upload pack).

Generates platform-ready titles/descriptions/tags. Title formula (research):
full plot premise, present tense, sensational but accurate, NO movie name
in long-form titles; Shorts get punchy premise + hashtags.

Costs 1 Gemini call; falls back to templates offline.
"""
import json
import re


META_PROMPT = """You are a YouTube strategist for movie recap channels. Given this story bible,
write upload metadata as JSON. Titles: full plot premise, present tense, sensational but
accurate, NO movie name. Description: 3-4 sentences + chapters placeholder + hashtags.
Tags: 12-15. Also 3 short-form titles (punchy + hashtags) for TikTok/Reels/Shorts.

BIBLE: {bible}

Return ONLY valid JSON:
{{"titles": ["...", "...", "..."],
  "description": "...",
  "tags": ["...", ...],
  "short_titles": ["... #movierecap #shorts", ...],
  "hashtags": ["#movierecap", ...]}}
"""


def _template_meta(bible: dict) -> dict:
    hook = bible.get("hook_premise", "An unforgettable story")
    return {
        "titles": [hook[:90],
                   f"{hook[:70]} — Full Story Explained",
                   "You Won't Believe How This Ends — Full Movie Recap"],
        "description": (f"{hook}\n\nFull movie recap with commentary — "
                        "spoilers ahead.\n\n#movierecap #recap"),
        "tags": ["movie recap", "film recap", "movie explained", "recap",
                 "story explained", "movierecap", "film explained"],
        "short_titles": [f"{hook[:60]} #movierecap #shorts"],
        "hashtags": ["#movierecap", "#movierecapped", "#movie", "#shorts"],
    }


def generate_metadata(client, bible: dict) -> dict:
    try:
        out = client.generate(META_PROMPT.format(
            bible=json.dumps(bible, ensure_ascii=False)[:3000]))
        m = re.search(r"\{.*\}", out, re.S)
        meta = json.loads(m.group(0))
        for k in ("titles", "description", "tags", "short_titles", "hashtags"):
            if k not in meta:
                raise ValueError(f"missing {k}")
        return meta
    except Exception:
        return _template_meta(bible)
