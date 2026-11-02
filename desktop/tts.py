"""Edge TTS narration: synthesize every script line, fit the target length,
then build one narration track plus a matching SRT timeline."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from pathlib import Path

import config
import db
import ffmpeg_utils as ff
import ingest
import languages
import scriptgen
import voices


def voice_for(language: str, narrator_id: str, rate_offset: float = 0.0,
              pitch_offset: float = 0.0) -> tuple[str, int, int]:
    """Return (edge voice, rate percent, pitch Hz).

    The narrator profile gives the base; `voice_rate` / `voice_pitch` are the
    user's fine-tuning offsets from the settings panel.
    """
    prof = voices.get_voice(narrator_id) or voices.get_voice("F01")
    lang = languages.get_language(language) or languages.get_language("my")
    base_rate = int(round((float(prof.get("rate") or 1.0) - 1.0) * 100))
    pitch = int(prof.get("pitch") or 0)
    if language == "my":
        voice = str(prof.get("base") or "my-MM-NilarNeural")
    else:
        key = "edge_female" if prof.get("gender") == "female" else "edge_male"
        voice = str((lang or {}).get(key) or "my-MM-NilarNeural")
        pitch = max(-10, min(10, pitch))
    base_rate = int(round(base_rate + float(rate_offset or 0)))
    pitch = int(round(pitch + float(pitch_offset or 0)))
    base_rate = max(config.TTS_RATE_MIN, min(config.TTS_RATE_MAX, base_rate))
    pitch = max(-50, min(50, pitch))
    return voice, base_rate, pitch


async def _synth_one(text: str, voice: str, rate: int, pitch: int, out: Path) -> None:
    import edge_tts  # lazy: fake mode does not need it

    communicate = edge_tts.Communicate(
        text, voice, rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz"
    )
    await communicate.save(str(out))


async def _synth_batch(jobs: list[dict], on_done=None, max_attempts: int | None = 3,
                       on_retry=None) -> None:
    """Synthesize a batch; `max_attempts=None` keeps retrying until it works."""
    sem = asyncio.Semaphore(4)

    async def one(job: dict) -> None:
        async with sem:
            attempt = 0
            last: Exception | None = None
            while True:
                attempt += 1
                try:
                    await _synth_one(job["text"], job["voice"], job["rate"], job["pitch"], job["out"])
                    break
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    if max_attempts is not None and attempt >= max_attempts:
                        raise RuntimeError(f"Edge TTS failed: {last}") from exc
                    delay = min(20.0, 1.2 * attempt)
                    if on_retry:
                        on_retry(attempt, delay, exc)
                    await asyncio.sleep(delay)
            if on_done:
                on_done()

    await asyncio.gather(*(one(j) for j in jobs))


def _silence(out: Path, seconds: float) -> None:
    ff.run_ffmpeg(
        ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{max(0.05, seconds):.3f}",
         "-c:a", "pcm_s16le", str(out)],
        label="silence",
    )


def _fake_voice(out: Path, seconds: float, freq: float = 220.0) -> None:
    """Quiet tone standing in for speech in fake mode.

    Real Edge TTS is never digital silence, and an all-silent AAC track
    encodes as empty packets that Chromium's media player refuses to decode.
    A low tone keeps the fake pipeline testable end to end, including playback
    of the rendered output in the browser.
    """
    ff.run_ffmpeg(
        ["-f", "lavfi", "-i", f"sine=frequency={freq:.0f}:sample_rate=44100",
         "-t", f"{max(0.05, seconds):.3f}", "-af", "volume=0.05",
         "-c:a", "pcm_s16le", str(out)],
        label="fake voice",
    )


def _norm_wav(src: Path, out: Path) -> None:
    ff.run_ffmpeg(
        ["-i", str(src), "-vn", "-ac", "1", "-ar", str(config.TTS_SAMPLE_RATE),
         "-c:a", "pcm_s16le", str(out)],
        label="tts normalize",
    )


def _duration(path: Path) -> float:
    try:
        return float(ff.duration_seconds(path))
    except Exception:
        return 0.0


def _synth_pass(pid: str, script: dict, voice: str, rate: int, pitch: int,
                work: Path, tag: str, on_progress) -> list[float]:
    """Synthesize every segment once; return measured durations."""
    segs = script["segments"]
    raw_dir = work / f"raw_{tag}"
    shutil.rmtree(raw_dir, ignore_errors=True)
    shutil.rmtree(work / f"norm_{tag}", ignore_errors=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for seg in segs:
        ext = ".wav" if config.FAKE_TTS else ".mp3"
        jobs.append({
            "text": seg["text"],
            "voice": voice,
            "rate": rate,
            "pitch": pitch,
            "out": raw_dir / f"{seg['id']}{ext}",
        })

    if config.FAKE_TTS:
        for job in jobs:
            seg = next(s for s in segs if s["id"] == job["out"].stem)
            base = max(0.4, float(seg.get("est_sec") or 1.0))
            # simulate Edge's rate control so the fitting pass is testable
            _fake_voice(job["out"], base * 100.0 / max(40.0, 100.0 + rate))
    else:
        done = {"n": 0}

        def tick() -> None:
            done["n"] += 1
            if on_progress and len(jobs):
                on_progress(done["n"] / len(jobs))

        asyncio.run(_synth_batch(jobs, on_done=tick))

    durations: list[float] = []
    for job in jobs:
        durations.append(round(_duration(job["out"]), 3))
    return durations


def synth_chunk(segs: list[dict], voice: str, rate: int, pitch: int, out_dir: Path,
                on_progress=None, on_retry=None) -> tuple[list[Path], list[float]]:
    """Synthesize one Gemini chunk's lines and return (files, durations).

    Used by the chunk pipeline: part 1 analysis -> part 1 voice -> part 2...
    Edge TTS is retried forever here, so a flaky network cannot stop the run.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = ".wav" if config.FAKE_TTS else ".mp3"
    paths = [out_dir / f"line_{i:04d}{ext}" for i in range(len(segs))]
    if not paths:
        return [], []

    if config.FAKE_TTS:
        for i, (seg, path) in enumerate(zip(segs, paths)):
            base = max(0.4, float(seg.get("est_sec") or 1.0))
            _fake_voice(path, base * 100.0 / max(40.0, 100.0 + rate))
            if on_progress and len(paths):
                on_progress((i + 1) / len(paths))
    else:
        jobs = [
            {
                "text": seg["text"],
                "voice": voice,
                "rate": rate,
                "pitch": pitch,
                "out": path,
            }
            for seg, path in zip(segs, paths)
        ]
        done = {"n": 0}

        def tick() -> None:
            done["n"] += 1
            if on_progress and len(jobs):
                on_progress(done["n"] / len(jobs))

        asyncio.run(_synth_batch(jobs, on_done=tick, max_attempts=None, on_retry=on_retry))

    durations = [round(_duration(path), 3) for path in paths]
    return paths, durations


