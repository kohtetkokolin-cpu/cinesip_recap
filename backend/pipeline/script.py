"""Cinesip Recap — script stage (Gemini, quota-budgeted).

Call budget per job (2h movie → ~90min recap): bible 1 + outline 1 +
beats ~5 (batched 6/call) + fit rewrites ~2 + metadata 1 ≈ 11 text calls,
plus ~6 scene-card calls. Everything else is local.

Script quality rules (from 2026-10-15 Myanmar recap channel research, R1-R12):
- Cold open ≤20s: no greeting/intro/jingle — hook-first with 2 archetypes
  (in-medias-res climax → rewind, or setup + open loop).
- First ~90s must establish WHO + PROBLEM + RULES/STAKES; open on an open loop.
- Clear written Burmese narration (not street slang); third-person neutral base,
  second-person audience address; first-person only with a host persona.
- 20-35% commentary per beat (moral, rating, humor) — NEVER mere description.
- Every beat ends with a micro payoff or open loop; full spoilers; ~150 wpm.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from .. import config
from .quota import QuotaManager
from .analyze import evidence_summary_for_prompt

MODEL_CASCADE = config.GEMINI_TEXT_MODELS or ["gemini-3.5-flash-lite"]


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
1. COLD OPEN: the narration must dive INTO the story within the first 20 seconds of audio. NO greeting, NO channel intro, NO jingle, NO spoiler warning. The first 2 sentences must drop the most shocking premise + the stakes + one open loop.
2. HOOK ARCHETYPE — pick the one that fits this film's genre. NEVER a lazy chronological "once upon a time" start:
   (a) IN-MEDIAS-RES: open on the climax confrontation or twist moment, then rewind to the beginning; or
   (b) SETUP + OPEN LOOP: introduce the character, land the core problem, and close on a burning "what happens next?" question.
3. THE 90-SECOND RULE: within the first ~225 words (about 90 seconds) the viewer must know WHO the protagonist is, WHAT the core problem is, and WHAT the rules/stakes are (the deal terms, the deadline, the danger).
4. OPEN-LOOP CLOSE: end the opening block on an explicit open loop addressed directly to the viewer — "what goes wrong and what happens next, you will see for yourself" energy.
5. VOICE: clear written Burmese (စာရေးဗမာ) — never fully formal/literary, never street slang; short punchy sentences. Third-person neutral base narration; address the audience directly in second person every few beats ("ပရိတ်သတ်တို့", "မိတ်ဆွေတို့"). NEVER use first-person "ကျွန်တော်" unless a host persona is explicitly set.
6. COMMENTARY RATIO: 20-35% of every beat must be YOUR take — why a character acted that way, the moral/lesson of the scene, an explicit rating ("10/10"), a sharp observation or joke. NEVER just describe what is visible on screen ("he opens the door and walks in" alone is FORBIDDEN — always add meaning).
7. OPEN LOOPS: end each beat with a payoff or a question that pulls into the next beat.
8. FULL STORY: spoilers allowed — the recap covers the whole movie including the ending.
9. FACTS ONLY from the transcript/scenes given. Never invent plot points.
10. Pacing: ~150 words per minute of narration.
11. NO BOREDOM — the viewer must FEEL the movie, not just hear its plot. Every beat must carry EMOTION:
    what the character wants, fears, or hides. Replace abstract summary ("he was sad", "they fought") with
    concrete sensory moments ("his hands shake on the door handle", "she laughs but her eyes don't").
    NEVER chain "and then... and then..." — each beat needs a turning point, a surprise, or a feeling.
12. RHYTHM: vary sentence length. Action = short punchy sentences. Emotion = longer flowing ones.
    Read each beat aloud in your head — if it drones, rewrite it.
13. NO DEAD AIR: the narration must NEVER stall. Every single sentence pushes the story
    forward — a reveal, a decision, a feeling, a twist. No filler lines, no throat-clearing,
    no "meanwhile, let's take a moment" pauses. If a beat feels slow, cut WORDS, never meaning.
    (Top recap channels run 6+ minutes with zero silence longer than a breath — match that energy.)
14. OPEN-LOOP ENDING: the final beat must NOT trail off with a summary. End on a hook —
    a burning question, a tease for the next episode/recap, or a "what would YOU do?"
    thrown directly at the viewer. The viewer should finish wanting more, not feeling done.
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

STYLE — {style_name}: {style_desc}
HOOK DIRECTIVE: {style_hook}

Return ONLY valid JSON list, one object per beat:
[{{"idx": 1, "footage_start": <sec>, "footage_end": <sec>, "goal": "what this beat must convey",
"hook_line": "opening line of this beat", "payoff": "how the beat ends (payoff or open loop)",
"target_words": <~450 for 3 min>}}, ...]
Beats must cover the whole film in order, footage windows must not overlap, and the
FIRST beat's hook_line must follow the HOOK DIRECTIVE above.

Beat 1 is the COLD OPEN: its narration must dive straight into the story within the
first 20 seconds — no greeting, no intro. The first ~90 seconds of narration must establish
WHO the protagonist is, WHAT the core problem is and WHAT the rules/stakes are; end the
opening block on an explicit open loop addressed to the viewer.
{rules}
"""


