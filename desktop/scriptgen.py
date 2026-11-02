"""Gemini chunk analysis -> one recap script for the whole movie."""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import time
from pathlib import Path

import config
import db
import gemini_client
import ingest
import languages

TAG_RE = re.compile(
    r"^\[(N|D)\s*"
    r"([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)?\s*"
    r"(?:[-–]\s*([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?))?\]\s*"
    r"(.*)$",
    re.DOTALL,
)

# speaking-rate estimates; the TTS pass measures real durations
CHARS_PER_SEC = {"my": 5.2, "th": 5.4, "km": 5.2, "zh": 4.6, "ja": 5.4, "hi": 5.0}
WORDS_PER_SEC = 2.6  # en / ms / vi / id


def _to_sec(value: str | None) -> float | None:
    if not value:
        return None
    parts = [p for p in value.strip().split(":") if p != ""]
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 1:
        return nums[0]
    return None


def _mmss(seconds: float) -> str:
    seconds = max(0, float(seconds or 0))
    m, s = divmod(int(round(seconds)), 60)
    return f"{m:02d}:{s:02d}"


def _hms(seconds: float) -> str:
    seconds = max(0, float(seconds or 0))
    h, rem = divmod(int(round(seconds)), 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def _unit(language: str) -> tuple[str, float]:
    """(unit word, units per second) for length instructions."""
    if language == "my":
        return "syllables", CHARS_PER_SEC["my"]
    if language in ("th", "km", "zh", "ja", "hi"):
        return "characters", CHARS_PER_SEC.get(language, 5.2)
    return "words", WORDS_PER_SEC


def estimate_seconds(text: str, language: str) -> float:
    text = (text or "").strip()
    if not text:
        return 0.0
    if language in CHARS_PER_SEC:
        units = len(re.sub(r"\s+", "", text))
        return round(units / CHARS_PER_SEC[language], 2)
    words = len([w for w in re.split(r"\s+", text) if w])
    return round(words / WORDS_PER_SEC, 2)


def build_prompt(
    style: str,
    clip_sec: float,
    target_pct: int,
    language: str,
    chunk_index: int,
    chunk_total: int,
    ratio: str,
) -> str:
    meta = config.STYLES[style]
    raw = (config.PROMPTS_DIR / meta["prompt"]).read_text(encoding="utf-8")
    lang = languages.get_language(language) or languages.get_language("my")
    lang_name = (lang or {}).get("label_en", "Burmese")
    target = max(1.0, clip_sec * target_pct / 100.0)
    unit, per_sec = _unit(language)
    total_units = max(20, int(round(target * per_sec)))
    part_len = clip_sec / 10.0
    part_units = max(5, total_units // 10)

    if style == "style_1_recap":
        raw = re.sub(
            r"Source video length: \d{1,2}:\d{2}\.",
            f"Clip length: {_mmss(clip_sec)}.",
            raw,
        )
        raw = re.sub(
            r"The voice must be between .*?do not summarise\.",
            (
                f"The voice must be between {_mmss(target * 0.92)} and {_mmss(target * 1.08)} "
                f"long when read aloud — aim for about {_mmss(target)}, about {total_units} "
                f"{unit} in total (narration and direct lines together). Shorter is wrong: do not summarise."
            ),
            raw,
            flags=re.DOTALL,
        )
        raw = re.sub(
            r"Longer is also wrong: .*?instead of adding more lines\.",
            (
                f"Longer is also wrong: a voice longer than {_mmss(target * 1.12)} runs past the clip. "
                f"Never write more than {int(total_units * 1.08)} {unit} in total — tell the story in "
                f"fewer, fuller sentences instead of adding more lines."
            ),
            raw,
            flags=re.DOTALL,
        )
        parts = "\n".join(
            f"  Part {i + 1}: {_mmss(i * part_len)}–{_mmss((i + 1) * part_len)}"
            for i in range(10)
        )
        block = (
            f"Split the clip into 10 equal parts of about {int(round(part_len))} seconds:\n"
            f"{parts}\n"
            f"- Write exactly one group of tagged lines per part, in order, about {part_units} "
            f"{unit} each. Each group tells only what happens in its own part. Every part gets "
            f"about the same amount of voice — do not spend extra words on the beginning and rush the end."
        )
        raw = re.sub(
            r"Split the video into 10 equal parts.*?do not spend extra words on the beginning and rush the end\.",
            block,
            raw,
            flags=re.DOTALL,
        )
    else:
        raw = re.sub(
            r"Watch the whole video \(05:00 long\)",
            f"Watch this clip ({_mmss(clip_sec)} long)",
            raw,
        )

    raw = raw.replace("vertical 9:16 video", f"{ratio} video")
    raw = re.sub(r"\n  - Burmese rules:\n(?:    - .*\n)+", "\n  - Captions: split long lines at natural pauses so each caption stays short.\n", raw)

    if language != "my":
        raw = raw.replace("Burmese (Myanmar Unicode)", lang_name)
        raw = raw.replace("Burmese", lang_name)

    header = (
        f"CONTEXT: this is clip {chunk_index + 1} of {chunk_total} of a movie recap. "
        f"Write only what happens inside this clip. [D] timestamps are relative to the "
        f"start of this clip (00:00 is the first frame you see), and the clip is "
        f"{_mmss(clip_sec)} long. Respond with the tagged lines only.\n\n"
    )
    return header + raw.strip()


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = text.strip(" \"'“”‘’-–—•")
    return text[:600]


def parse_lines(raw: str, language: str) -> list[dict]:
    """Parse `[N]` / `[D mm:ss-mm:ss]` lines into segments (clip-relative)."""
    segs: list[dict] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or not line.startswith("["):
            continue
        m = TAG_RE.match(line)
        if not m:
            continue
        kind_tag, t1, t2, text = m.groups()
        text = _clean_text(text)
        if not text:
            continue
        kind = "dialogue" if kind_tag == "D" else "narration"
        start, end = _to_sec(t1), _to_sec(t2)
        if kind == "dialogue" and start is not None:
            if end is None or end <= start:
                end = start + max(1.5, estimate_seconds(text, language))
        else:
            start = end = None
        segs.append({
            "kind": kind,
            "text": text,
            "rel_start": start,
            "rel_end": end,
            "est_sec": estimate_seconds(text, language),
        })
    return segs


def _assign_times(segs: list[dict], clip_sec: float, offset: float, language: str) -> None:
    """Give every line a source window inside this clip."""
    cursor = 0.0
    i = 0
    while i < len(segs):
        seg = segs[i]
        if seg["kind"] == "dialogue":
            start = max(0.0, min(clip_sec, seg.get("rel_start") or 0.0))
            end = max(start + 0.5, min(clip_sec, seg.get("rel_end") or start + 2.0))
            seg["rel_start"], seg["rel_end"] = start, end
            cursor = max(cursor, end)
            i += 1
            continue

        # a run of narration lines fills the gap up to the next dialogue line
        j = i
        while j < len(segs) and segs[j]["kind"] == "narration":
            j += 1
        boundary = clip_sec
        for nxt in segs[j:]:
            if nxt["kind"] == "dialogue" and nxt.get("rel_start") is not None:
                boundary = min(clip_sec, max(cursor, nxt["rel_start"]))
                break
        run = segs[i:j]
        if boundary <= cursor + 0.05:
            boundary = min(clip_sec, cursor + 0.6 * len(run))
        span = max(0.6 * len(run), boundary - cursor)
        weight = sum(max(0.5, s["est_sec"]) for s in run) or 1.0
        pos = cursor
        for s in run:
            share = max(0.5, s["est_sec"]) / weight * span
            s["rel_start"] = pos
            s["rel_end"] = pos + share
            pos += share
        cursor = max(cursor, pos)
        i = j

    for s in segs:
        s["src_start"] = round(offset + s["rel_start"], 3)
        s["src_end"] = round(offset + s["rel_end"], 3)


def _fake_chunk(index: int, clip_sec: float, target_pct: int, language: str) -> str:
    """Deterministic stand-in used by LONGMOVIE_FAKE_GEMINI=1."""
    unit, per_sec = _unit(language)
    samples = {
        "my": "ဒီအခန်းမှာ ဇာတ်ကောင်တွေ ရင်ခုန်စရာ အခြေအနေတွေ ကြုံတွေ့နေတယ်၊ သူတို့ ဘာလုပ်မလဲ ဆက်ကြည့်ရအောင်၊ သူက စိတ်ထဲ ကြောက်နေပေမယ့် မျက်နှာကို မပြဘူး၊ အသံတောင် တုန်နေတယ်၊ နောက်ဆုံးတော့ သူတို့ နှစ်ယောက် ရင်ဆိုင်လိုက်ရတယ်၊ စကားတွေ ပြင်းထန်လာတယ်၊",
        "th": "ในฉากนี้ ตัวละครต้องเผชิญกับช่วงเวลาสำคัญ และทุกอย่างกำลังเปลี่ยนไปอย่างรวดเร็ว",
        "km": "នៅក្នុងឈុតនេះ តួអង្គបានជួបប្រទះពេលវេលាសំខាន់ ហើយរឿងរ៉ាវកំពុងផ្លាស់ប្តូរ",
        "zh": "在这一场里，人物面对重要的时刻，事情开始发生变化，所有人都必须做出选择。",
        "ja": "この場面で登場人物たちは重大な局面に直面し、物語が大きく動き出します。",
        "hi": "इस दृश्य में किरदार एक अहम पल का सामना करते हैं और कहानी बदलने लगती है।",
        "en": "In this scene the characters face a turning point, and everything starts to change as the story moves forward.",
        "ms": "Dalam babak ini watak-watak menghadapi detik penting dan cerita mula berubah.",
        "vi": "Trong cảnh này, các nhân vật đối mặt với một khoảnh khắc quan trọng và câu chuyện bắt đầu thay đổi.",
        "id": "Di adegan ini para tokoh menghadapi momen penting dan cerita mulai berubah.",
    }
    sample = samples.get(language, samples["en"])

    def fit(seconds: float) -> str:
        """Text whose estimated speaking time is about `seconds`."""
        if seconds <= 0.05:
            return ""
        if language in CHARS_PER_SEC:
            n = max(2, int(round(seconds * per_sec)))
            body = (sample * 12)[:n]
            body = body.rstrip("၊ ။ ,. 。")
            suffix = {"my": "၊", "zh": "。", "ja": "。", "hi": "।"}.get(language, ".")
            return body + suffix
        n = max(1, int(round(seconds * per_sec)))
        words = (sample.split() * 80)[:n]
        return " ".join(words).rstrip(",. ") + "."

    out: list[str] = []
    parts = max(2, min(10, int(clip_sec // 50) or 2))
    part = max(0.5, clip_sec / parts)
    budget = clip_sec * target_pct / 100.0
    per_part = budget / parts
    for i in range(parts):
        start = i * part
        if start >= clip_sec:
            break
        end = min(clip_sec, start + part)
        dialogue = i % 3 == 1
        narr = per_part * (0.68 if dialogue else 1.0)
        text = fit(narr)
        if text:
            out.append(f"[N] {text}")
        if dialogue:
            d0 = start + part * 0.25
            d1 = min(end, d0 + max(0.8, per_part * 0.32))
            line = fit(per_part * 0.32)
            if line:
                out.append(f"[D {_mmss(d0)}-{_mmss(d1)}] {line}")
        out.append("")
    return "\n".join(out)


def script_dir(pid: str) -> Path:
    d = ingest.project_dir(pid) / "script"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# per-chunk pipeline cache
#
# Each 10-minute chunk is handled on its own: Gemini analysis -> Edge TTS ->
# next chunk. Text and audio caches are separate so changing the narrator only
# re-records the voice instead of paying for Gemini again.
# --------------------------------------------------------------------------

PIPELINE_VERSION = 1


def chunks_dir(pid: str) -> Path:
    d = script_dir(pid) / "chunks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _text_cache_path(pid: str, index: int) -> Path:
    return chunks_dir(pid) / f"text_{index:03d}.json"


def _voice_cache_path(pid: str, index: int) -> Path:
    return chunks_dir(pid) / f"voice_{index:03d}.json"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _text_signature(path: Path, index: int, clip_sec: float, offset: float,
                    style: str, language: str, pct: int, ratio: str) -> str:
    try:
        st = path.stat()
        size, mtime = st.st_size, int(st.st_mtime)
    except OSError:
        size, mtime = 0, 0
    payload = {
        "v": PIPELINE_VERSION,
        "index": index,
        "file": path.name,
        "size": size,
        "mtime": mtime,
        "start": round(float(offset), 3),
        "duration": round(float(clip_sec), 3),
        "style": style,
        "language": language,
        "pct": int(pct),
        "ratio": ratio,
    }
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _voice_signature(text_sig: str, voice: str, rate: int, pitch: int) -> str:
    payload = {
        "v": PIPELINE_VERSION,
        "text": text_sig,
        "voice": voice,
        "rate": int(rate),
        "pitch": int(pitch),
    }
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _audio_ok(cache: dict, count: int) -> bool:
    paths = cache.get("audio") or []
    if len(paths) != count or len(cache.get("durations") or []) != count:
        return False
    return all(Path(p).exists() for p in paths)


def load_script(pid: str) -> dict | None:
    path = ingest.project_dir(pid) / "script" / "script.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def run_analysis(pid: str, with_voice: bool = True) -> None:
    """Analyze every chunk and (optionally) voice it right away.

    The pipeline is deliberately sequential per 10-minute chunk:

        chunk 1: Gemini -> Edge TTS
        chunk 2: Gemini -> Edge TTS
        ...
        all chunks: merge narration -> script.json -> timeline -> render

    Gemini retries forever on transient errors (model fallback, wait, retry).
    Text and voice are cached per chunk, so a re-run resumes where it stopped.
    """
    p = db.get_project(pid)
    if not p:
        raise RuntimeError("project not found")
    if p.get("status") != "ready":
        raise RuntimeError("ingest is not ready yet")
    meta = p.get("meta_json") or {}
    chunks = meta.get("chunks") or []
    if not chunks:
        raise RuntimeError("no Gemini chunks — run ingest first")

    settings = p.get("settings_json") or {}
    style = settings.get("style") or "style_1_recap"
    language = settings.get("language") or "my"
    pct = int(settings.get("target_pct") or 50)
    ratio = settings.get("ratio") or "16:9"
    duration = float(p.get("duration") or 0)
    chunk_dir = ingest.project_dir(pid) / "gemini"

    if not gemini_client.ready():
        raise RuntimeError(gemini_client.missing_reason())

    # A new script invalidates any narration made from the previous one.
    clean_meta = dict(meta)
    for key in ("narration_wav", "timeline_json", "srt_path"):
        clean_meta.pop(key, None)
    db.update_project(
        pid, meta_json=clean_meta,
        script_status="analyzing", script_progress=1,
        script_message=f"အပိုင်း {len(chunks)} ခု — တစ်ပိုင်းချင်း Gemini → အသံ",
        script_error="",
        tts_status="queued" if with_voice else "",
        tts_progress=0, tts_error="",
    )
    db.append_log(pid, f"Pipeline စတင် — chunk {len(chunks)} ခု, {language}, {pct}%")

    voice = ""
    rate = pitch = 0
    narrator = settings.get("narrator_id") or "F01"
    tts_mod = None
    if with_voice:
        import tts as tts_mod  # lazy: tts imports scriptgen

        voice, rate, pitch = tts_mod.voice_for(
            language,
            narrator,
            settings.get("voice_rate") or 0,
            settings.get("voice_pitch") or 0,
        )
        db.append_log(pid, f"Voice — {voice}, rate {rate:+d}%, pitch {pitch:+d}Hz")

    total = duration * pct / 100.0
    segments: list[dict] = []
    per_chunk: list[dict] = []
    audio_all: list[Path] = []
    durations_all: list[float] = []
    n = len(chunks)
    resumed = 0

    for i, c in enumerate(chunks):
        clip_sec = float(c.get("duration") or 0)
        offset = float(c.get("start") or 0)
        path = chunk_dir / str(c.get("file") or f"chunk_{i:03d}.mp4")
        if not path.exists():
            raise RuntimeError(f"chunk file missing: {path.name}")

        text_sig = _text_signature(
            path, i, clip_sec, offset, style, language, pct, ratio
        )
        text_cache = _read_json(_text_cache_path(pid, i))
        used_model = text_cache.get("model") or ""
        if text_cache.get("signature") == text_sig and text_cache.get("segments"):
            segs = text_cache["segments"]
            resumed += 1
            db.append_log(
                pid, f"chunk {i + 1}/{n}: စာသား cache ✓ ({len(segs)} ကြောင်း)"
            )
        else:
            prompt = build_prompt(style, clip_sec, pct, language, i, n, ratio)

            def status(msg: str, _i=i) -> None:
                db.update_project(
                    pid,
                    script_message=f"အပိုင်း {_i + 1}/{n} · {msg}",
                    script_progress=round((_i + 0.15) / n * 100, 1),
                )

            if config.FAKE_GEMINI:
                raw = _fake_chunk(i, clip_sec, pct, language)
                used_model = "fake"
            else:
                raw, used_model = gemini_client.analyze_chunk_video(
                    path, prompt, on_status=status, infinite=True
                )
            segs = parse_lines(raw, language)
            if not segs:
                raise RuntimeError(f"chunk {i + 1}: Gemini က စာသား မပြန်ပါ")
            _assign_times(segs, clip_sec, offset, language)
            _write_json(_text_cache_path(pid, i), {
                "version": PIPELINE_VERSION,
                "signature": text_sig,
                "index": i,
                "start": round(offset, 3),
                "duration": round(clip_sec, 3),
                "model": used_model,
                "segments": segs,
            })
            db.append_log(
                pid, f"chunk {i + 1}/{n}: Gemini ပြီး — {len(segs)} ကြောင်း"
            )

        audio_paths: list[Path] = []
        durs: list[float] = []
        if with_voice and tts_mod is not None:
            voice_sig = _voice_signature(text_sig, voice, rate, pitch)
            voice_cache = _read_json(_voice_cache_path(pid, i))
            if voice_cache.get("signature") == voice_sig and _audio_ok(voice_cache, len(segs)):
                audio_paths = [Path(x) for x in voice_cache["audio"]]
                durs = [float(x) for x in voice_cache["durations"]]
                resumed += 1
                db.append_log(pid, f"chunk {i + 1}/{n}: အသံ cache ✓")
                db.update_project(
                    pid,
                    tts_status="synthesizing",
                    tts_progress=round((i + 1) / n * 100, 1),
                    tts_message=f"အပိုင်း {i + 1}/{n} · အသံ cache ✓",
                )
            else:
                out_dir = chunks_dir(pid) / f"chunk_{i:03d}" / "audio"
                shutil.rmtree(out_dir, ignore_errors=True)

                def vprog(frac: float, _i=i) -> None:
                    db.update_project(
                        pid,
                        tts_progress=round((_i + 0.2 + 0.8 * frac) / n * 100, 1),
                        tts_message=f"အပိုင်း {_i + 1}/{n} · အသံထွက် {int(frac * 100)}%",
                    )

                def vretry(attempt: int, delay: float, _exc, _i=i) -> None:
                    db.update_project(
                        pid,
                        tts_message=(
                            f"အပိုင်း {_i + 1}/{n} · Edge TTS ပြန်ကြိုးစား "
                            f"({attempt}) — {int(delay)}s"
                        ),
                    )

                db.update_project(
                    pid,
                    tts_status="synthesizing",
                    tts_progress=round((i + 0.2) / n * 100, 1),
                    tts_message=f"အပိုင်း {i + 1}/{n} · အသံထွက်နေသည်...",
                )
                audio_paths, durs = tts_mod.synth_chunk(
                    segs, voice, rate, pitch, out_dir,
                    on_progress=vprog, on_retry=vretry,
                )
                _write_json(_voice_cache_path(pid, i), {
                    "version": PIPELINE_VERSION,
                    "signature": voice_sig,
                    "audio": [str(x) for x in audio_paths],
                    "durations": durs,
                })
                db.append_log(
                    pid, f"chunk {i + 1}/{n}: အသံပြီး — {len(audio_paths)} ကြောင်း"
                )

        for s in segs:
            s["chunk"] = i
        segments.extend(segs)
        audio_all.extend(audio_paths)
        durations_all.extend(durs)
        per_chunk.append({
            "index": i,
            "start": round(offset, 3),
            "duration": round(clip_sec, 3),
            "lines": len(segs),
            "model": used_model,
        })
        db.update_project(
            pid, script_progress=round((i + 1) / n * 100, 1),
            script_message=f"အပိုင်း {i + 1}/{n} ပြီး — {len(segs)} ကြောင်း",
        )
        db.append_log(pid, f"chunk {i + 1}/{n}: {len(segs)} ကြောင်း")

    # stable ids + speaking estimate
    for num, s in enumerate(segments, 1):
        s["id"] = f"s{num:04d}"
        s["est_sec"] = estimate_seconds(s["text"], language)
        s.pop("rel_start", None)
        s.pop("rel_end", None)

    est = round(sum(s["est_sec"] for s in segments), 2)
    script = {
        "version": 1,
        "project": pid,
        "style": style,
        "language": language,
        "ratio": ratio,
        "target_pct": pct,
        "source_sec": round(duration, 3),
        "target_sec": round(total, 3),
        "est_speech_sec": est,
        "created": time.time(),
        "model": (
            "fake"
            if config.FAKE_GEMINI
            else "+".join(sorted({c.get("model") or "" for c in per_chunk if c.get("model")}))
        ),
        "chunks": per_chunk,
        "segments": segments,
    }

    out_dir = script_dir(pid)
    (out_dir / "script.json").write_text(
        json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    meta = dict((db.get_project(pid) or {}).get("meta_json") or {})
    meta["script_json"] = str(out_dir / "script.json")
    db.update_project(pid, meta_json=meta)

    if with_voice and tts_mod is not None:
        if len(audio_all) != len(segments):
            raise RuntimeError(
                f"voice file count mismatch: {len(audio_all)} files for "
                f"{len(segments)} lines"
            )
        db.update_project(
            pid,
            tts_status="synthesizing",
            tts_progress=95,
            tts_message="အပိုင်းအားလုံး ပြီး — အသံဖိုင်များ ပေါင်းနေသည်...",
        )
        db.append_log(pid, "အပိုင်းအားလုံး ပြီး — narration ပေါင်းနေသည်")
        tts_mod.publish_narration(
            pid, script, ingest.project_dir(pid) / "script" / "tts",
            durations=durations_all,
            voice=voice,
            rate=rate,
            pitch=pitch,
            narrator=narrator,
            language=language,
            target=total,
            tag="chunks",
            raw_paths=audio_all,
        )

    db.update_project(
        pid,
        script_status="ready",
        script_progress=100,
        script_message=(
            f"{len(segments)} ကြောင်း · ခန့်မှန်း {_hms(est)} / target {_hms(total)}"
            " · Setting ချိန်ပြီး Render နှိပ်ပါ"
        ),
        script_error="",
    )
    if resumed:
        db.append_log(pid, f"Pipeline: cache မှ {resumed} အပိုင်း ပြန်သုံးပြီး")
    db.append_log(pid, f"Script ပြီး — {len(segments)} ကြောင်း, ခန့်မှန်း {_hms(est)}")