def _build_track(pid: str, script: dict, work: Path, tag: str,
                 durations: list[float],
                 raw_paths: list[Path] | None = None) -> tuple[Path, Path, float]:
    segs = script["segments"]
    gap = max(0.0, float(config.TTS_GAP_SEC))
    norm_dir = work / f"norm_{tag}"
    norm_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = work / f"raw_{tag}"

    files: list[Path] = []
    ext = ".wav" if config.FAKE_TTS else ".mp3"
    for idx, (seg, dur) in enumerate(zip(segs, durations)):
        if raw_paths is not None:
            src = Path(raw_paths[idx])
        else:
            src = raw_dir / f"{seg['id']}{ext}"
        out = norm_dir / f"{seg['id']}.wav"
        _norm_wav(src, out)
        files.append(out)

    gap_file = work / "gap.wav"
    if gap > 0 and not gap_file.exists():
        _silence(gap_file, gap)

    list_file = work / "concat.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for i, path in enumerate(files):
            f.write("file '" + str(path).replace("'", "'\\''") + "'\n")
            if gap > 0 and i < len(files) - 1:
                f.write("file '" + str(gap_file).replace("'", "'\\''") + "'\n")

    narration = work / "narration.wav"
    ff.run_ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", str(list_file), "-ac", "1",
         "-ar", str(config.TTS_SAMPLE_RATE), "-c:a", "pcm_s16le", str(narration)],
        label="narration concat",
    )

    actual = _duration(narration)
    if actual <= 0:
        actual = sum(durations) + gap * max(0, len(durations) - 1)
    return narration, norm_dir, actual


