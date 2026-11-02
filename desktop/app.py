"""Cinesip Recap — standalone Long Movie app (M3).

M1: upload + ingest. M2: Gemini script + Edge TTS narration.
M3: WYSIWYG editor + frame-accurate render.
Run:  python app.py   ->  http://127.0.0.1:7861
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# Burmese UI/log strings are part of the app; make Windows consoles UTF-8 safe.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
import db
import fairuse
import ffmpeg_utils as ff
import fonts
import gemini_client
import ingest
import jobs
import languages
import render
import scriptgen
import voice_preview
import voices


DEFAULTS: dict[str, Any] = {
    "target_pct": 50,
    "style": "style_1_recap",
    "fairuse": 30,
    "language": "my",
    "narrator_id": "F01",
    "ratio": "16:9",
    "resolution": "1280:720",
    "watermark_text": "",
    "scene_sec": 5.0,
    "voice_rate": 0,
    "voice_pitch": 0,
}


def normalize_settings(raw: Any) -> dict[str, Any]:
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k in out})
    try:
        out["target_pct"] = int(out["target_pct"])
    except Exception:
        out["target_pct"] = DEFAULTS["target_pct"]
    if out["target_pct"] not in config.TARGET_PCTS:
        out["target_pct"] = DEFAULTS["target_pct"]
    if out["style"] not in config.STYLES:
        out["style"] = DEFAULTS["style"]
    try:
        out["fairuse"] = int(out["fairuse"])
    except Exception:
        out["fairuse"] = DEFAULTS["fairuse"]
    if out["fairuse"] not in config.FAIRUSE_LEVELS:
        out["fairuse"] = DEFAULTS["fairuse"]
    if not languages.get_language(str(out["language"])):
        out["language"] = DEFAULTS["language"]
    if not voices.get_voice(str(out["narrator_id"])):
        out["narrator_id"] = DEFAULTS["narrator_id"]
    if out["ratio"] not in config.RATIOS:
        out["ratio"] = DEFAULTS["ratio"]
    if out["resolution"] not in config.RESOLUTIONS:
        out["resolution"] = DEFAULTS["resolution"]
    out["watermark_text"] = str(out.get("watermark_text") or "").strip()[:100]
    try:
        out["scene_sec"] = round(float(out["scene_sec"]), 2)
    except Exception:
        out["scene_sec"] = DEFAULTS["scene_sec"]
    out["scene_sec"] = max(3.0, min(15.0, out["scene_sec"]))
    for key in ("voice_rate", "voice_pitch"):
        try:
            out[key] = int(round(float(out[key])))
        except Exception:
            out[key] = DEFAULTS[key]
    out["voice_rate"] = max(-40, min(40, out["voice_rate"]))
    out["voice_pitch"] = max(-20, min(20, out["voice_pitch"]))
    return out


def render_public(p: dict[str, Any]) -> dict[str, Any]:
    meta = p.get("render_meta_json") or {}
    path = p.get("render_path") or ""
    available = bool(path and Path(path).exists())
    stale = False
    if available:
        try:
            stale = render.is_stale(p)
        except Exception:
            stale = False
    duration = float(meta.get("duration") or 0)
    return {
        "status": p.get("render_status") or "",
        "progress": p.get("render_progress") or 0,
        "message": p.get("render_message") or "",
        "error": p.get("render_error") or "",
        "available": available,
        "stale": stale,
        "url": f"/api/projects/{p['id']}/file/output" if available else "",
        "download_url": f"/api/projects/{p['id']}/file/output?download=1" if available else "",
        "duration": duration,
        "duration_label": ff.fmt_duration(duration) if duration else "",
        "frames": meta.get("frames") or 0,
        "actual_frames": meta.get("actual_frames") or 0,
        "fps": meta.get("fps") or 0,
        "width": meta.get("width") or 0,
        "height": meta.get("height") or 0,
        "ratio": meta.get("ratio") or "",
        "resolution": meta.get("resolution") or "",
        "size": meta.get("size") or 0,
        "updated": meta.get("updated") or 0,
    }


def project_public(p: dict[str, Any]) -> dict[str, Any]:
    meta = p.get("meta_json") or {}
    duration = float(p.get("duration") or 0)
    settings = p.get("settings_json") or {}
    chunks = []
    for c in meta.get("chunks") or []:
        chunks.append({
            **{k: c.get(k) for k in ("index", "start", "end", "duration", "file", "size")},
            "url": f"/api/projects/{p['id']}/file/chunk?index={int(c.get('index', 0))}",
        })
    files: dict[str, str] = {}
    if p.get("source_path"):
        files["source"] = f"/api/projects/{p['id']}/file/source"
    if p.get("proxy_path"):
        files["proxy"] = f"/api/projects/{p['id']}/file/proxy"
    if p.get("audio_path"):
        files["audio"] = f"/api/projects/{p['id']}/file/audio"
    if p.get("thumb_path"):
        files["thumb"] = f"/api/projects/{p['id']}/file/thumb"
    target_pct = int(settings.get("target_pct") or 50)
    return {
        "id": p["id"],
        "name": p["name"],
        "status": p["status"],
        "progress": p.get("progress") or 0,
        "message": p.get("message") or "",
        "error": p.get("error") or "",
        "created": p.get("created"),
        "updated": p.get("updated"),
        "duration": duration,
        "duration_label": ff.fmt_duration(duration) if duration else "",
        "width": p.get("width") or 0,
        "height": p.get("height") or 0,
        "fps": p.get("fps") or 0,
        "video_codec": p.get("video_codec") or "",
        "audio_codec": p.get("audio_codec") or "",
        "size_bytes": p.get("size_bytes") or 0,
        "settings": settings,
        "target_sec": round(duration * target_pct / 100.0, 1) if duration else 0,
        "target_label": ff.fmt_duration(duration * target_pct / 100.0) if duration else "",
        "files": files,
        "chunks": chunks,
        "log": (p.get("log") or "")[-12000:],
        "script": script_public(p, chunks),
        "render": render_public(p),
        "upload": {
            "filename": p.get("upload_filename") or "",
            "size": p.get("upload_size") or 0,
            "chunk_size": p.get("upload_chunk_size") or config.CHUNK_SIZE,
            "total_chunks": p.get("upload_total_chunks") or 0,
        },
    }


def script_public(p: dict[str, Any], chunks: list | None = None) -> dict[str, Any]:
    meta = p.get("meta_json") or {}
    out: dict[str, Any] = {
        "status": p.get("script_status") or "",
        "progress": p.get("script_progress") or 0,
        "message": p.get("script_message") or "",
        "error": p.get("script_error") or "",
        "available": bool(meta.get("script_json")),
        "chunk_count": len(chunks if chunks is not None else (meta.get("chunks") or [])),
        "tts": {
            "status": p.get("tts_status") or "",
            "progress": p.get("tts_progress") or 0,
            "message": p.get("tts_message") or "",
            "error": p.get("tts_error") or "",
            "available": bool(meta.get("narration_wav")),
            "timeline_sig": render.timeline_signature(p["id"]) if meta.get("narration_wav") else "",
        },
    }

    script = scriptgen.load_script(p["id"]) if out["available"] else None
    if script:
        segs = script.get("segments") or []
        out.update({
            "language": script.get("language"),
            "style": script.get("style"),
            "target_sec": script.get("target_sec") or 0,
            "target_label": ff.fmt_duration(script.get("target_sec") or 0),
            "est_sec": script.get("est_speech_sec") or 0,
            "est_label": ff.fmt_duration(script.get("est_speech_sec") or 0),
            "model": script.get("model"),
            "segment_count": len(segs),
            "segments": segs[:1000],
        })

    if out["tts"]["available"]:
        out["tts"].update({
            "audio_url": f"/api/projects/{p['id']}/file/narration",
            "srt_url": f"/api/projects/{p['id']}/file/srt",
        })
        timeline = meta.get("timeline_json")
        if timeline and Path(timeline).exists():
            try:
                data = json.loads(Path(timeline).read_text(encoding="utf-8"))
                out["tts"].update({
                    "actual_sec": data.get("actual_sec") or 0,
                    "actual_label": ff.fmt_duration(data.get("actual_sec") or 0),
                    "lines": len(data.get("segments") or []),
                    "voice": data.get("voice"),
                    "rate": data.get("rate"),
                })
            except Exception:
                pass
    return out


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.ensure_dirs()
    db.init_db()
    interrupted = db.mark_interrupted()
    if interrupted:
        print(f"[startup] marked {interrupted} interrupted project(s) as error", flush=True)
    if voices.duplicates():
        print(f"[startup] WARNING duplicate voice profiles: {voices.duplicates()}", flush=True)
    if config.FAKE_GEMINI:
        print("[startup] Gemini: FAKE mode (LONGMOVIE_FAKE_GEMINI=1)", flush=True)
    elif not gemini_client.configured():
        print("[startup] Gemini: no GEMINI_API_KEY — script step disabled", flush=True)
    else:
        print(f"[startup] Gemini: {config.GEMINI_MODEL}", flush=True)
    if config.FAKE_TTS:
        print("[startup] TTS: FAKE mode (LONGMOVIE_FAKE_TTS=1)", flush=True)
    print(f"[startup] Render: {render.FPS} fps, libx264 veryfast", flush=True)
    yield


app = FastAPI(title="Cinesip Recap", version="0.1.0", lifespan=lifespan)
config.ensure_dirs()
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
app.mount("/assets", StaticFiles(directory=str(config.ASSETS_DIR)), name="assets")


class ProjectCreate(BaseModel):
    name: str = "Untitled"
    settings: dict = Field(default_factory=dict)


class UploadInit(BaseModel):
    filename: str
    size: int


class UploadComplete(BaseModel):
    filename: str
    size: int
    total_chunks: int


def _get_or_404(pid: str) -> dict[str, Any]:
    p = db.get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    return p


def _safe_name(filename: str) -> str:
    name = Path(str(filename or "video.mp4")).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    if not name:
        name = "video.mp4"
    return name[:120]


def _chunk_dir(pid: str) -> Path:
    d = ingest.project_dir(pid) / "upload" / "chunks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _received(pid: str) -> list[int]:
    out = []
    for p in _chunk_dir(pid).glob("*.part"):
        try:
            out.append(int(p.stem))
        except ValueError:
            pass
    return sorted(out)


@app.get("/", response_class=HTMLResponse)
def index():
    path = config.STATIC_DIR / "index.html"
    if not path.exists():
        return HTMLResponse("<h1>Cinesip Recap</h1><p>static/index.html missing</p>", status_code=500)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/api/health")
def health():
    return {"ok": True, "app": "Cinesip Recap", "version": "0.1.0"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    icon = config.ASSETS_DIR / "favicon.png"
    if icon.exists():
        return FileResponse(icon, media_type="image/png")
    return HTMLResponse("", status_code=204)


@app.get("/api/config")
def get_config():
    return {
        "target_pcts": list(config.TARGET_PCTS),
        "styles": [
            {"id": k, "label": v["label"], "short": v["short"]}
            for k, v in config.STYLES.items()
        ],
        "fairuse_levels": fairuse.list_levels(),
        "languages": languages.LANGUAGES,
        "voices": voices.list_voices(),
        "ratios": list(config.RATIOS),
        "resolutions": list(config.RESOLUTIONS),
        "fonts": fonts.list_fonts(),
        "defaults": DEFAULTS,
        "max_upload_bytes": config.MAX_UPLOAD_BYTES,
        "chunk_size": config.CHUNK_SIZE,
        "gemini_chunk_sec": config.GEMINI_CHUNK_SEC,
        "proxy_height": config.PROXY_HEIGHT,
        "gemini": {
            "ready": gemini_client.ready(),
            "model": config.GEMINI_MODEL,
            "fake": config.FAKE_GEMINI,
            "reason": gemini_client.missing_reason(),
        },
        "tts": {"fake": config.FAKE_TTS, "gap": config.TTS_GAP_SEC},
    }


@app.get("/api/projects")
def list_projects():
    return {"projects": [project_public(p) for p in db.list_projects()]}


@app.post("/api/projects")
def create_project(body: ProjectCreate):
    settings = normalize_settings(body.settings)
    p = db.create_project(body.name, settings)
    db.append_log(p["id"], "Project အသစ် ဖန်တီးပြီး")
    return project_public(p)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return project_public(_get_or_404(pid))


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    p = _get_or_404(pid)
    busy = (
        p.get("status") in {"queued", "probing", "proxying", "audio", "chunking", "thumbnail"}
        or (p.get("script_status") or "") in {"queued", "analyzing"}
        or (p.get("tts_status") or "") in {"queued", "synthesizing"}
        or (p.get("render_status") or "") in {"queued", "rendering"}
    )
    if busy:
        raise HTTPException(409, "ဒီ project မှာ အလုပ်လုပ်နေဆဲဖြစ်လို့ မဖျက်နိုင်ပါ — ပြီးမှ ပြန်စမ်းပါ")
    shutil.rmtree(ingest.project_dir(pid), ignore_errors=True)
    db.delete_project(pid)
    return {"ok": True}


@app.post("/api/projects/{pid}/upload/init")
def upload_init(pid: str, body: UploadInit):
    p = _get_or_404(pid)
    filename = _safe_name(body.filename)
    ext = Path(filename).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"unsupported video type: {ext or 'none'}")
    if body.size <= 0:
        raise HTTPException(400, "empty file")
    if body.size > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file too large (max {config.MAX_UPLOAD_BYTES // 1024**3} GB)")

    old_size = int(p.get("upload_size") or 0)
    old_total = int(p.get("upload_total_chunks") or 0)
    total = max(1, (body.size + config.CHUNK_SIZE - 1) // config.CHUNK_SIZE)
    if old_size != body.size or old_total != total:
        shutil.rmtree(_chunk_dir(pid), ignore_errors=True)
        _chunk_dir(pid)

    db.update_project(
        pid, status="uploading", progress=0,
        message="ဗီဒီယို တင်နေသည်...", error="",
        upload_filename=filename, upload_size=body.size,
        upload_chunk_size=config.CHUNK_SIZE, upload_total_chunks=total,
    )
    db.append_log(pid, f"Upload စတင်: {filename} ({body.size / 1024 / 1024:.1f} MB, {total} chunks)")
    return {
        "ok": True,
        "filename": filename,
        "size": body.size,
        "chunk_size": config.CHUNK_SIZE,
        "total_chunks": total,
        "received": _received(pid),
    }


@app.get("/api/projects/{pid}/upload/status")
def upload_status(pid: str):
    p = _get_or_404(pid)
    total = int(p.get("upload_total_chunks") or 0)
    received = _received(pid)
    return {
        "ok": True,
        "received": received,
        "total_chunks": total,
        "uploaded_chunks": len(received),
        "percent": round(len(received) / total * 100, 1) if total else 0,
    }


@app.put("/api/projects/{pid}/upload/chunk/{index}")
async def upload_chunk(pid: str, index: int, request: Request):
    p = _get_or_404(pid)
    total = int(p.get("upload_total_chunks") or 0)
    if total <= 0:
        raise HTTPException(400, "call upload/init first")
    if index < 0 or index >= total:
        raise HTTPException(400, f"chunk index out of range 0..{total - 1}")
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty chunk")
    if len(body) > config.CHUNK_SIZE + 2 * 1024 * 1024:
        raise HTTPException(413, "chunk larger than allowed")
    d = _chunk_dir(pid)
    tmp = d / f"{index:06d}.part.tmp"
    final = d / f"{index:06d}.part"
    tmp.write_bytes(body)
    os.replace(tmp, final)
    received = _received(pid)
    progress = round(len(received) / total * 100, 1)
    db.update_project(pid, progress=progress, message=f"ဗီဒီယို တင်နေသည်... {len(received)}/{total}")
    return {"ok": True, "index": index, "received": len(received), "total": total}


@app.post("/api/projects/{pid}/upload/complete")
def upload_complete(pid: str, body: UploadComplete):
    p = _get_or_404(pid)
    total = int(p.get("upload_total_chunks") or 0)
    if total <= 0:
        raise HTTPException(400, "call upload/init first")
    if int(body.total_chunks) != total:
        raise HTTPException(400, f"chunk count mismatch (expected {total})")
    received = set(_received(pid))
    missing = [i for i in range(total) if i not in received]
    if missing:
        raise HTTPException(400, f"missing chunks: {missing[:20]}{'...' if len(missing) > 20 else ''}")

    filename = _safe_name(body.filename)
    ext = Path(filename).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(400, "unsupported video type")
    if int(body.size) != int(p.get("upload_size") or 0):
        raise HTTPException(400, "file size changed; re-initialize upload")

    source_dir = ingest.project_dir(pid) / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    final = source_dir / filename
    tmp = source_dir / (filename + ".assembling")
    with open(tmp, "wb") as out:
        for i in range(total):
            part = _chunk_dir(pid) / f"{i:06d}.part"
            with open(part, "rb") as f:
                shutil.copyfileobj(f, out)
    if tmp.stat().st_size != int(body.size):
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, "assembled size does not match upload size")
    os.replace(tmp, final)
    shutil.rmtree(_chunk_dir(pid), ignore_errors=True)

    try:
        info = ff.probe(final)
    except Exception as exc:  # noqa: BLE001
        final.unlink(missing_ok=True)
        raise HTTPException(400, f"cannot read video: {exc}") from exc

    target_pct = int((p.get("settings_json") or {}).get("target_pct") or 50)
    db.update_project(
        pid, status="uploaded", progress=0, message="Upload ပြီး — စစ်ဆေးပြီးပါပြီ",
        source_path=str(final), duration=info["duration"],
        width=info["display_width"], height=info["display_height"], fps=info["fps"],
        video_codec=info["video_codec"], audio_codec=info["audio_codec"],
        size_bytes=info["size"], error="",
    )
    db.append_log(
        pid, f"Upload ပြီး: {ff.fmt_duration(info['duration'])}, "
             f"{info['display_width']}x{info['display_height']} — target {target_pct}%"
    )
    jobs.enqueue(pid)
    return project_public(db.get_project(pid) or p)


@app.post("/api/projects/{pid}/ingest")
def retry_ingest(pid: str):
    p = _get_or_404(pid)
    if not p.get("source_path") or not Path(p["source_path"]).exists():
        raise HTTPException(400, "source video is missing")
    if jobs.enqueue(pid):
        return {"ok": True, "message": "queued"}
    return {"ok": False, "message": "already queued/running"}


class ScriptAction(BaseModel):
    force: bool = False


class EditBody(BaseModel):
    edit: dict = Field(default_factory=dict)


class SettingsBody(BaseModel):
    settings: dict = Field(default_factory=dict)


class PresetBody(BaseModel):
    name: str = ""
    project_id: str = ""
    settings: dict = Field(default_factory=dict)
    edit: dict = Field(default_factory=dict)


class PresetApply(BaseModel):
    project_id: str = ""


class RenderAction(BaseModel):
    force: bool = False


@app.post("/api/projects/{pid}/script")
def start_script(pid: str, body: ScriptAction | None = None):
    p = _get_or_404(pid)
    if p.get("status") != "ready":
        raise HTTPException(400, "ingest is not ready yet")
    if not (p.get("meta_json") or {}).get("chunks"):
        raise HTTPException(400, "no Gemini chunks yet")
    if not gemini_client.ready():
        raise HTTPException(400, gemini_client.missing_reason() or "Gemini is not configured")
    if jobs.script_busy(pid):
        return {"ok": False, "message": "already queued/running"}
    force = bool(body.force) if body else False
    if not force and (p.get("script_status") or "") == "ready":
        return {"ok": False, "message": "script already exists — pass force to redo"}
    jobs.enqueue_script(pid, "analyze")
    return {"ok": True, "message": "queued"}


@app.post("/api/projects/{pid}/tts")
def start_tts(pid: str):
    p = _get_or_404(pid)
    if not scriptgen.load_script(pid):
        raise HTTPException(400, "generate the script first")
    if jobs.script_busy(pid):
        return {"ok": False, "message": "already queued/running"}
    jobs.enqueue_script(pid, "tts")
    return {"ok": True, "message": "queued"}


@app.get("/api/projects/{pid}/script")
def get_script(pid: str):
    p = _get_or_404(pid)
    return script_public(p)


@app.get("/api/projects/{pid}/edit")
def get_edit(pid: str):
    p = _get_or_404(pid)
    edit = render.load_or_create_edit(pid)
    data = render.edit_payload(p, edit)
    data["render"] = render_public(p)
    return data


@app.put("/api/projects/{pid}/edit")
def put_edit(pid: str, body: EditBody):
    p = _get_or_404(pid)
    current = render.load_or_create_edit(pid)
    merged = render.merge_edit(current, body.edit)
    edit = render.normalize_edit(merged, p.get("settings_json") or {})
    render.save_edit(pid, edit)
    data = render.edit_payload(p, edit)
    data["render"] = render_public(db.get_project(pid) or p)
    return data


@app.post("/api/projects/{pid}/edit/reset")
def reset_edit(pid: str):
    p = _get_or_404(pid)
    edit = render.normalize_edit(render.default_edit(p.get("settings_json") or {}),
                                 p.get("settings_json") or {})
    render.save_edit(pid, edit)
    data = render.edit_payload(p, edit)
    data["render"] = render_public(db.get_project(pid) or p)
    return data


# --------------------------------------------------------------------------
# settings / narrator audition / saved presets
# --------------------------------------------------------------------------

@app.put("/api/projects/{pid}/settings")
def put_settings(pid: str, body: SettingsBody):
    p = _get_or_404(pid)
    merged = {**(p.get("settings_json") or {}), **(body.settings or {})}
    settings = normalize_settings(merged)
    db.update_project(pid, settings_json=settings)
    # Ratio, resolution and fairuse are also stored in edit.json, which is what
    # the render actually reads. Keep them in sync when the user changes them in
    # Settings, otherwise Render would use the old values.
    if render.edit_path(pid).exists():
        edit = render.load_or_create_edit(pid)
        edit["ratio"] = settings["ratio"]
        edit["resolution"] = settings["resolution"]
        edit["fairuse"] = settings["fairuse"]
        render.save_edit(pid, render.normalize_edit(edit, settings))
    updated = db.get_project(pid) or p
    return {"ok": True, "settings": settings, "project": project_public(updated)}


@app.get("/api/voice/preview")
def voice_preview_audio(language: str = "my", narrator_id: str = "F01",
                        rate: int = 0, pitch: int = 0):
    settings = normalize_settings({
        "language": language,
        "narrator_id": narrator_id,
        "voice_rate": rate,
        "voice_pitch": pitch,
    })
    try:
        path, media = voice_preview.sample(settings)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"voice preview failed: {exc}") from exc
    return FileResponse(
        path,
        media_type=media,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


def preset_public(preset: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": preset["id"],
        "name": preset["name"],
        "created": preset.get("created"),
        "updated": preset.get("updated"),
        "settings": preset.get("settings_json") or {},
        "edit": preset.get("edit_json") or {},
        "has_edit": bool(preset.get("edit_json")),
    }


@app.get("/api/presets")
def list_presets():
    return {"presets": [preset_public(x) for x in db.list_presets()]}


@app.post("/api/presets")
def save_preset(body: PresetBody):
    settings: dict[str, Any] = {}
    edit: dict[str, Any] = {}
    if body.project_id:
        p = _get_or_404(body.project_id)
        settings = dict(p.get("settings_json") or {})
        edit = render.load_or_create_edit(body.project_id)
    if body.settings:
        settings = {**settings, **body.settings}
    if body.edit:
        edit = render.merge_edit(edit, body.edit)
    if not settings:
        raise HTTPException(400, "nothing to save — pass a project_id or settings")
    settings = normalize_settings(settings)
    edit = render.normalize_edit(edit, settings)
    # Settings and the editor snapshot must agree, otherwise applying the preset
    # later restores a stale ratio/fairuse from the edit blob.
    edit["ratio"] = settings["ratio"]
    edit["resolution"] = settings["resolution"]
    edit["fairuse"] = settings["fairuse"]
    edit = render.normalize_edit(edit, settings)
    preset = db.create_preset(body.name, settings, edit)
    return preset_public(preset)


@app.post("/api/presets/{preset_id}/apply")
def apply_preset(preset_id: str, body: PresetApply):
    preset = db.get_preset(preset_id)
    if not preset:
        raise HTTPException(404, "preset not found")
    p = _get_or_404(body.project_id)
    settings = normalize_settings(preset.get("settings_json") or {})
    db.update_project(p["id"], settings_json=settings)
    edit = preset.get("edit_json") or {}
    if not edit:
        edit = render.load_or_create_edit(p["id"])
    edit["ratio"] = settings["ratio"]
    edit["resolution"] = settings["resolution"]
    edit["fairuse"] = settings["fairuse"]
    edit = render.normalize_edit(edit, settings)
    render.save_edit(p["id"], edit)
    p = db.get_project(p["id"]) or p
    data = render.edit_payload(p, edit)
    data["render"] = render_public(p)
    data["project"] = project_public(p)
    return data


@app.delete("/api/presets/{preset_id}")
def remove_preset(preset_id: str):
    if not db.delete_preset(preset_id):
        raise HTTPException(404, "preset not found")
    return {"ok": True}


@app.post("/api/projects/{pid}/render")
def start_render(pid: str, body: RenderAction | None = None):
    p = _get_or_404(pid)
    edit = render.load_or_create_edit(pid)
    try:
        render.build_plan(p, edit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    if jobs.render_busy(pid):
        return {"ok": False, "message": "already queued/running"}
    jobs.enqueue_render(pid)
    return {"ok": True, "message": "queued"}


@app.get("/api/projects/{pid}/render")
def get_render(pid: str):
    p = _get_or_404(pid)
    return render_public(p)


@app.get("/api/projects/{pid}/preview")
def get_preview(pid: str, t: float = 0, w: int = 960):
    _get_or_404(pid)
    try:
        path = render.render_preview(pid, t, w)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/api/projects/{pid}/file/{kind}")
def get_file(pid: str, kind: str, index: int | None = None, download: int = 0):
    p = _get_or_404(pid)
    root = ingest.project_dir(pid).resolve()
    meta = p.get("meta_json") or {}
    mapping = {
        "source": p.get("source_path"),
        "proxy": p.get("proxy_path"),
        "audio": p.get("audio_path"),
        "thumb": p.get("thumb_path"),
        "narration": meta.get("narration_wav"),
        "srt": meta.get("srt_path"),
        "script": meta.get("script_json"),
        "timeline": meta.get("timeline_json"),
        "output": p.get("render_path"),
        "edit": str(render.edit_path(pid)),
    }
    if kind == "chunk":
        if index is None:
            raise HTTPException(400, "index required")
        path = root / "gemini" / f"chunk_{int(index):03d}.mp4"
    else:
        raw = mapping.get(kind)
        if not raw:
            raise HTTPException(404, f"{kind} not ready")
        path = Path(raw)
    try:
        resolved = path.resolve()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "file not found") from exc
    if not str(resolved).startswith(str(root)) or not resolved.exists():
        raise HTTPException(404, "file not found")
    media = {
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
        ".webm": "video/webm", ".avi": "video/x-msvideo",
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".jpg": "image/jpeg", ".png": "image/png",
        ".srt": "text/plain; charset=utf-8", ".json": "application/json",
    }.get(resolved.suffix.lower(), "application/octet-stream")
    filename = resolved.name if download else None
    return FileResponse(resolved, media_type=media, filename=filename)


@app.get("/api/projects/{pid}/log")
def get_log(pid: str):
    p = _get_or_404(pid)
    return {"log": p.get("log") or ""}


def _port_in_use(port: int) -> bool:
    """True when something already listens on the app port.

    uvicorn runs the lifespan (which marks running jobs as interrupted) before
    it binds the socket, so a second `run.bat` window would otherwise kill the
    jobs of the server that is already running.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return True
    return False


if __name__ == "__main__":
    if _port_in_use(config.APP_PORT):
        print(
            f"[startup] port {config.APP_PORT} is already in use — "
            f"Cinesip Recap is probably already running: "
            f"http://127.0.0.1:{config.APP_PORT}",
            flush=True,
        )
        print("[startup] use the open window, or close it and run run.bat again.", flush=True)
        sys.exit(0)
    uvicorn.run(app, host="0.0.0.0", port=config.APP_PORT)
