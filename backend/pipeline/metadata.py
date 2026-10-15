"""Cinesip Recap — metadata stage (upload pack).

Generates platform-ready titles/descriptions/tags per the 2026-10-15 Myanmar
recap channel research (R7/R10/R11):
- Titles: pure-Burmese spoiler-hook ("…တဲ့အခါ" / question / number cluster / emoji).
- Description: opening narration as poetic line-break teaser + synopsis + polite
  CTA + Burmese/English hashtag block + links placeholder.
- CTA: polite Burmese with နော်/ဦး particles + thank-you note.

Costs 1 Gemini call; falls back to templates offline.
"""
import json
import re


META_PROMPT = """You are a YouTube strategist for Burmese movie-recap channels. Given this story bible,
write upload metadata as JSON. ALL user-facing text must be in Burmese (Myanmar language).

TITLES (spoiler-hook, pure Burmese): 3 titles, each a curiosity-gap spoiler hook — use one
of these proven patterns: "…တဲ့အခါ" / a burning question ("ဘယ်သူက…လဲ?") / a number
cluster ("သရဲ1ကောင်၊ဝန်ထမ်း2ယောက်") / emoji emphasis. Prefer a pure Burmese hook over the
English movie name. Sensational but accurate.
{style_title_hint}

DESCRIPTION (script-teaser SEO): FIRST, the OPENING NARRATION below, broken into short
poetic lines (one phrase per line). Then 2-3 sentences of synopsis. Then the closing CTA
line (below). Then a hashtag block (Burmese + English). End with a "links" placeholder
line: "Telegram / Facebook / TikTok: ...".

CTA (polite Burmese): the closing line must use နော်/ဦး particles and a thank-you, e.g.
"Channel လေးကို Subscribe လုပ်ထားပေးပါဦးနော် — အားပေးတဲ့ ပရိတ်သတ်များကို
ကျေးဇူးတင်ပါတယ်။"

TAGS: 12-15 tags, Burmese + English mix.
SHORT_TITLES: 3 punchy Burmese premise hooks + hashtags, for TikTok/Reels/Shorts.

BIBLE: {bible}
OPENING NARRATION: {opening}

Return ONLY valid JSON:
{{"titles": ["...", "...", "..."],
  "description": "...",
  "tags": ["...", ...],
  "short_titles": ["... #ဇာတ်ကားအကျဉ်း #shorts", ...],
  "hashtags": ["#ဇာတ်ကားအကျဉ်း", ...]}}
"""


def _template_meta(bible: dict, opening: str = "") -> dict:
    hook = bible.get("hook_premise", "ဒီဇာတ်ကားရဲ့ ဇာတ်လမ်း")
    teaser = opening[:600] if opening else hook
    return {
        "titles": [f"{hook[:90]}",
                   "ဒီလိုအဆုံးသတ်မယ်လို့ ဘယ်သူမှ မထင်ထားဘူး — ဇာတ်ကားအကျဉ်း",
                   "အစဆုံး ရှင်းပြချက် — ဇာတ်သိမ်းအထိ spoil ပါတယ်"],
        "description": (f"{teaser}\n\n"
                        "ဇာတ်ကားအကျဉ်း + သုံးသပ်ချက် — အဆုံးထိ spoil ပါတယ်။\n\n"
                        "Channel လေးကို Subscribe လုပ်ထားပေးပါဦးနော် — "
                        "အားပေးတဲ့ ပရိတ်သတ်များကို ကျေးဇူးတင်ပါတယ်။\n\n"
                        "#ဇာတ်ကားအကျဉ်း #movierecap #recapmyanmar"),
        "tags": ["ဇာတ်ကားအကျဉ်း", "movie recap", "film recap", "movie explained",
                 "recap", "recap myanmar", "ဇာတ်လမ်းအကျဉ်း", "movierecap",
                 "film explained", "story explained", "ရုပ်ရှင်အကျဉ်း",
                 "myanmar recap"],
        "short_titles": [f"{hook[:60]} #ဇာတ်ကားအကျဉ်း #shorts"],
        "hashtags": ["#ဇာတ်ကားအကျဉ်း", "#movierecap", "#recapmyanmar", "#shorts"],
    }


def generate_metadata(client, bible: dict, opening_narration: str = "",
                      style: str | None = None) -> dict:
    try:
        from .script import _style_block
        hint = _style_block(style).get("title_hint", "")
        style_line = f"STYLE TITLE FLAVOR: {hint}" if hint else ""
        out = client.generate(META_PROMPT.format(
            bible=json.dumps(bible, ensure_ascii=False)[:3000],
            opening=opening_narration[:1500], style_title_hint=style_line))
        m = re.search(r"\{.*\}", out, re.S)
        meta = json.loads(m.group(0))
        for k in ("titles", "description", "tags", "short_titles", "hashtags"):
            if k not in meta:
                raise ValueError(f"missing {k}")
        return meta
    except Exception:
        return _template_meta(bible, opening_narration)