def _caption_chunks(text: str, language: str, limit: int = 58) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    parts = re.split(r"(?<=[၊။.!?])\s*", text)
    out: list[str] = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not current:
            current = part
        elif len(current) + 1 + len(part) <= limit:
            current += " " + part
        else:
            out.append(current)
            current = part
    if current:
        out.append(current)
    # hard-wrap anything still too long
    final: list[str] = []
    for piece in out:
        while len(piece) > limit * 2:
            final.append(piece[: limit * 2])
            piece = piece[limit * 2:]
        final.append(piece)
    return final


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    ms = int(round((seconds - int(seconds)) * 1000))
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(script: dict, timeline: list[dict], out: Path) -> None:
    language = script.get("language") or "my"
    blocks: list[str] = []
    n = 0
    for seg in timeline:
        start = float(seg["audio_start"])
        end = float(seg["audio_end"])
        text = seg.get("text") or ""
        caps = _caption_chunks(text, language)
        if not caps:
            continue
        weight = sum(len(c) for c in caps) or 1
        pos = start
        for cap in caps:
            share = (end - start) * (len(cap) / weight)
            n += 1
            blocks.append(f"{n}\n{_srt_time(pos)} --> {_srt_time(min(end, pos + max(0.6, share)))}\n{cap}\n")
            pos += share
    out.write_text("\n".join(blocks), encoding="utf-8", newline="\n")


def publish_narration(pid: str, script: dict, work: Path, *,
                      durations: list[float], voice: str, rate: int, pitch: int,
                      narrator: str, language: str, target: float,
                      tag: str = "a",
                      raw_paths: list[Path] | None = None) -> dict:
    """Merge per-line audio into narration.wav + timeline.json + SRT.

    Shared by the manual voice pass (`run_tts`) and the per-chunk pipeline, so
    both produce exactly the same files and metadata.
    """
    narration, norm_dir, actual = _build_track(pid, script, work, tag, durations, raw_paths)

    # Final length control: gently time-stretch the whole track when it is close
    # enough that atempo stays transparent. Pitch is preserved.
    stretch = 1.0
    if target > 0 and actual > 0:
        raw_ratio = actual / target
        ratio = min(1.5, max(0.85, raw_ratio))
        if abs(ratio - 1.0) > 0.03:
            db.update_project(pid, tts_progress=92,
                              tts_message=f"အရှည် ညှိနေသည် — atempo {ratio:.2f}x")
            fitted = work / "narration_fit.wav"
            ff.run_ffmpeg(
                ["-i", str(narration), "-filter:a", f"atempo={ratio:.4f}",
                 "-ac", "1", "-ar", str(config.TTS_SAMPLE_RATE), "-c:a", "pcm_s16le", str(fitted)],
                label="narration fit",
            )
            fitted_dur = _duration(fitted)
            if fitted_dur > 0:
                narration = fitted
                durations = [d / ratio for d in durations]
                actual = fitted_dur
                stretch = ratio
                note = " (clamped)" if abs(raw_ratio - ratio) > 0.01 else ""
                db.append_log(
                    pid, f"အရှည်ညှိ — atempo {ratio:.3f}x{note} → {ff.fmt_duration(actual)}"
                )

    cursor = 0.0
    gap_step = config.TTS_GAP_SEC / stretch if stretch else config.TTS_GAP_SEC
    timeline: list[dict] = []
    for seg, dur in zip(script["segments"], durations):
        line = {
            "id": seg["id"],
            "kind": seg.get("kind"),
            "text": seg.get("text"),
            "src_start": seg.get("src_start"),
            "src_end": seg.get("src_end"),
            "audio_start": round(cursor, 3),
            "audio_end": round(cursor + dur, 3),
            "duration": round(dur, 3),
            "file": str(norm_dir / f"{seg['id']}.wav"),
        }
        timeline.append(line)
        cursor += dur + gap_step

    srt = work / "subtitles.srt"
    _write_srt(script, timeline, srt)

    payload = {
        "version": 1,
        "project": pid,
        "language": language,
        "voice": voice,
        "rate": rate,
        "pitch": pitch,
        "narrator_id": narrator,
        "target_sec": target,
        "actual_sec": round(actual, 3),
        "stretch": round(stretch, 4),
        "segments": timeline,
        "updated": time.time(),
        "mode": "fake" if config.FAKE_TTS else "edge",
    }
    timeline_path = work / "timeline.json"
    timeline_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    p = db.get_project(pid) or {}
    meta = dict(p.get("meta_json") or {})
    meta.update({
        "narration_wav": str(narration),
        "timeline_json": str(timeline_path),
        "srt_path": str(srt),
    })
    delta = actual - target if target else 0
    db.update_project(
        pid,
        meta_json=meta,
        tts_status="ready",
        tts_progress=100,
        tts_message=f"{ff.fmt_duration(actual)} · target {ff.fmt_duration(target)} "
                    f"({delta:+.1f}s)",
        tts_error="",
    )
    db.append_log(
        pid,
        f"TTS ပြီး — {ff.fmt_duration(actual)} (target {ff.fmt_duration(target)}), "
        f"rate {rate:+d}%, {len(timeline)} ကြောင်း",
    )
    return {
        "narration": narration,
        "timeline_path": timeline_path,
        "srt": srt,
        "actual": actual,
        "stretch": stretch,
    }