def write_outline(client: GeminiClient, bible: dict, ev: dict,
                  target_min: float, n_beats: int,
                  style: str | None = None) -> list:
    ev_txt = evidence_summary_for_prompt(ev, max_chars=6000)
    st = _style_block(style)
    out = client.generate(OUTLINE_PROMPT.format(
        n_beats=n_beats, target_min=round(target_min, 1),
        bible=json.dumps(bible, ensure_ascii=False)[:4000],
        evidence=ev_txt, rules=QUALITY_RULES,
        style_name=st["name"], style_desc=st["desc"], style_hook=st["hook"]))
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

STYLE — {style_name}: {style_desc}
VOICE DIRECTIVE: {style_voice}
COMMENTARY DIRECTIVE: {style_commentary}
{persona_line}
Each beat: clear written Burmese (not slang), third-person neutral base unless the style
sets a host persona, direct second-person audience address ("ပရိတ်သတ်တို့", "မိတ်ဆွေတို့")
every few beats, ~150 words/min.
BEATS TO WRITE (JSON): {beats_json}
STORY CONTEXT: {bible}

Return ONLY valid JSON list: [{{"idx": <n>, "narration": "<full Burmese narration>",
"voice": "narrator"|"female"|"male"}}, ...]
{dub_line}{rules}
"""

DUB_INSTRUCTION = """
DUB MODE (dialogue + narration hybrid):
- DIALOGUE SCENES: write the characters' ACTUAL lines translated into natural, emotional,
  SPOKEN Burmese \u2014 as if dubbing the film. Sound like talking, not writing: interruptions,
  short reactions, emotion. Set "voice" to "male" or "female" matching the speaker.
- NON-DIALOGUE SCENES (action, montage, silent tension, transitions): write gripping
  third-person narration that carries the story ("voice": "narrator").
