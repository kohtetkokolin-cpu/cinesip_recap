"""Cinesip Recap — FastAPI server (Phase 1: ingest + WYSIWYG preview editor).

Run on Windows:
    python -m venv venv && venv\\Scripts\\activate
    pip install -r requirements.txt
    uvicorn backend.main:app --host 0.0.0.0 --port 8000
Then open http://localhost:8000
Requires ffmpeg + ffprobe on PATH.
"""
import json
import os
import shutil
import subprocess
import uuid

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse

from . import config
from .pipeline import script as _sc
from .pipeline import voice as _vc

app = FastAPI(title="Cinesip Recap")


@app.get("/api/health")
def health():
    """Lightweight status: is a Gemini key configured? (never exposes the key)"""
    n = len(config.GOOGLE_API_KEYS)
    return {"ok": True, "gemini_keys": n,
            "gemini_ready": n > 0,
            "msg": f"Gemini key {n} ခု တွေ့သည်" if n else
                   "Gemini key မရှိသေးပါ — .env မှာ GOOGLE_API_KEY ထည့်ပါ"}


@app.get("/api/styles")
def list_styles():
    """The 10 research-based recap styles for the wizard picker."""
    return {"styles": _sc.get_styles(), "default": _sc.DEFAULT_STYLE}


@app.get("/api/voices")
def list_voices():
    """The 10 named recap voices for the wizard picker."""
    return {"voices": _vc.get_voice_presets(), "default": _vc.DEFAULT_PRESET}


def _check_bins():
    for b in (config.FFMPEG, config.FFPROBE):
        if shutil.which(b) is None:
            raise RuntimeError(
                f"'{b}' not found on PATH. Install ffmpeg (Windows: https://www.gyan.dev/ffmpeg/builds/) "
                "and add its bin folder to PATH, then restart.")


@app.on_event("startup")
def _startup():
    _check_bins()
    # resume jobs orphaned by a server restart — a "queued" job must never
    # sit forever just because the worker thread died with the old process
    try:
        for jid in _jobs.orphaned_jobs():
            try:
                _jobs.resume_job(jid)
            except Exception:
                pass
    except Exception:
        pass


# ---------- auth (optional token gate) ----------
def require_token():
    if not config.APP_TOKEN:
        return ""
    from fastapi import Header
    async def _dep(x_token: str = Header("", alias="X-Token")):
        if x_token != config.APP_TOKEN:
            raise HTTPException(401, "Invalid token")
        return x_token
    return _dep


# ---------- frontend ----------
@app.get("/", response_class=FileResponse)
def index():
    p = os.path.join(config.FRONTEND_DIR, "index.html")
    if not os.path.isfile(p):
        return JSONResponse({"ok": False, "error": "frontend not built yet"}, 503)
    return FileResponse(p, media_type="text/html; charset=utf-8")


# ---------- video ingest ----------
def _probe(path: str) -> dict:
    out = subprocess.check_output(
        [config.FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration",
         "-show_entries", "format=duration",
         "-of", "json", path], text=True)
    d = json.loads(out)
    st = (d.get("streams") or [{}])[0]
    dur = float(st.get("duration") or (d.get("format") or {}).get("duration") or 0)
    return {"width": int(st.get("width") or 0), "height": int(st.get("height") or 0),
            "duration": dur}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    vid = uuid.uuid4().hex[:12]
    ext = os.path.splitext(file.filename or "")[1].lower() or ".mp4"
    if ext not in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
        raise HTTPException(400, "video file only (.mp4/.mkv/.mov/.webm/.avi)")
    dest = os.path.join(config.UPLOAD_DIR, f"{vid}{ext}")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        meta = _probe(dest)
    except Exception as e:
        os.remove(dest)
        raise HTTPException(400, f"cannot read video: {e}")
    meta.update({"video_id": vid, "path": dest})
    with open(os.path.join(config.UPLOAD_DIR, f"{vid}.json"), "w") as f:
        json.dump(meta, f)
    return {"video_id": vid, "width": meta["width"], "height": meta["height"],
            "duration": meta["duration"]}


def _video_path(video_id: str) -> str:
    mj = os.path.join(config.UPLOAD_DIR, f"{video_id}.json")
    if not os.path.isfile(mj):
        raise HTTPException(404, "video not found")
    with open(mj) as f:
        return json.load(f)["path"]


@app.get("/api/video/{video_id}")
def serve_video(video_id: str):
    return FileResponse(_video_path(video_id), media_type="video/mp4")