def run_tts(pid: str) -> None:
    p = db.get_project(pid)
    if not p:
        raise RuntimeError("project not found")
    script = scriptgen.load_script(pid)
    if not script:
        raise RuntimeError("script is missing — generate the script first")

    settings = p.get("settings_json") or {}
    language = script.get("language") or settings.get("language") or "my"
    narrator = settings.get("narrator_id") or "F01"
    rate_off = settings.get("voice_rate") or 0
    pitch_off = settings.get("voice_pitch") or 0
    voice, base_rate, pitch = voice_for(language, narrator, rate_off, pitch_off)
    target = float(script.get("target_sec") or 0)

    work = ingest.project_dir(pid) / "script" / "tts"
    work.mkdir(parents=True, exist_ok=True)
    db.update_project(
        pid, tts_status="synthesizing", tts_progress=2,
        tts_message=f"{len(script['segments'])} ကြောင်း အသံထွက်နေသည်...", tts_error="",
    )
    db.append_log(pid, f"TTS စတင် — {voice}, rate {base_rate:+d}%, pitch {pitch:+d}Hz")

    def progress(frac: float) -> None:
        db.update_project(pid, tts_progress=round(2 + frac * 70, 1),
                          tts_message=f"အသံထွက် {int(frac * 100)}%")

    durations = _synth_pass(pid, script, voice, base_rate, pitch, work, "a", progress)
    total_a = sum(durations) + config.TTS_GAP_SEC * max(0, len(durations) - 1)
    factor = (target / total_a) if (target and total_a > 0) else 1.0

    used_rate = base_rate
    if target and (factor < 0.92 or factor > 1.08):
        wanted = int(round(base_rate + (factor - 1.0) * 100 * 0.85))
        wanted = max(config.TTS_RATE_MIN, min(config.TTS_RATE_MAX, wanted))
        if abs(wanted - base_rate) >= 2:
            db.update_project(
                pid, tts_progress=76,
                tts_message=f"အရှည် ညှိနေသည် — rate {wanted:+d}%",
            )
            durs_b = _synth_pass(pid, script, voice, wanted, pitch, work, "b", lambda f: None)
            total_b = sum(durs_b) + config.TTS_GAP_SEC * max(0, len(durs_b) - 1)
            if target:
                if abs(total_b - target) < abs(total_a - target):
                    durations, total_a, used_rate = durs_b, total_b, wanted
                else:
                    used_rate = base_rate

    tag = "b" if used_rate != base_rate else "a"
    publish_narration(
        pid, script, work,
        durations=durations,
        voice=voice,
        rate=used_rate,
        pitch=pitch,
        narrator=narrator,
        language=language,
        target=target,
        tag=tag,
    )
