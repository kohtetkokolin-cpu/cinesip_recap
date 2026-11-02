"""Small ffmpeg/ffprobe helpers with progress reporting."""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Callable


class FFmpegError(RuntimeError):
    pass


def _rate(value: str | None) -> float:
    try:
        num, den = str(value or "0/0").split("/")
        den_f = float(den)
        return float(num) / den_f if den_f else 0.0
    except Exception:
        return 0.0


def probe(path: str | Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {proc.stderr[-800:]}")
    try:
        data = json.loads(proc.stdout)
    except Exception as exc:
        raise FFmpegError(f"ffprobe returned invalid JSON: {exc}") from exc

    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise FFmpegError("No video stream found")

    rotation = 0
    for side in video.get("side_data_list") or []:
        if isinstance(side, dict) and "rotation" in side:
            try:
                rotation = int(float(side["rotation"]))
            except Exception:
                pass
    if not rotation:
        try:
            rotation = int(float((video.get("tags") or {}).get("rotate", 0)))
        except Exception:
            rotation = 0

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if abs(rotation) % 180 == 90:
        display_width, display_height = height, width
    else:
        display_width, display_height = width, height

    try:
        duration = float(fmt.get("duration") or video.get("duration") or 0)
    except Exception:
        duration = 0.0

    return {
        "duration": round(duration, 3),
        "size": int(fmt.get("size") or 0),
        "format": fmt.get("format_name") or "",
        "width": width,
        "height": height,
        "display_width": display_width,
        "display_height": display_height,
        "rotation": rotation,
        "fps": round(_rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate")), 4),
        "video_codec": video.get("codec_name") or "",
        "pix_fmt": video.get("pix_fmt") or "",
        "has_audio": bool(audio),
        "audio_codec": (audio or {}).get("codec_name") or "",
        "channels": int((audio or {}).get("channels") or 0),
        "sample_rate": int((audio or {}).get("sample_rate") or 0),
    }


def duration_seconds(path: str | Path) -> float:
    """Format duration for any media file (audio-only included)."""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {proc.stderr[-800:]}")
    try:
        fmt = (json.loads(proc.stdout) or {}).get("format") or {}
        return round(float(fmt.get("duration") or 0), 3)
    except Exception as exc:
        raise FFmpegError(f"ffprobe returned invalid JSON: {exc}") from exc


def run_ffmpeg(
    args: list[str],
    *,
    duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    timeout: float | None = None,
    label: str = "ffmpeg",
    cwd: str | Path | None = None,
) -> None:
    """Run ffmpeg and optionally report 0..1 progress.

    `args` must be everything after the ffmpeg binary (input, filters, output).
    `cwd` keeps filter-script and overlay paths relative, which avoids the
    Windows drive-letter escaping mess inside filter graphs.
    """
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-nostats", "-progress", "pipe:1",
    ] + args
    started = time.time()
    tail: list[str] = []
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            tail.append(line)
            if len(tail) > 60:
                tail.pop(0)
            if on_progress and duration and duration > 0:
                frac = None
                if line.startswith("out_time_us="):
                    frac = _to_seconds(line.split("=", 1)[1]) / duration
                elif line.startswith("out_time_ms="):
                    frac = _to_seconds(line.split("=", 1)[1]) / duration
                elif line.startswith("out_time="):
                    frac = _to_seconds(line.split("=", 1)[1]) / duration
                elif line == "progress=end":
                    frac = 1.0
                if frac is not None:
                    on_progress(max(0.0, min(1.0, frac)))
            if timeout and (time.time() - started) > timeout:
                proc.kill()
                raise FFmpegError(f"{label} timed out after {timeout:.0f}s")
        rc = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    if rc != 0:
        raise FFmpegError(f"{label} failed (exit {rc}): " + " | ".join(tail[-12:]))


def _to_seconds(value: str) -> float:
    value = value.strip()
    try:
        return float(value) / 1_000_000.0
    except ValueError:
        pass
    m = re.match(r"^(\d+):(\d+):(\d+(?:\.\d+)?)$", value)
    if m:
        h, mnt, sec = m.groups()
        return int(h) * 3600 + int(mnt) * 60 + float(sec)
    return 0.0


def keyframe_times(path: str | Path) -> list[float]:
    """Return sorted keyframe timestamps (seconds) of the first video stream.

    Used to snap chunk cuts onto keyframes so `-c copy` splitting is exact and
    does not duplicate footage across neighbouring chunks.
    """
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
        "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe keyframes failed: {proc.stderr[-400:]}")
    times: set[float] = set()
    for line in proc.stdout.splitlines():
        try:
            value = float(line.strip().strip(","))
        except ValueError:
            continue
        if value >= 0:
            times.add(round(value, 3))
    return sorted(times)


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(float(seconds or 0))))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"
