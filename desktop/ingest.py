"""Ingest pipeline: probe -> 360p proxy -> audio -> 10-minute chunks -> thumb."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import config
import db
import ffmpeg_utils as ff


def project_dir(pid: str) -> Path:
    safe = "".join(ch for ch in str(pid) if ch.isalnum() or ch in "_-")[:64]
    return config.PROJECTS_DIR / safe


def _update(pid: str, *, status: str | None = None, progress: float | None = None,
            message: str | None = None, **fields) -> None:
    if status is not None:
        fields["status"] = status
    if progress is not None:
        fields["progress"] = round(max(0.0, min(100.0, progress)), 1)
    if message is not None:
        fields["message"] = message
    db.update_project(pid, **fields)


def _log(pid: str, message: str) -> None:
    db.append_log(pid, message)


def run_ingest(pid: str) -> None:
    project = db.get_project(pid)
    if not project:
        raise RuntimeError("project not found")
    source = project.get("source_path") or ""
    src = Path(source)
    if not src.exists() or src.stat().st_size < 1024:
        raise RuntimeError("source video is missing")

    root = project_dir(pid)
    proxy_dir = root / "proxy"
    audio_dir = root / "audio"
    chunks_dir = root / "gemini"
    thumb_dir = root / "thumbs"
    for d in (proxy_dir, audio_dir, chunks_dir, thumb_dir):
        d.mkdir(parents=True, exist_ok=True)

    _log(pid, "Ingest စတင်သည် — ဗီဒီယိုကို စစ်ဆေးနေသည်")
    _update(pid, status="probing", progress=2, message="ဗီဒီယို စစ်ဆေးနေသည်...")
    info = ff.probe(src)
    if info["duration"] <= 0:
        raise RuntimeError("video duration is zero")
    _update(
        pid, duration=info["duration"], width=info["display_width"],
        height=info["display_height"], fps=info["fps"],
        video_codec=info["video_codec"], audio_codec=info["audio_codec"],
        size_bytes=info["size"],
    )
    _log(
        pid,
        f"ဗီဒီယို: {ff.fmt_duration(info['duration'])}, "
        f"{info['display_width']}x{info['display_height']}, {info['fps']:.3f}fps, "
        f"အသံ={'ရှိ' if info['has_audio'] else 'မရှိ'}",
    )

    proxy = proxy_dir / f"proxy_{config.PROXY_HEIGHT}p.mp4"
    if proxy.exists():
        proxy.unlink()
    _update(pid, status="proxying", progress=5, message=f"{config.PROXY_HEIGHT}p proxy ပြုလုပ်နေသည်...")
    _log(pid, f"{config.PROXY_HEIGHT}p proxy ပြုလုပ်နေသည် (Gemini အတွက်)")

    vf = f"scale=-2:{config.PROXY_HEIGHT}:flags=lanczos,fps={config.PROXY_FPS}"
    args = ["-i", str(src), "-map", "0:v:0", "-sn", "-dn", "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-force_key_frames", "expr:gte(t,n_forced*2)"]
    if info["has_audio"]:
        args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "96k", "-ac", "2"]
    args += ["-movflags", "+faststart", str(proxy)]

    def proxy_progress(frac: float) -> None:
        _update(pid, progress=5 + frac * 55,
                message=f"{config.PROXY_HEIGHT}p proxy {int(frac * 100)}%")

    ff.run_ffmpeg(args, duration=info["duration"], on_progress=proxy_progress, label="360p proxy")
    if not proxy.exists() or proxy.stat().st_size < 1024:
        raise RuntimeError("proxy output was not created")
    _update(pid, proxy_path=str(proxy), progress=60)
    _log(pid, f"Proxy ပြီး: {proxy.stat().st_size / 1024 / 1024:.1f} MB")

    audio = audio_dir / "audio_16k.wav"
    _update(pid, status="audio", progress=62, message="အသံ ခွဲထုတ်နေသည်...")
    if info["has_audio"]:
        ff.run_ffmpeg(
            ["-i", str(src), "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(audio)],
            duration=info["duration"], label="audio extract",
        )
        _update(pid, audio_path=str(audio), progress=72)
        _log(pid, "အသံ ခွဲထုတ်ပြီး (16kHz mono WAV)")
    else:
        _log(pid, "မူရင်းမှာ အသံမရှိပါ — audio extract ကျော်မယ်")

    _update(pid, status="chunking", progress=74,
            message=f"{config.GEMINI_CHUNK_SEC // 60} မိနစ် chunk များ ခွဲနေသည်...")
    chunks = split_chunks(pid, proxy, info["duration"], chunks_dir)
    _update(pid, progress=94)
    _log(pid, f"Gemini chunk {len(chunks)} ခု ခွဲပြီး")

    thumb = thumb_dir / "thumb.jpg"
    try:
        _update(pid, status="thumbnail", progress=95, message="Thumbnail ပြုလုပ်နေသည်...")
        thumb_time = min(max(info["duration"] * 0.1, 1.0), max(info["duration"] - 1.0, 1.0))
        ff.run_ffmpeg(
            ["-ss", f"{thumb_time:.3f}", "-i", str(src), "-frames:v", "1",
             "-vf", "scale=640:-2", "-q:v", "3", str(thumb)],
            label="thumbnail",
        )
        _log(pid, "Thumbnail ပြီး")
    except Exception as exc:  # noqa: BLE001
        _log(pid, f"Thumbnail မရ — ဆက်သွားမယ်: {exc}")
        thumb = None

    meta = {
        "probe": info,
        "proxy": str(proxy),
        "audio": str(audio) if audio.exists() else "",
        "thumb": str(thumb) if thumb and thumb.exists() else "",
        "chunks": chunks,
        "gemini_chunk_sec": config.GEMINI_CHUNK_SEC,
        "proxy_height": config.PROXY_HEIGHT,
        "proxy_fps": config.PROXY_FPS,
    }
    (root / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _update(
        pid, status="ready", progress=100, message="အဆင်သင့်ဖြစ်ပြီ — Settings ဆက်လုပ်နိုင်သည်",
        thumb_path=str(thumb) if thumb and thumb.exists() else "", meta_json=meta,
    )
    _log(pid, "Ingest ပြီးစီး — project အဆင်သင့်ဖြစ်သည်")


def _boundaries(duration: float, targets: list[float], keys: list[float]) -> list[float]:
    """Snap requested cut points up to the next keyframe.

    Cutting MP4 with `-c copy` can only start on a keyframe. Snapping keeps
    chunks contiguous (no gap, no duplicated footage) while staying close to
    the requested chunk length.
    """
    bounds = [0.0]
    for target in targets:
        if target >= duration - 0.05:
            break
        if keys:
            snapped = next((k for k in keys if k >= target - 0.001), None)
            if snapped is None or snapped >= duration - 0.05:
                break
        else:
            snapped = target
        if snapped > bounds[-1] + 0.05:
            bounds.append(round(snapped, 3))
    if duration > bounds[-1] + 0.05:
        bounds.append(round(duration, 3))
    return bounds


def split_chunks(pid: str, proxy: Path, duration: float, out_dir: Path) -> list[dict]:
    for old in out_dir.glob("chunk_*.mp4"):
        old.unlink()

    step = max(1, int(config.GEMINI_CHUNK_SEC))
    targets = [float(i * step) for i in range(1, int(duration // step) + 2)]
    try:
        keys = ff.keyframe_times(proxy)
    except Exception as exc:  # noqa: BLE001
        _log(pid, f"Keyframe ဖတ်မရ — exact re-encode mode: {exc}")
        keys = []

    bounds = _boundaries(duration, targets, keys)
    total = max(1, len(bounds) - 1)
    chunks: list[dict] = []
    for index in range(total):
        start = bounds[index]
        end = bounds[index + 1]
        length = max(0.0, end - start)
        if length <= 0.2:
            break
        out = out_dir / f"chunk_{index:03d}.mp4"

        if index + 1 == total:
            if total == 1:
                shutil.copyfile(proxy, out)
            else:
                ff.run_ffmpeg(
                    ["-ss", f"{start:.3f}", "-i", str(proxy),
                     "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                     "-c", "copy", "-avoid_negative_ts", "make_zero", str(out)],
                    label=f"chunk {index}",
                )
        else:
            ff.run_ffmpeg(
                ["-ss", f"{start:.3f}", "-i", str(proxy), "-t", f"{length:.3f}",
                 "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                 "-c", "copy", "-avoid_negative_ts", "make_zero", str(out)],
                label=f"chunk {index}",
            )

        try:
            cdur = ff.probe(out)["duration"]
        except Exception:
            cdur = length

        # Stream-copy cuts can still be off if the proxy has sparse keyframes.
        if abs(cdur - length) > 0.5:
            ff.run_ffmpeg(
                ["-ss", f"{start:.3f}", "-i", str(proxy), "-t", f"{length:.3f}",
                 "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                 "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
                 "-avoid_negative_ts", "make_zero", str(out)],
                label=f"chunk {index} reencode",
            )
            try:
                cdur = ff.probe(out)["duration"]
            except Exception:
                cdur = length

        chunks.append({
            "index": index,
            "start": round(start, 3),
            "end": round(start + cdur, 3),
            "duration": round(cdur, 3),
            "file": out.name,
            "size": out.stat().st_size if out.exists() else 0,
        })
        _update(pid, progress=74 + ((index + 1) / total) * 20,
                message=f"Chunk {index + 1}/{total} ခွဲပြီး")
    return chunks
