"""Cinesip Recap — job orchestrator.

Pipeline: analyze → script → [SCRIPT REVIEW GATE] → voice → render →
shorts → metadata → done.

Jobs run in background threads; state persists to data/jobs/{id}.json so a
restart doesn't lose history. The review gate pauses after script writing:
the user reads/edits beats in the UI, then approves to continue.
"""
import json
import os
import threading
import time
import traceback
import uuid

from .. import config
from .quota import QuotaManager
from . import analyze as az
from . import script as sc
from . import voice as vc
from . import fairuse as fu
from . import render as rd

JOB_DIR = os.path.join(config.DATA_DIR, "jobs")
os.makedirs(JOB_DIR, exist_ok=True)

STAGES = ["queued", "analyze", "script", "awaiting_script",
          "voice", "render", "shorts", "metadata", "done", "error"]

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _path(job_id: str) -> str:
    return os.path.join(JOB_DIR, f"{job_id}.json")


def _save(job: dict):
    tmp = _path(job["id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in job.items() if k != "_thread"}, f,
                  ensure_ascii=False, default=str)
    os.replace(tmp, _path(job["id"]))


def get_job(job_id: str) -> dict | None:
    with _lock:
        if job_id in _jobs:
            return _jobs[job_id]
    p = _path(job_id)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
        with _lock:
            _jobs[job_id] = j
        return j
    return None


# jobs that can be safely resumed after an interruption (server restart etc.)
# "awaiting_script" needs the user, "done" needs nothing, "error" is manual retry.
RESUMABLE_STAGES = {"queued", "analyze", "script", "voice", "render", "shorts", "metadata"}


def orphaned_jobs() -> list:
    """Ids of jobs stuck mid-pipeline (e.g. server was restarted)."""
    import glob
    out = []
    for p in glob.glob(os.path.join(JOB_DIR, "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f)
            if j.get("stage") in RESUMABLE_STAGES:
                out.append(j["id"])
        except Exception:
            pass
    return out


def resume_job(job_id: str):
    """Re-queue an interrupted job, continuing from the right stage."""
    job = get_job(job_id)
    if not job:
        raise RuntimeError("job not found")
    stage = job.get("stage")
    if stage in ("done", "awaiting_script"):
        raise RuntimeError("job needs no resume")
    log(job, "🔄 ပြန်လည်စတင်သည်")
    if stage in ("voice", "render", "shorts", "metadata") and job.get("script"):
        _launch(job_id, _run_after_approval)
    else:
        # queued / analyze / script / error → full run (pauses again at review gate)
        _launch(job_id, run_job)


def log(job: dict, msg: str):
    job["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    job["log"] = job["log"][-300:]
    _save(job)


def set_stage(job: dict, stage: str, pct: float = 0, detail: str = ""):
    job["stage"] = stage
    job["progress"] = round(pct, 1)
    if detail:
        job["stage_detail"] = detail
    _save(job)


def create_job(video_id: str, video_path: str, mode: str = "long",
               target_minutes: float = 60, style: str = "recap",
               voice_provider: str = "gemini", persona: str | None = None,
               voice_preset: str | None = None,
               voice_opts: dict | None = None,
               ratio: str = "16:9", resolution: str = "720p",
               source_title: str = "") -> dict:
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "video_id": video_id, "video_path": video_path,
           "mode": mode, "target_minutes": target_minutes, "style": style,
           "voice_provider": voice_provider, "persona": persona,
           "voice_preset": voice_preset, "voice_opts": voice_opts or {},
           "ratio": ratio if ratio in ("16:9", "9:16", "original") else "16:9",
           "resolution": resolution if resolution in ("1080p", "720p", "480p") else "720p",
           "source_title": (source_title or "")[:200],
           "stage": "queued", "progress": 0, "stage_detail": "",
           "log": [], "created": time.time(), "script": None, "result": None,
           "error": None}
    with _lock:
        _jobs[job_id] = job
    _save(job)
    return job


# ---------------------------------------------------------------- runner
def _progress(job, stage):
    def cb(name, i, n):
        pct = {"analyze": (0, 25), "script": (25, 45), "voice": (45, 65),
               "render": (65, 90), "shorts": (90, 95), "metadata": (95, 100)
               }.get(stage, (0, 100))
        p = pct[0] + (pct[1] - pct[0]) * (i / max(1, n))
        set_stage(job, stage, p, f"{name} {i}/{n}")
    return cb


def run_job(job_id: str):
    job = get_job(job_id)
    if not job:
        return
    try:
        _run(job)
    except Exception as e:
        job["error"] = str(e)[:500]
        set_stage(job, "error", job.get("progress", 0))
        log(job, f"❌ {e}")
        traceback.print_exc()


def _run(job: dict):
    from . import shorts as sh
    from . import metadata as md

    quota = QuotaManager()
    client = sc.GeminiClient(quota)
    video_path = job["video_path"]

    # ---- quota gate ----
    import subprocess as _sp, json as _js
    dur = float(_sp.check_output(
        [config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path], text=True).strip())
    st = _sp.check_output(
        [config.FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", video_path], text=True)
    sj = _js.loads(st)["streams"][0]
    W, H = int(sj["width"]), int(sj["height"])
    job["src_duration"] = dur; job["src_w"] = W; job["src_h"] = H
    # output ratio/resolution → target dims for the final render
    out_w, out_h = rd.output_dims(job.get("ratio", "16:9"),
                                  job.get("resolution", "720p"), W, H)
    job["out_w"], job["out_h"] = out_w, out_h
    log(job, f"📐 Output {out_w}×{out_h} ({job.get('ratio')}, {job.get('resolution')})")

    budget = quota.check_budget(dur / 60, job["target_minutes"])
    if not budget["ok"]:
        raise RuntimeError(budget["hint"] or "Gemini quota မလုံလောက်ပါ။")
    log(job, f"Quota OK — လိုအပ် ~{budget['need']}, ကျန် {budget['have']}")

    # ---- 1. analyze ----
    set_stage(job, "analyze", 2, "transcribing")
    log(job, "🔍 Analysis စတင်သည် (local, free)")
    ev = az.build_evidence(job["video_id"], video_path,
                           progress_cb=lambda n, i, t: _progress(job, "analyze")(n, i, t))
    job["evidence"] = {"duration": ev["duration"], "n_scenes": ev["n_scenes"],
                       "n_segments": ev["n_segments"], "cached": ev["cached"]}
    log(job, f"✅ Analysis ပြီးပြီ — scenes {ev['n_scenes']}, transcript {ev['n_segments']} segs"
             + (" (cached)" if ev["cached"] else ""))
    _save(job)

    # ---- 2. script ----
    set_stage(job, "script", 26, "writing bible")
    log(job, "✍️ Script ရေးနေသည် (Gemini)")
    script = sc.build_script(client, ev, job["target_minutes"],
                             progress_cb=lambda n, i, t: _progress(job, "script")(n, i, t),
                             persona=job.get("persona"),
                             style=job.get("style") or sc.DEFAULT_STYLE)
    # commentary flags for the reviewer
    for b in script["beats"]:
        b["commentary_score"] = round(fu.commentary_score(b.get("narration", "")), 2)
        b["flag"] = "low_commentary" if fu.needs_commentary_rewrite(b.get("narration", "")) else ""
    job["script"] = script
    _save(job)
    set_stage(job, "awaiting_script", 45, "review needed")
    log(job, f"📝 Script ပြီးပြီ — {len(script['beats'])} beats. Review လုပ်ပြီး Approve နှိပ်ပါ.")
    return  # ← REVIEW GATE: resume via approve_job()


def _launch(job_id: str, target):
    """Start a worker thread for a job, refusing if one is already alive."""
    job = get_job(job_id)
    th = job.get("_thread") if job else None
    if th is not None and th.is_alive():
        raise RuntimeError("job already running")
    t = threading.Thread(target=target, args=(job_id,), daemon=True)
    if job is not None:
        job["_thread"] = t
    t.start()


def resume_after_approval(job_id: str):
    job = get_job(job_id)
    if not job or job["stage"] != "awaiting_script":
        raise RuntimeError("job is not awaiting approval")
    _launch(job_id, _run_after_approval)


def _run_after_approval(job_id: str):
    job = get_job(job_id)
    try:
        quota = QuotaManager()
        client = sc.GeminiClient(quota)
        beats = job["script"]["beats"]
        ev = az.build_evidence(job["video_id"], job["video_path"])  # cached
        scenes = [(a, b) for a, b in ev["scenes"]]
        # target output dims (ratio/resolution chosen by the user)
        W, H = job.get("out_w") or job["src_w"], job.get("out_h") or job["src_h"]
        ratio = job.get("ratio", "16:9")
        workdir = os.path.join(rd.RENDER_DIR, job_id, "beats")
        os.makedirs(workdir, exist_ok=True)
        max_clip = 5.0

        # ---- 3. voice (PARALLEL — TTS is network-bound) ----
        log(job, "🎙️ Voiceover ထုတ်နေသည်")
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _voice_one(b):
            """Synthesize one beat. Runs in worker threads — each beat writes
            its own wav file; quota manager is thread-safe."""
            nar = (b.get("narration") or "").strip()
            if not nar:
                return (b["idx"], {"wav": "", "vo_dur": 0, "words": [], "provider": "none"})
            wav = os.path.join(workdir, f"vo_{b['idx']:03d}.wav")
            r = vc.synthesize(nar, b.get("voice", "narrator"), wav, quota,
                              prefer=job.get("voice_provider", "gemini"),
                              voice_preset=job.get("voice_preset"),
                              voice_opts=job.get("voice_opts"))
            if not r.get("ok"):
                raise RuntimeError(f"TTS failed (beat {b['idx']}): {r.get('error')}")
            # cap: VO must not exceed 2.5x footage window + 30s
            fw = max(10.0, b["footage_end"] - b["footage_start"])
            max_vo = fw * 2.5 + 30
            if vc.wav_duration(wav) > max_vo:
                nar2 = sc.rewrite_shorter(client, nar, int(max_vo * 2.2))
                b["narration"] = nar2
                r = vc.synthesize(nar2, b.get("voice", "narrator"), wav, quota,
                                  prefer=job.get("voice_provider", "gemini"),
                                  voice_preset=job.get("voice_preset"),
                                  voice_opts=job.get("voice_opts"))
                if not r.get("ok"):
                    raise RuntimeError(f"TTS retry failed (beat {b['idx']})")
            wav, dur = vc.fit_beat_wav(wav, max_vo)
            return (b["idx"], {"wav": wav, "vo_dur": round(dur, 2),
                               "words": r.get("words", []), "provider": r.get("provider")})

        by_idx = {b["idx"]: b for b in beats}
        n_workers = max(1, config.VOICE_WORKERS)
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = {ex.submit(_voice_one, b): b["idx"] for b in beats}
            done = 0
            for fut in as_completed(futs):
                idx, res = fut.result()  # raises → outer handler marks job error
                by_idx[idx].update(res)
                done += 1
                set_stage(job, "voice", 45 + 20 * done / max(1, len(beats)),
                          f"beat {done}/{len(beats)} ({n_workers} parallel)")
        log(job, "✅ Voiceover ပြီးပြီ")
        _save(job)

        # ---- 4. render ----
        from . import overlays as _ov
        log(job, "🎬 Render လုပ်နေသည် (fair-use transforms + sync)")
        # load user overlays saved in preview editor
        ov_path = os.path.join(config.DATA_DIR, "overlays", f"{job['video_id']}.json")
        overlay_cfg = {}
        if os.path.isfile(ov_path):
            with open(ov_path, encoding="utf-8") as f:
                overlay_cfg = json.load(f)
        logo_cands = [os.path.join(config.DATA_DIR, "overlays", f"{job['video_id']}_logo{e}")
                      for e in (".png", ".jpg", ".jpeg", ".webp")]
        logo_path = next((c for c in logo_cands if os.path.isfile(c)), None)

        beat_files = []
        # PARALLEL beat renders — ffmpeg is CPU-bound, so only 2 workers on a
        # weak laptop; each ffmpeg is capped to RENDER_THREADS (see render.py)
        rw = max(1, config.RENDER_WORKERS)
        def _render_one(b):
            if not b.get("wav"):
                return None
            return rd.render_beat(job_id, b, job["video_path"], b["wav"],
                                  scenes, W, H, max_clip, workdir, ratio=ratio,
                                  src_w=job["src_w"], src_h=job["src_h"])
        voiced_beats = [b for b in beats if b.get("wav")]
        with ThreadPoolExecutor(max_workers=rw) as ex:
            futs = {ex.submit(_render_one, b): b["idx"] for b in voiced_beats}
            done = 0
            results = {}
            for fut in as_completed(futs):
                idx = futs[fut]
                bf = fut.result()
                if bf:
                    results[idx] = bf
                done += 1
                set_stage(job, "render", 65 + 25 * done / max(1, len(beats)),
                          f"beat {done}/{len(voiced_beats)} ({rw} parallel)")
        # keep original beat order for concat
        beat_files = [results[i] for i in sorted(results)]
        voiced = [b for b in beats if b.get("wav")]
        res = rd.assemble_final(job_id, beat_files, voiced, W, H,
                                overlay_cfg, logo_path,
                                progress_cb=lambda n, i, t: None)
        set_stage(job, "render", 90, "assembled")
        log(job, f"✅ Render ပြီးပြီ — {res['duration']/60:.1f} min")
        job["result"] = {"video": res["video"], "srt": res["srt"],
                         "duration": round(res["duration"], 1)}
        _save(job)

        # ---- 5. shorts ----
        if job["mode"] in ("long", "both"):
            from . import shorts as sh
            log(job, "📱 Shorts ဖြတ်နေသည်")
            job["result"]["shorts"] = sh.make_shorts(
                job_id, res["video"], voiced, W, H, n_shorts=4,
                progress_cb=lambda n, i, t: set_stage(job, "shorts", 90 + 5 * i / max(1, t), n))
            _save(job)

        # ---- 6. metadata ----
        from . import metadata as md
        log(job, "🏷️ Upload metadata ထုတ်နေသည်")
        _beats0 = (job.get("script") or {}).get("beats") or []
        _opening = _beats0[0].get("narration", "") if _beats0 else ""
        job["result"]["meta"] = md.generate_metadata(
            client, job["script"]["bible"], opening_narration=_opening,
            style=job.get("style"))
        _save(job)

        set_stage(job, "done", 100, "complete")
        log(job, "🎉 ပြီးပြီ!")
    except Exception as e:
        job["error"] = str(e)[:500]
        set_stage(job, "error", job.get("progress", 0))
        log(job, f"❌ {e}")
        traceback.print_exc()