@app.get("/api/frame/{video_id}")
def frame(video_id: str, t: float = 5.0):
    src = _video_path(video_id)
    out = os.path.join(config.CLIP_DIR, f"{video_id}_frame.jpg")
    subprocess.run([config.FFMPEG, "-y", "-ss", str(max(0, t)), "-i", src,
                    "-vframes", "1", "-q:v", "3", out],
                   check=True, capture_output=True)
    return FileResponse(out, media_type="image/jpeg")


# ---------- fonts ----------
@app.get("/api/fonts")
def list_fonts():
    fonts = sorted(f for f in os.listdir(config.FONT_DIR)
                   if f.lower().endswith((".ttf", ".otf")))
    return {"fonts": fonts}


@app.post("/api/fonts")
async def upload_font(file: UploadFile = File(...)):
    name = os.path.basename(file.filename or "")
    if not name.lower().endswith((".ttf", ".otf")):
        raise HTTPException(400, "only .ttf/.otf fonts")
    dest = os.path.join(config.FONT_DIR, name)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return await list_fonts()


# ---------- logo ----------
@app.post("/api/logo/{video_id}")
async def upload_logo(video_id: str, file: UploadFile = File(...)):
    _video_path(video_id)  # 404 if unknown
    name = os.path.basename(file.filename or "logo.png")
    ext = os.path.splitext(name)[1].lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(400, "image only")
    dest = os.path.join(config.OVERLAY_DIR, f"{video_id}_logo{ext}")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"ok": True, "logo_path": dest,
            "logo_url": f"/api/logo/{video_id}"}


def _logo_path(video_id: str) -> str | None:
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        c = os.path.join(config.OVERLAY_DIR, f"{video_id}_logo{ext}")
        if os.path.isfile(c):
            return c
    return None


@app.get("/api/logo/{video_id}")
def serve_logo(video_id: str):
    p = _logo_path(video_id)
    if not p:
        raise HTTPException(404, "logo not found")
    return FileResponse(p)


# ---------- overlays (the WYSIWYG model) ----------
def _ov_path(video_id: str) -> str:
    return os.path.join(config.OVERLAY_DIR, f"{video_id}.json")


@app.get("/api/overlays/{video_id}")
def get_overlays(video_id: str):
    p = _ov_path(video_id)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


