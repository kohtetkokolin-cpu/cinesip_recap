"""Cinesip Recap — script stage (Gemini, quota-budgeted).

Call budget per job (2h movie → ~90min recap): bible 1 + outline 1 +
beats ~5 (batched 6/call) + fit rewrites ~2 + metadata 1 ≈ 11 text calls,
plus ~6 scene-card calls. Everything else is local.

Script quality rules (from recap-channel research):
- Hook in the first 15 seconds: premise drop + stakes + open loop.
- Conversational third-person "friend explaining the movie".
- ≥20% commentary/opinion/analysis — NEVER merely describe visible action
  (YouTube penalizes "simply narrating what is happening on screen").
- Every beat ends with a micro payoff or open loop.
- Full spoilers: the recap IS the whole story compressed.
- ~150 words/minute pacing.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from .. import config
from .quota import QuotaManager
from .analyze import evidence_summary_for_prompt

MODEL_CASCADE = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]


class GeminiClient:
    def __init__(self, quota: QuotaManager | None = None):
        self.quota = quota or QuotaManager()

    def generate(self, prompt: str, max_retries: int = 3) -> str:
        from google import genai
        last_err = None
        for attempt in range(max_retries):
            key = self.quota.next_key()
            if not key:
                raise RuntimeError("Gemini quota ကုန်သွားပါပြီ — key အသစ်ထည့်ပါ ဒါမှမဟုတ် မနက်ဖြန်မှ ပြန်လုပ်ပါ။")
            for model in MODEL_CASCADE:
                try:
                    client = genai.Client(api_key=key)
                    resp = client.models.generate_content(model=model, contents=prompt)
                    self.quota.record(key)
                    txt = (resp.text or "").strip()
                    if txt:
                        return txt
                except Exception as e:
                    last_err = e
                    msg = str(e)
                    if "429" in msg or "503" in msg or "overloaded" in msg.lower():
                        time.sleep(2 + attempt * 3)
                        continue
                    break  # non-retryable for this key; try next key
        raise RuntimeError(f"Gemini မရနိုင်ပါ: {last_err}")


QUALITY_RULES = """
SCRIPT QUALITY RULES (must follow):
1. HOOK FIRST: the first 2 sentences must drop the most shocking premise + stakes + one open loop. No greetings, no "today we will recap".
2. VOICE: conversational third-person, like a friend explaining the movie. Short punchy sentences.
3. COMMENTARY RATIO: at least 20% of every beat must be YOUR take — why a character acted that way, what the scene really means, a sharp observation or dark joke. NEVER just describe what is visible on screen ("he opens the door and walks in" alone is FORBIDDEN — always add meaning).
4. OPEN LOOPS: end each beat with a payoff or a question that pulls into the next beat.
5. FULL STORY: spoilers allowed — the recap covers the whole movie including the ending.
6. FACTS ONLY from the transcript/scenes given. Never invent plot points.
7. Language: natural spoken Burmese (Myanmar). No formal written style.
8. Pacing: ~150 words per minute of narration.
""".strip()

BIBLE_PROMPT = """You are a movie analyst. Read this transcript+scene evidence and write a story bible as JSON.
{evidence}
Return ONLY valid JSON: {{"title_hint": "...", "logline": "...", "protagonist": "...",
"antagonist_or_force": "...", "themes": ["..."], "turning_points": ["... (5-8)"],
"characters": [{{"name": "...", "role": "..."}}], "hook_premise": "one shocking sentence for the cold open",
"ending_spoiler": "how it ends in 2 sentences"}}
{rules}
"""


def _extract_json(txt: str) -> dict | list:
    """Extract the first JSON object or array from model output."""
    t = txt.strip()
    # strip markdown fences
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    # try whole text first
    try:
        return json.loads(t)
    except Exception:
        pass
    # find first JSON start and raw-decode from there (handles arrays + objects)
    m = re.search(r"[\[{]", t)
    if not m:
        raise ValueError("no JSON in model output")
    obj, _ = json.JSONDecoder().raw_decode(t[m.start():])
    return obj


def write_bible(client: GeminiClient, ev: dict) -> dict:
    ev_txt = evidence_summary_for_prompt(ev)
    out = client.generate(BIBLE_PROMPT.format(evidence=ev_txt, rules=QUALITY_RULES))
    return _extract_json(out)


OUTLINE_PROMPT = """You are a recap scriptwriter. Given this story bible and evidence, plan {n_beats} beats
for a {target_min}-minute Burmese movie recap (~150 words/min narration).

