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
from .pipeline import overlays as ov

app = FastAPI(title="Cinesip Recap")


def _check_bins():
    for b in (config.FFMPEG, config.FFPROBE):
        if shutil.which(b) is None:
            raise RuntimeError(
                f"'{b}' not found on PATH. Install ffmpeg (Windows: https://www.gyan.dev/ffmpeg/builds/) "
                "and add its bin folder to PATH, then restart.")


@app.on_event("startup")
def _startup():
    _check_bins()


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
async def render_preview(video_id: str, t: float = 0):
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
    return FileResponse(p, media_type="video/mp4")
