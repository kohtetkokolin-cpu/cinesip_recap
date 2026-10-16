"""Cinesip Recap — AutoSync smart clip selection (100% local, free, zero user input).

Problem: narration says one thing, footage shows another ("slideshow effect").
Solution: match each beat's narration to the footage that actually fits —
WHAT is visible (YAMNet audio events + visual features), WHO is on screen
(face clusters auto-named via dialogue co-occurrence), and WHERE in the story.

No Gemini needed for matching. No user labeling. Degrades gracefully to the
old sequential selection when confidence is low (never worse than before).
"""
import re

# --- Burmese keyword -> semantic category -------------------------------
# category: (yamnet classes, visual hints, burmese words)
CATEGORIES = {
    "action": {
        "yamnet": ["Explosion", "Gunshot", "Machine gun", "Scream", "Yell",
                   "Shout", "Thump", "Crash"],
        "visual": {"motion": "high", "energy": "high"},
        "words": ["တိုက်", "သတ်", "ပစ်", "ခတ်", "ပေါက်", "ဖမ်း", "ပြေး", "လိုက်",
                  "ရန်", "စစ်", "သေ", "သွေး", "ဗုံး", "သေနတ်", "ဓား", "ခိုက်",
                  "ဒဏ်ရာ", "တိုက်ပွဲ", "စစ်ပွဲ"],
    },
    "sad": {
        "yamnet": ["Crying", "Sobbing", "Wail", "Moan", "Sigh"],
        "visual": {"motion": "low", "energy": "low", "faces": True},
        "words": ["ငို", "ဝမ်း", "နာ", "ကြေ", "ကွဲ", "လွမ်း", "မျက်ရည်",
                  "ဆုံး", "ထွက်", "ဝမ်းနည်း"],
    },
    "happy": {
        "yamnet": ["Laughter", "Chuckle", "Giggle", "Applause", "Cheering",
                   "Crowd"],
        "visual": {"bright": "high", "faces": True},
        "words": ["ရယ်", "ပျော်", "ချစ်", "မင်္ဂလာ", "ပွဲ", "ဆု",
                  "ပြုံး"],  # NOTE: 'အောင်' omitted — it's usually a name part (ကိုအောင်)
    },
    "tense": {
        "yamnet": ["Scream", "Whispering", "Silence", "Heartbeat"],
        "visual": {"bright": "low"},
        "words": ["ကြောက်", "လန့်", "တိတ်", "ခြိမ်း", "ခြောက်", "သံသယ",
                  "လျှို့", "ဝှက်", "စိုး"],
    },
    "dialogue": {
        "yamnet": ["Speech", "Conversation", "Narration"],
        "visual": {"faces": True, "motion": "low"},
        "words": ["ပြော", "မေး", "ဖြေ", "ဆွေး", "နွေး", "တိုင်ပင်", "ညှိ",
                  "ကတိ", "စကား"],
    },
    "music": {
        "yamnet": ["Music", "Singing", "Song"],
        "visual": {},
        "words": ["သီချင်း", "ဆို", "ဂီတ", "အဆို"],
    },
}

# name pattern: 2-4 Myanmar-syllable words (loose; refined by co-occurrence)
_NAME_RE = re.compile(r"[\u1000-\u102F]{2,}(?:\s[\u1000-\u102F]{2,}){0,2}")


def beat_categories(narration):
    """Return set of semantic categories for a Burmese narration.

    Burmese has no word spaces, so naive substring matching over-triggers
    (e.g. 'ပွဲ' inside 'တိုက်ပွဲ', 'အောင်' inside the name 'ကိုအောင်').
    Fix: blank out likely person names first, then match longest keywords
    first and consume them.
    """
    # blank out probable names so 'အောင်' in 'ကိုအောင်' doesn't count as 'win'
    text = narration
    for m in _NAME_RE.findall(narration):
        if " " in m.strip():
            text = text.replace(m, "\u200b")
    cats = set()
    remaining = text
    all_kw = []
    for cat, d in CATEGORIES.items():
        for w in d["words"]:
            all_kw.append((len(w), w, cat))
    all_kw.sort(reverse=True)
    for _, w, cat in all_kw:
        if w in remaining:
            cats.add(cat)
            remaining = remaining.replace(w, "\u200b")  # consume
    return cats or {"dialogue"}  # default: people talking


def extract_names(beats, transcript):
    """Character names: quoted/capitalized mentions across script+transcript.

    Strategy: collect candidate names from narration (2+ word Myanmar phrases
    appearing 3+ times), ranked by frequency. Cheap, no NER model needed.
    """
    from collections import Counter
    texts = [b.get("narration", "") for b in beats]
    texts += [s.get("text", "") for s in transcript]
    cnt = Counter()
    for t in texts:
        for m in _NAME_RE.findall(t):
            m = m.strip()
            if 2 <= len(m) <= 12:
                cnt[m] += 1
    # keep frequent multi-mention names (likely characters, not common words)
    names = [w for w, c in cnt.most_common(20) if c >= 3 and " " in w]
    return names