BIBLE: {bible}
EVIDENCE: {evidence}

Return ONLY valid JSON list, one object per beat:
[{{"idx": 1, "footage_start": <sec>, "footage_end": <sec>, "goal": "what this beat must convey",
"hook_line": "opening line of this beat", "payoff": "how the beat ends (payoff or open loop)",
"target_words": <~450 for 3 min>}}, ...]
Beats must cover the whole film in order, footage windows must not overlap, and the
FIRST beat's hook_line must be the cold-open premise drop.
{rules}
"""


def write_outline(client: GeminiClient, bible: dict, ev: dict,
                  target_min: float, n_beats: int) -> list:
    ev_txt = evidence_summary_for_prompt(ev, max_chars=6000)
    out = client.generate(OUTLINE_PROMPT.format(
        n_beats=n_beats, target_min=round(target_min, 1),
        bible=json.dumps(bible, ensure_ascii=False)[:4000],
        evidence=ev_txt, rules=QUALITY_RULES))
    beats = _extract_json(out)
    # normalize footage windows
    dur = ev["duration"]
    for i, b in enumerate(beats):
        b["idx"] = i + 1
        b["footage_start"] = max(0.0, float(b.get("footage_start", 0)))
        b["footage_end"] = min(dur, float(b.get("footage_end", dur)))
        if b["footage_end"] <= b["footage_start"]:
            b["footage_end"] = min(dur, b["footage_start"] + 60)
    return beats


BEATS_PROMPT = """You are a Burmese recap narrator. Write the full spoken narration for these beats.
Each beat: conversational third-person Burmese, ~150 words/min, commentary woven in.

BEATS TO WRITE (JSON): {beats_json}
STORY CONTEXT: {bible}

Return ONLY valid JSON list: [{{"idx": <n>, "narration": "<full Burmese narration>",
"voice": "narrator"|"female"|"male"}}, ...]
{rules}
"""


def write_beats(client: GeminiClient, bible: dict, beats: list,
                batch_size: int = 6, max_workers: int = 3) -> list:
    """Write narrations in parallel batches. Returns beats with 'narration'."""
    batches = [beats[i:i + batch_size] for i in range(0, len(beats), batch_size)]
    bible_s = json.dumps(bible, ensure_ascii=False)[:3000]

    def _one(batch):
        slim = [{k: b.get(k) for k in ("idx", "goal", "hook_line", "payoff", "target_words")}
                for b in batch]
        out = client.generate(BEATS_PROMPT.format(
            beats_json=json.dumps(slim, ensure_ascii=False),
            bible=bible_s, rules=QUALITY_RULES))
        return {r["idx"]: r for r in _extract_json(out)}

    merged: dict = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for m in ex.map(_one, batches):
            merged.update(m)
    for b in beats:
        r = merged.get(b["idx"], {})
        b["narration"] = r.get("narration", "")
        b["voice"] = r.get("voice", "narrator") if r.get("voice") in (
            "narrator", "female", "male") else "narrator"
    return beats


REWRITE_PROMPT = """Shorten this Burmese narration to at most {max_words} words, keeping the hook,
the key plot point and the ending payoff. Keep it punchy and spoken-style.
Return ONLY the rewritten narration, no JSON.

NARRATION: {narration}
"""


def rewrite_shorter(client: GeminiClient, narration: str, max_words: int) -> str:
    return client.generate(REWRITE_PROMPT.format(max_words=max_words, narration=narration))


def build_script(client: GeminiClient, ev: dict, target_min: float,
                 progress_cb=None) -> dict:
    """Full script pipeline: bible → outline → beats. Returns {bible, beats}."""
    n_beats = max(4, int(target_min / 3))
    if progress_cb:
        progress_cb("bible", 0, 1)
    bible = write_bible(client, ev)
    if progress_cb:
        progress_cb("outline", 0, 1)
    beats = write_outline(client, bible, ev, target_min, n_beats)
    if progress_cb:
        progress_cb("beats", 0, len(beats))
    beats = write_beats(client, bible, beats)
    if progress_cb:
        progress_cb("beats", len(beats), len(beats))
    return {"bible": bible, "beats": beats, "target_min": target_min}