- One beat = one mode: if a beat mixes both, lead with whichever dominates the footage.
- NEVER narrate over dialogue ("he says...") \u2014 just give the dubbed line directly.
"""

# 14 selectable recap styles — grounded in the 2026-10-15 Myanmar recap channel
# research (R1–R12). Each style drives the outline hook, the narration voice,
# the commentary flavor, and the title hint. Personas: first-person "ကျွန်တော်"
# is allowed ONLY for host styles (R5).
RECAP_STYLES = {
    "cold_open": {
        "name": "⚡ အထွတ်အထိပ်နဲ့စ",
        "desc": "Burmese Top Movie စတိုင် — အထွတ်အထိပ်ခန်းနဲ့စ၊ ပြီးမှ နောက်ကြောင်းပြန်",
        "hook": ("Beat 1 opens IN-MEDIAS-RES on the film's climax confrontation — a gun pointed, "
                 "a betrayal revealed, a shocking death. Dive in within 20 seconds, no greeting. "
                 "Beat 2 rewinds to the chronological beginning."),
        "voice": ("Punchy, high-energy delivery. Short sentences. Drop the most shocking premise "
                  "in the first 2 sentences, then the stakes, then one open loop."),
        "commentary": "Sharp observations and explicit ratings (\"10/10\"); call out clever/dumb character moves.",
        "persona": None,
        "title_hint": "Start the title with the shocking moment: \"…တဲ့အခါ\" spoiler-hook in pure Burmese.",
    },
    "open_loop": {
        "name": "❓ မေးခွန်းနဲ့စ",
        "desc": "TV Myanmar စတိုင် — ဇာတ်ကောင်မိတ်ဆက် → အခက်အခဲ → 'ဘာဆက်ဖြစ်မလဲ'",
        "hook": ("SETUP + OPEN LOOP: beat 1 introduces WHO the protagonist is and their normal life, "
                 "lands the core PROBLEM, and closes on a burning question addressed directly to "
                 "viewers — \"ဘာဆက်ဖြစ်မလဲ, ပရိတ်သတ်တို့ကိုယ်တိုင် ကြည့်ရှုနားဆင်ရမှာပဲ\" energy."),
        "voice": ("Warm storyteller tone. Build curiosity steadily; the first ~90 seconds must establish "
                  "WHO + PROBLEM + the rules/stakes (the deal terms, the deadline, the danger)."),
        "commentary": "Moral framing of each twist; ask the viewer rhetorical questions.",
        "persona": None,
        "title_hint": "Title as a curiosity question in Burmese, e.g. \"…တဲ့အခါ ဘာဆက်ဖြစ်မလဲ?\"",
    },
    "host_kwee": {
        "name": "🎙️ Host စတိုင်",
        "desc": "\"ကွီး\" စတိုင် — ပထမပုဂ္ဂိုလ်၊ ဟာသ၊ 10/10 rating",
        "hook": ("The HOST drops a bold claim or funny question first (\"ကျွန်တော် ဒီကားကို 10/10 ပေးတယ်, "
                 "ဘာလို့လဲဆိုတာ…\"), then dives into the story within 20 seconds."),
        "voice": ("First-person \"ကျွန်တော်\" host voice. Warm, playful, humble — never arrogant. Address "
                  "viewers as \"ပရိတ်သတ်တို့\". React out loud to twists."),
        "commentary": "Rate scenes out loud (\"ဒီအခန်းကို 10/10\"), playful teasing of characters, personal takes 30%+.",
        "persona": "kwee",
        "title_hint": "Playful Burmese title with the host's verdict, e.g. \"10/10 တန် ကား\".",
    },
    "series_host": {
        "name": "📺 စီးရီး Host",
        "desc": "\"ကိုအံ့\" စတိုင် — အပိုင်းလိုက်၊ အရင်အပိုင်း အကျဉ်း + အခန်းသစ်",
        "hook": ("Open with a 30-second recap of the previous episode (\"အရင်အပိုင်းမှာ…\"), then tease "
                 "what this episode covers. Cold open within 20 seconds of the recap."),
        "voice": ("Episodic host energy, \"bro\" warmth. First-person \"ကျွန်တော်\" allowed. End each beat "
                  "with a next-episode tease."),
        "commentary": "Predictions about the next episode, character theories, audience polls (\"မန့်မှာ ပြောခဲ့\").",
        "persona": "koeant",
        "title_hint": "Numbered episode style: \"အပိုင်း(၃)\" + Burmese hook.",
    },
    "horror": {
        "name": "👻 သရဲ/Thriller",
        "desc": "Horror channel စတိုင် — လန့်စရာတည်ဆောက်မှု၊ ဖြည်းဖြည်းချင်း",
        "hook": ("Eerie cold open: describe the most unsettling image or sound first — a whisper, a shadow, "
                 "a cursed object. Dread within 20 seconds, no greeting."),
        "voice": ("Slow-burn, atmospheric. Short ominous sentences. Let silences breathe between revelations. "
                  "Never rush the scare."),
        "commentary": "Minimal jokes. Focus on tension: \"မင်းတို့ဆို ဘာလုပ်မလဲ\" dread questions; explain the curse/rules.",
        "persona": None,
        "title_hint": "Chilling Burmese hook with numbers: \"သရဲ1ကောင်၊ ဝန်ထမ်း2ယောက်…\".",
    },
    "comedy": {
        "name": "😂 ဟာသ",
        "desc": "ဟာသဦးစားပေး — ဇာတ်ကောင်တွေကို စနောက်၊ ပေါ့ပေါ့ပါးပါး",
        "hook": ("Drop the funniest premise first — the most absurd situation in the film — within 20 seconds. "
                 "Make the viewer laugh before they know the plot."),
        "voice": ("Playful, witty. Tease the characters lovingly; sharp one-liners between plot points."),
        "commentary": "Humor-forward 30%+: jokes, memes references, funny ratings of dumb decisions.",
        "persona": None,
        "title_hint": "Funny Burmese title with emoji.",
    },
    "moral": {
        "name": "📖 သင်ခန်းစာ",
        "desc": "အသိအမြင်ဦးစားပေး — သင်ခန်းစာ၊ moral, နက်နက်တွေးစရာ",
        "hook": ("Cold open on the fateful CHOICE — the moment a character decides and seals their fate. "
                 "\"ဒီဆုံးဖြတ်ချက်က သူ့ဘဝကို ပြောင်းလဲသွားစေတယ်…\" within 20 seconds."),
        "voice": ("Reflective, wise storyteller. Clear written Burmese. Pause on the meaning of each turning point."),
        "commentary": "Commentary-heavy 30-35%: moral lessons, life insight (\"အသိအမြင်\"), reflective close on what the story teaches.",
        "persona": None,
        "title_hint": "Thought-provoking Burmese title about the lesson.",
    },
    "docu": {
        "name": "🗓️ Year/Place အဖွင့်",
        "desc": "Million Recap စတိုင် — ခုနှစ်+နေရာ + ပုံမှန်ဘဝ + အပြောင်းအလဲ",
        "hook": ("Documentary cold open: \"[YEAR]ခုနှစ်, [PLACE]…\" — establish normal life in 2 sentences, "
                 "then the DISRUPTION that shatters it. Within 20 seconds."),
        "voice": ("Measured, cinematic, prestige tone. Let the setting breathe; historical weight where it fits."),
        "commentary": "Thematic observations; real-world parallels; restrained ratings.",
        "persona": None,
        "title_hint": "Cinematic Burmese title naming the era/place hook.",
    },
    "neutral": {
        "name": "📰 ရိုးရိုး Recap",
        "desc": "ရိုးရိုးရှင်းရှင်း — တတိယပုဂ္ဂိုလ်၊ ရှင်းလင်းတဲ့ ဇာတ်လမ်းပြောပြချက်",
        "hook": ("Clear premise drop within 20 seconds: who, where, and the trouble — then chronological storytelling."),
        "voice": ("Neutral third-person narrator. Clear written Burmese, steady pace, no slang, no persona."),
        "commentary": "Light 20%: brief sharp observations only; plot stays the star.",
        "persona": None,
        "title_hint": "Simple clear Burmese title: movie concept + hook.",
    },
    "binge": {
        "name": "🎬 အစဆုံး Compilation",
        "desc": "Shine/LIGHT စတိုင် — 1-3 နာရီ အစဆုံး၊ အခန်းလိုက် ပြောပြချက်",
        "hook": ("Sweeping cold open promising the FULL journey — \"အစကနေ အဆုံးထိ…\" — then the first chapter "
                 "within 20 seconds."),
        "voice": ("Steady stamina narration for long watches. Chapter-like beats with mini-recaps (\"ဒီအထိ…\"); "
                  "\"the saga continues\" momentum between chapters."),
        "commentary": "Periodic \"so far\" recaps, arc-level observations, finale ratings.",
        "persona": None,
        "title_hint": "Binge title with \"[ အစဆုံး ]\" marker + Burmese hook.",
    },
    "full_story": {
        "name": "🎞️ ဇာတ်ကားတစ်ကားလုံး ကြည့်ပြီးသလို",
        "desc": "မကြည့်ဘူးတဲ့လူ ဇာတ်ကားတစ်ကားလုံး ကြည့်ပြီးသလို ခံစားရအောင် — အစကနေ အဆုံးထိ စွဲဆောင်မှုရှိရှိ",
        "hook": ("Promise the FULL journey within 20 seconds: the viewer will feel they have lived this entire movie. "
                 "Cold open on the most EMOTIONALLY powerful moment — not just shocking, but a moment that makes "
                 "the viewer FEEL something."),
        "voice": ("Cinematic immersion: no plot holes — every major twist, betrayal, sacrifice, and revelation is "
                  "covered so a first-time viewer understands everything. Concrete sensory language, never abstract "
                  "summary (not \"he was sad\" but what his hands do, what her eyes hide). Character motivations "
                  "always clear: say WHY they act, not just what they do. Vary rhythm with the story's pulse."),
        "commentary": "Emotional insight 25%: why this moment hits hard, what the character truly feels. The story's emotions are the star.",
        "persona": None,
        "title_hint": "Burmese title promising the complete experience: \"[ အစဆုံး ]\" + emotional hook.",
    },
    "dub_dialog": {
        "name": "🎭 ဒိုင်ယာလော့ + ဇာတ်ပြော",
        "desc": "ဇာတ်ကောင်ပြောတဲ့အတိုင်း မြန်မာလို dub, စကားမပြောတဲ့အခန်း narration",
        "hook": ("Open mid-conversation: a striking line of dialogue, dubbed into Burmese, drops the viewer straight "
                 "into the drama within 20 seconds — then the narrator catches them up on who is speaking."),
        "voice": ("Hybrid dub + narration (see DUB MODE). Dialogue in the character's voice, story carried by narration. "
                  "Translated lines must feel alive — the way people really talk."),
        "commentary": "Light 15%: brief context between dramatic moments; never talk over the drama.",
        "persona": None,
        "dub": True,
        "title_hint": "Burmese title with a memorable quoted line from the film.",
    },
    "mystery": {
        "name": "🔍 လျှို့ဝှက်ချက်",
        "desc": "Twist/လျှို့ဝှက်ဇာတ်ကားတွေအတွက် — သဲလွန်စတွေချ၊ twist ကို သိမ်းထား",
        "hook": ("Open with the impossible question at the heart of the film — the thing that makes no sense YET. "
                 "Promise the answer is coming, within 20 seconds."),
        "voice": ("Detective-storytelling: plant clues visibly, withhold the reveal, revisit clues before the twist "
                  "lands. Let the viewer theorize with you."),
        "commentary": "25%: clue-spotting, theories, \"did you catch it?\" moments; explain the twist's logic after the reveal.",
        "persona": None,
        "title_hint": "Burmese mystery-hook title ending with \"?\".",
    },
    "speedrun": {
        "name": "⏩ အမြန်ဇာတ်လမ်း",
        "desc": "ဇာတ်လမ်းချည်းပဲ အမြန်ဆုံး — အတိုချုပ် သိချင်သူတွေအတွက်",
        "hook": "Maximum velocity: premise + stakes + first twist inside 20 seconds. No throat-clearing, ever.",
        "voice": ("Relentless plot density. One event per sentence. Cut every word that is not story. "
                  "Still complete — just fast."),
        "commentary": "Minimal 10%: only the sharpest one-liners; plot is everything.",
        "persona": None,
        "title_hint": "Punchy short Burmese title.",
    },
}

STYLE_ORDER = ["full_story", "cold_open", "dub_dialog", "open_loop", "mystery",
               "host_kwee", "series_host", "horror", "comedy", "speedrun",
               "moral", "docu", "neutral", "binge"]

DEFAULT_STYLE = "full_story"


def get_styles() -> list:
    """Public style list for the API/UI: [{id, name, desc}]."""
    return [{"id": sid, "name": RECAP_STYLES[sid]["name"], "desc": RECAP_STYLES[sid]["desc"]}
            for sid in STYLE_ORDER]


def _style_block(style_id: str | None) -> dict:
    st = RECAP_STYLES.get(style_id or "") or RECAP_STYLES[DEFAULT_STYLE]
    return st


# Optional host personas (R5): first-person "ကျွန်တော်" is allowed ONLY when one is set.
PERSONA_LINES = {
    "kwee": 'HOST PERSONA "ကွီး": warm, playful host. You MAY speak in first person ("ကျွန်တော်"), address viewers as "ပရိတ်သတ်တို့", and rate scenes out loud ("ဒီဇာတ်လမ်းကို 10/10 ပေးပါတယ်"). Stay warm and humble — never arrogant.',
    "koeant": 'HOST PERSONA "ကိုအံ့": episodic series host with "bro" warmth. You MAY speak in first person ("ကျွန်တော်"). Open each episode block with a quick "အရင်အပိုင်းမှာ…" recap, tease the next episode at every beat end, invite comments ("မန့်မှာ ပြောခဲ့").',
}


def _persona_line(persona: str | None) -> str:
    return PERSONA_LINES.get(persona or "", "")


def write_beats(client: GeminiClient, bible: dict, beats: list,
                batch_size: int = 6, max_workers: int = 3,
                persona: str | None = None, style: str | None = None) -> list:
    """Write narrations in parallel batches. Returns beats with 'narration'."""
    batches = [beats[i:i + batch_size] for i in range(0, len(beats), batch_size)]
    bible_s = json.dumps(bible, ensure_ascii=False)[:3000]
    st = _style_block(style)
    # style's persona wins; explicit persona arg is the fallback (back-compat)
    persona_line = _persona_line(st.get("persona") or persona)
    dub_line = (DUB_INSTRUCTION if st.get("dub") else "")

    def _one(batch):
        slim = [{k: b.get(k) for k in ("idx", "goal", "hook_line", "payoff", "target_words")}
                for b in batch]
        out = client.generate(BEATS_PROMPT.format(
            beats_json=json.dumps(slim, ensure_ascii=False),
            bible=bible_s, rules=QUALITY_RULES, persona_line=persona_line,
            style_name=st["name"], style_desc=st["desc"],
            style_voice=st["voice"], style_commentary=st["commentary"],
            dub_line=dub_line))
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
                 progress_cb=None, persona: str | None = None,
                 style: str | None = None) -> dict:
    """Full script pipeline: bible → outline → beats. Returns {bible, beats}."""
    n_beats = max(4, int(target_min / 3))
    if progress_cb:
        progress_cb("bible", 0, 1)
    bible = write_bible(client, ev)
    if progress_cb:
        progress_cb("outline", 0, 1)
    beats = write_outline(client, bible, ev, target_min, n_beats, style=style)
    if progress_cb:
        progress_cb("beats", 0, len(beats))
    beats = write_beats(client, bible, beats, persona=persona, style=style)
    if progress_cb:
        progress_cb("beats", len(beats), len(beats))
    return {"bible": bible, "beats": beats, "target_min": target_min,
            "style": (style or DEFAULT_STYLE)}