def auto_name_clusters(names, clusters, transcript, window=10.0):
    """Link names -> face clusters via mention co-occurrence.

    When name X is spoken/mentioned near time T, which face cluster appears
    at T most often? Returns {name: cluster_id} for confident mappings only.
    Conservative: unconfident names stay anonymous (never force a wrong label).
    """
    if not names or not clusters:
        return {}
    # mention times per name (from transcript)
    mention = {n: [] for n in names}
    for s in transcript:
        txt = s.get("text", "")
        for n in names:
            if n in txt:
                mention[n].append(float(s.get("start", 0)))

    mapping = {}
    for n, times in mention.items():
        if len(times) < 3:
            continue
        best_cid, best_score, second = -1, 0.0, 0.0
        for c in clusters:
            ctimes = c.get("times", [])
            if not ctimes:
                continue
            hits = sum(1 for mt in times
                       if any(abs(ct - mt) <= window for ct in ctimes))
            score = hits / len(times)
            if score > best_score:
                second, best_score, best_cid = best_score, score, c["id"]
        # confident only: >50% co-occurrence AND clearly better than runner-up
        if best_score >= 0.5 and best_score - second >= 0.2:
            mapping[n] = best_cid
    return mapping


def _scene_features(scene, evidence):
    """Visual features for a scene dict (cached in evidence)."""
    return scene.get("feat") or {}


def score_scene(beat, scene, evidence, name_to_cluster):
    """Simple retrieval score: higher = better match. Explainable."""
    s, e = scene["start"], scene["end"]
    narration = beat.get("narration", "")
    cats = beat_categories(narration)
    score, reasons = 0.0, []

    # 1. character match (strongest): named person visible in scene
    for name, cid in name_to_cluster.items():
        if name in narration:
            ctimes = next((c["times"] for c in
                           evidence.get("face_clusters", [])
                           if c["id"] == cid), [])
            if any(s - 2 <= t <= e + 2 for t in ctimes):
                score += 3.0
                reasons.append(f"{name} visible")

    # 2. audio event match (YAMNet timeline)
    tl = evidence.get("audio_events", {}).get("timeline", [])
    ev_here = set()
    for w in tl:
        if s - 3 <= w["t"] <= e + 3:
            ev_here.update(ev for ev, _ in w["events"])
    for cat in cats:
        want = set(CATEGORIES[cat]["yamnet"])
        hit = want & ev_here
        if hit:
            score += 2.0
            reasons.append(f"hears {sorted(hit)[0].lower()}")

    # 3. visual feature match
    feat = _scene_features({"start": s, "end": e}, evidence)
    for cat in cats:
        vh = CATEGORIES[cat]["visual"]
        ok = True
        if vh.get("motion") == "high" and feat.get("motion", 0.5) < 0.6:
            ok = False
        if vh.get("motion") == "low" and feat.get("motion", 0.5) > 0.4:
            ok = False
        if vh.get("bright") == "high" and feat.get("bright", 0.5) < 0.55:
            ok = False
        if vh.get("bright") == "low" and feat.get("bright", 0.5) > 0.45:
            ok = False
        if ok and vh:
            score += 1.0
            reasons.append(cat)

    # 4. chronological anchor: prefer scenes inside the beat's footage window
    fs, fe = beat.get("footage_start", 0), beat.get("footage_end", 0)
    if fs <= s and e <= fe:
        score += 1.5
    elif not (e < fs or s > fe):
        score += 0.5  # partial overlap
    else:
        # outside window: allowed only if it scores well on content
        score -= 1.0

    return score, reasons


def smart_select(beat, evidence, name_to_cluster, max_clip_sec=5.0):
    """Return ranked clip list for a beat: [{start,end,reasons,score}].

    Falls back to [] when nothing scores (caller uses sequential then).
    """
    scenes = evidence.get("scenes", [])
    if not scenes:
        return []
    ranked = []
    for sc in scenes:
        sc_score, reasons = score_scene(beat, sc, evidence, name_to_cluster)
        if sc_score > 1.0:  # needs at least some positive signal
            ranked.append({"start": sc["start"], "end": sc["end"],
                           "score": round(sc_score, 2), "reasons": reasons})
    ranked.sort(key=lambda r: -r["score"])
    # take top scenes, cut into <=max_clip_sec clips, keep story order
    clips = []
    for r in ranked[:6]:
        d = r["end"] - r["start"]
        t = r["start"]
        while d > 0.1 and len(clips) < 8:
            cd = min(d, max_clip_sec)
            clips.append({"start": round(t, 2), "end": round(t + cd, 2),
                          "reasons": r["reasons"], "score": r["score"]})
            t += cd
            d -= cd
    clips.sort(key=lambda c: c["start"])  # chronological: no story scrambling
    return clips
