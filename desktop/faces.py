"""Face focus detection for the 150% "face zoom" recipe.

One frame per scripted scene is enough: the renderer zooms toward the largest
face found at the middle of the shot. Detection uses OpenCV's YuNet model
(bundled in `assets/faces/`), because OpenCV 5 wheels no longer ship the old
Haar cascades. The result is cached per project so previews and re-renders stay
cheap.

If OpenCV or the model is missing, or no face is found, callers fall back to
the centre of the frame.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import config

MAX_WIDTH = 640
SCORE_THRESHOLD = 0.6
NMS_THRESHOLD = 0.3
MODEL_NAME = "face_detection_yunet_2023mar.onnx"

_detector = None
_detector_failed = False


def model_path() -> Path | None:
    path = config.ASSETS_DIR / "faces" / MODEL_NAME
    return path if path.exists() else None


def available() -> bool:
    try:
        import cv2  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return hasattr(cv2, "FaceDetectorYN_create") and model_path() is not None


def _get_detector():
    global _detector, _detector_failed
    if _detector is not None or _detector_failed:
        return _detector
    try:
        import cv2  # type: ignore

        path = model_path()
        if path is None:
            raise FileNotFoundError(MODEL_NAME)
        _detector = cv2.FaceDetectorYN_create(
            str(path), "", (320, 320), SCORE_THRESHOLD, NMS_THRESHOLD, 5000
        )
    except Exception:  # noqa: BLE001
        _detector, _detector_failed = None, True
    return _detector


def _load_cache(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_cache(path: Path | None, data: dict) -> None:
    if not path:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        pass


def _detect(source: str, t: float) -> list[float] | None:
    """Return the largest face centre as [x, y] fractions, or None."""
    detector = _get_detector()
    if detector is None:
        return None
    try:
        import cv2  # type: ignore
    except Exception:  # noqa: BLE001
        return None

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        return None
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t)) * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            # Seeking can land past the last frame; retry at the exact start.
            cap.set(cv2.CAP_PROP_POS_MSEC, 0.0)
            ok, frame = cap.read()
        if not ok or frame is None:
            return None
        h, w = frame.shape[:2]
        if w > MAX_WIDTH:
            scale = MAX_WIDTH / float(w)
            frame = cv2.resize(frame, (MAX_WIDTH, int(round(h * scale))))
        h, w = frame.shape[:2]
        detector.setInputSize((w, h))
        _ok, faces = detector.detect(frame)
        if faces is None or len(faces) == 0:
            return None
        x, y, fw, fh = max(
            (row[:4] for row in faces),
            key=lambda r: float(r[2]) * float(r[3]),
        )
        return [
            round((float(x) + float(fw) / 2.0) / max(1, w), 4),
            round((float(y) + float(fh) / 2.0) / max(1, h), 4),
        ]
    except Exception:  # noqa: BLE001
        return None
    finally:
        cap.release()


def focus(source: str, t: float, cache_path: Path | None = None) -> list[float]:
    """Cached face centre for `t` seconds; centre of frame when unknown."""
    key = f"{round(max(0.0, float(t)), 1):.1f}"
    cache = _load_cache(cache_path)
    hit = cache.get(key)
    if isinstance(hit, list) and len(hit) == 2:
        try:
            return [float(hit[0]), float(hit[1])]
        except (TypeError, ValueError):
            pass
    found = _detect(source, float(t))
    cache[key] = found
    _save_cache(cache_path, cache)
    return list(found) if found else [0.5, 0.5]