@app.post("/api/overlays/{video_id}")
async def save_overlays(video_id: str, body: dict):
    _video_path(video_id)
    clean = ov.sanitize_overlays(body)
    with open(_ov_path(video_id), "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=1)
    return {"ok": True, "overlays": clean}


# ---------- render a 10s test clip with overlays burned in ----------
@app.post("/api/render-preview/{video_id}")
def render_preview(video_id: str, t: float = 0):
    """Render 10 seconds starting at t with ALL overlays applied exactly as
    the preview editor shows them. This is the WYSIWYG proof."""
    src = _video_path(video_id)
    meta = _probe(src)
    w, h = meta["width"], meta["height"]
    if w <= 0 or h <= 0:
        raise HTTPException(400, "bad video dimensions")

    p = _ov_path(video_id)
    raw = {}
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    clean = ov.sanitize_overlays(raw)

    logo_path = _logo_path(video_id)

    # burn the sample subtitle so placement can be verified
    ass_path = os.path.join(config.CLIP_DIR, f"{video_id}_preview.ass")
    ov.write_ass(ass_path, w, h,
                 [(0.5, 9.5, clean["sub"]["text"])],
                 clean, font_name=clean["sub"].get("font") or "Padauk.ttf")

    vf = ov.build_filter(w, h, clean, logo_path=logo_path,
                         font_dir=config.FONT_DIR, ass_path=ass_path)
    out = os.path.join(config.CLIP_DIR, f"{video_id}_preview.mp4")
    dur = 10.0
    cmd = [config.FFMPEG, "-y", "-ss", str(max(0, t)), "-i", src,
           "-t", str(dur), "-filter_complex", vf, "-map", "[vout]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-c:a", "aac", "-shortest", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(out):
        raise HTTPException(500, f"render failed: {r.stderr[-800:]}")
    return {"ok": True, "clip_url": f"/api/clip/{os.path.basename(out)}"}


@app.get("/api/clip/{name}")
def serve_clip(name: str):
    p = os.path.join(config.CLIP_DIR, os.path.basename(name))
    if not os.path.isfile(p):
        raise HTTPException(404, "clip not found")
    mt = "audio/wav" if name.lower().endswith(".wav") else "video/mp4"
    return FileResponse(p, media_type=mt)


@app.post("/api/voice-preview")
def voice_preview(body: dict):
    """Synthesize a short sample with the chosen voice preset + opts.
    Lets the user hear the voice before starting a job."""
    from .pipeline import voice as _vv
    from .pipeline.quota import QuotaManager as _QQ
    preset = body.get("voice_preset") or _vv.DEFAULT_PRESET
    if preset not in _vv._PRESET_BY_ID:
        preset = _vv.DEFAULT_PRESET
    opts = body.get("voice_opts") or {}
    text = (body.get("text") or "")[:300]
    if not text.strip():
        raise HTTPException(400, "text required")
    out = os.path.join(config.CLIP_DIR, f"voiceprev_{preset}_{uuid.uuid4().hex[:8]}.wav")
    r = _vv.synthesize(text, preset, out, _QQ(), voice_preset=preset, voice_opts=opts)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    return {"ok": True, "audio_url": f"/api/clip/{os.path.basename(r.get('wav', out))}"}


# ================= PHASE 2: recap jobs =================
from .pipeline import jobs as _jobs
from .pipeline.quota import QuotaManager as _QM
import threading as _th


@app.post("/api/jobs")
def create_job(body: dict):
    video_id = body.get("video_id", "")
    src = _video_path(video_id)
    mode = body.get("mode", "long")  # long | short | both
    target = float(body.get("target_minutes") or (60 if mode != "short" else 5))
    style = body.get("style") or _sc.DEFAULT_STYLE
    if style not in _sc.RECAP_STYLES:
        style = _sc.DEFAULT_STYLE
    voice_preset = body.get("voice_preset") or _vc.DEFAULT_PRESET
    if voice_preset not in _vc._PRESET_BY_ID:
        voice_preset = _vc.DEFAULT_PRESET
    # voice_opts: {speed: 0.7-1.3, pitch: -12..12, volume: 50-150} — clamped in voice.py
    voice_opts = body.get("voice_opts") or {}
    if not isinstance(voice_opts, dict):
        voice_opts = {}
    # provider is derived from the preset; kept for back-compat display
    voice_provider = _vc._PRESET_BY_ID[voice_preset]["provider"]
    job = _jobs.create_job(video_id, src, mode=mode, target_minutes=target,
                           style=style,
                           voice_provider=voice_provider,
                           persona=body.get("persona"),
                           voice_preset=voice_preset,
                           voice_opts=voice_opts,
                           ratio=body.get("ratio") or "16:9",
                           resolution=body.get("resolution") or "720p")
    # quota pre-check before burning any work
    import subprocess as _sp
    dur = float(_sp.check_output(
        [config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", src], text=True).strip())
    chk = _QM().check_budget(dur / 60, target)
    if not chk["ok"]:
        # mark it errored, not "queued" — a queued job with no worker looks stuck
        msg = chk["hint"] or "Gemini quota မလုံလောက်ပါ။"
        job["error"] = msg
        _jobs.set_stage(job, "error", 0)
        _jobs.log(job, f"❌ {msg}")
        return {"ok": False, "error": msg, "budget": chk, "job_id": job["id"]}
    t = _th.Thread(target=_jobs.run_job, args=(job["id"],), daemon=True)
    job["_thread"] = t
    t.start()
    return {"ok": True, "job_id": job["id"], "budget": chk}


@app.get("/api/jobs")
def list_jobs():
    import glob as _g
    out = []
    for p in sorted(_g.glob(os.path.join(config.DATA_DIR, "jobs", "*.json")),
                    key=os.path.getmtime, reverse=True)[:20]:
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
        out.append({"id": j["id"], "stage": j["stage"], "progress": j.get("progress", 0),
                    "mode": j.get("mode"), "created": j.get("created"),
                    "video_id": j.get("video_id"),
                    "voice_provider": j.get("voice_provider")})
    return {"jobs": out}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    j = _jobs.get_job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    # video dims for the "Overlay Studio" jump button
    vdims = {}
    try:
        mp = os.path.join(config.UPLOAD_DIR, f"{j.get('video_id')}.json")
        if os.path.isfile(mp):
            with open(mp) as f:
                m = json.load(f)
            vdims = {"width": m.get("width"), "height": m.get("height"),
                     "duration": m.get("duration")}
    except Exception:
        pass
    return {"id": j["id"], "stage": j["stage"], "progress": j.get("progress", 0),
            "stage_detail": j.get("stage_detail", ""), "log": j.get("log", [])[-60:],
            "error": j.get("error"), "evidence": j.get("evidence"),
            "video_id": j.get("video_id"), "mode": j.get("mode"),
            "voice_provider": j.get("voice_provider"), "voice_preset": j.get("voice_preset"),
            "created": j.get("created"),
            "video_width": vdims.get("width"), "video_height": vdims.get("height"),
            "video_duration": vdims.get("duration"),
            "n_beats": len((j.get("script") or {}).get("beats", []))}


@app.get("/api/jobs/{job_id}/script")
def job_script(job_id: str):
    j = _jobs.get_job(job_id)
    if not j or not j.get("script"):
        raise HTTPException(404, "script not ready")
    return {"bible": j["script"]["bible"], "beats": j["script"]["beats"]}


@app.post("/api/jobs/{job_id}/script")
async def save_script(job_id: str, body: dict):
    j = _jobs.get_job(job_id)
    if not j or j["stage"] != "awaiting_script":
        raise HTTPException(400, "job is not awaiting script review")
    beats = body.get("beats") or []
    # keep only editable fields, preserve the rest
    cur = {b["idx"]: b for b in j["script"]["beats"]}
    for nb in beats:
        idx = nb.get("idx")
        if idx in cur:
            for k in ("narration", "voice", "hook_line", "payoff", "goal"):
                if k in nb:
                    cur[idx][k] = nb[k]
            # re-score commentary after edit
            from .pipeline import fairuse as _fu
            cur[idx]["commentary_score"] = round(_fu.commentary_score(cur[idx].get("narration", "")), 2)
            cur[idx]["flag"] = "low_commentary" if _fu.needs_commentary_rewrite(cur[idx].get("narration", "")) else ""
    _jobs._save(j)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/approve")
def approve_job(job_id: str):
    _jobs.resume_after_approval(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str):
    """Manually re-queue a stuck or failed job."""
    try:
        _jobs.resume_job(job_id)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/jobs/{job_id}/voice-preview")
def voice_preview_job(job_id: str, body: dict):
    """Synthesize one beat's narration for preview (does not advance the job)."""
    j = _jobs.get_job(job_id)
    if not j or not j.get("script"):
        raise HTTPException(404, "script not ready")
    idx = int(body.get("beat_idx", 1))
    beat = next((b for b in j["script"]["beats"] if b["idx"] == idx), None)
    if not beat or not beat.get("narration"):
        raise HTTPException(404, "beat not found")
    from .pipeline import voice as _vc
    from .pipeline.quota import QuotaManager as _QM
    out = os.path.join(config.DATA_DIR, "jobs", f"{job_id}_pv_{idx}.wav")
    r = _vc.synthesize(beat["narration"][:600], beat.get("voice", "narrator"), out,
                       _QM(), prefer=j.get("voice_provider", "gemini"),
                       voice_preset=j.get("voice_preset"),
                       voice_opts=j.get("voice_opts"))
    if not r.get("ok"):
        raise HTTPException(500, r.get("error"))
    return {"ok": True, "audio_url": f"/api/jobs/{job_id}/voice-file/pv_{idx}.wav",
            "provider": r.get("provider")}


@app.get("/api/jobs/{job_id}/voice-file/{name}")
def voice_file(job_id: str, name: str):
    p = os.path.join(config.DATA_DIR, "jobs", f"{job_id}_{os.path.basename(name)}")
    if not os.path.isfile(p):
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type="audio/wav")


@app.get("/api/jobs/{job_id}/result")
def job_result(job_id: str):
    j = _jobs.get_job(job_id)
    if not j or j["stage"] != "done":
        raise HTTPException(404, "result not ready")
    r = j["result"] or {}
    base = f"/api/jobs/{job_id}/file"
    out = {"video_url": f"{base}/final.mp4", "srt_url": f"{base}/final.srt",
           "duration": r.get("duration"), "meta": r.get("meta"),
           "shorts": [{"url": f"{base}/shorts/{os.path.basename(s['mp4'])}",
                       "title_hint": s.get("title_hint"), "duration": s.get("duration")}
                      for s in (r.get("shorts") or [])]}
    return out


@app.get("/api/jobs/{job_id}/file/{name}")
def result_file(job_id: str, name: str):
    p = os.path.join(config.DATA_DIR, "renders", job_id, os.path.basename(name))
    if not os.path.isfile(p):
        raise HTTPException(404, "not found")
    mt = "video/mp4" if name.endswith(".mp4") else "text/plain; charset=utf-8"
    return FileResponse(p, media_type=mt)


@app.get("/api/jobs/{job_id}/file/shorts/{name}")
def result_short(job_id: str, name: str):
    p = os.path.join(config.DATA_DIR, "renders", job_id, "shorts", os.path.basename(name))
    if not os.path.isfile(p):
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type="video/mp4")
