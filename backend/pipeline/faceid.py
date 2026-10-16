"""Cinesip Recap — AutoSync face clustering (100% local, free).

Detects faces in sampled frames (YuNet), embeds them (SFace), and clusters
into anonymous persons (Person 1, 2, ...). Auto-naming links Burmese character
names from the script to clusters via dialogue/narration co-occurrence —
no user labeling needed.

Models (auto-downloaded once, cached):
  - face_detection_yunet_2023mar.onnx (~230KB)
  - face_recognition_sface_2021dec.onnx (~38MB)
"""
import os
import urllib.request

import cv2
import numpy as np

from .. import config

MODEL_DIR = os.path.join(config.DATA_DIR, "models")
os.makedirs(MODEL_DIR, exist_ok=True)

YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/"
             "models/face_detection_yunet/face_detection_yunet_2023mar.onnx")
SFACE_URL = ("https://github.com/opencv/opencv_zoo/raw/main/"
             "models/face_recognition_sface/face_recognition_sface_2021dec.onnx")

SIM_THRESHOLD = 0.50  # cosine similarity to join a cluster


def ensure_models(progress_cb=None):
    """Download YuNet + SFace once. Returns (yunet_path, sface_path)."""
    out = []
    for url, name in ((YUNET_URL, "yunet.onnx"), (SFACE_URL, "sface.onnx")):
        p = os.path.join(MODEL_DIR, name)
        if not os.path.isfile(p) or os.path.getsize(p) < 1000:
            if progress_cb:
                progress_cb(f"downloading {name}")
            urllib.request.urlretrieve(url, p + ".tmp")
            os.replace(p + ".tmp", p)
        out.append(p)
    return tuple(out)


def _detector(ynet_path):
    return cv2.FaceDetectorYN.create(ynet_path, "", (320, 320), 0.6, 0.3, 5000)


def _recognizer(sface_path):
    return cv2.FaceRecognizerSF.create(sface_path, "")


def sample_faces(video_path, every_sec=5.0, max_frames=400, progress_cb=None):
    """Sample frames, detect+embed faces.

    Returns list of {"t": sec, "emb": np(128,), "box": (x,y,w,h)}.
    """
    ynet_p, sface_p = ensure_models(progress_cb)
    det = _detector(ynet_p)
    rec = _recognizer(sface_p)

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur = total / fps if fps else 0.0

    step = int(max(1, round(every_sec * fps)))
    out = []
    idx = 0
    n_checked = 0
    while idx < total and len(out) < max_frames * 4 and n_checked < max_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            break
        n_checked += 1
        t = idx / fps
        h, w = frame.shape[:2]
        det.setInputSize((w, h))
        try:
            _, faces = det.detect(frame)
        except Exception:
            faces = None
        if faces is not None:
            for f in faces:
                x, y, fw, fh = (int(v) for v in f[:4])
                if fw < 40 or fh < 40:  # too small to trust
                    continue
                try:
                    aligned = rec.alignCrop(frame, f)
                    emb = rec.feature(aligned).flatten().astype(np.float32)
                    n = np.linalg.norm(emb)
                    if n > 0:
                        out.append({"t": round(t, 1), "emb": emb / n,
                                    "box": (x, y, fw, fh)})
                except Exception:
                    pass
        idx += step
        if progress_cb and n_checked % 20 == 0:
            progress_cb(f"faces {n_checked}")
    cap.release()
    return out


def cluster_embeddings(faces):
    """Greedy cosine clustering. Returns [{"times":[...], "emb": centroid}]."""
    clusters = []  # each: {"embs": [...], "times": [...]}
    for f in faces:
        best, best_sim = -1, -1.0
        for i, c in enumerate(clusters):
            cent = np.mean(c["embs"], axis=0)
            sim = float(np.dot(f["emb"], cent))
            if sim > best_sim:
                best, best_sim = i, sim
        if best_sim >= SIM_THRESHOLD:
            clusters[best]["embs"].append(f["emb"])
            clusters[best]["times"].append(f["t"])
        else:
            clusters.append({"embs": [f["emb"]], "times": [f["t"]]})
    # drop tiny clusters (likely false positives)
    clusters = [c for c in clusters if len(c["times"]) >= 3]
    out = []
    for c in clusters:
        cent = np.mean(c["embs"], axis=0)
        n = np.linalg.norm(cent)
        out.append({"times": sorted(c["times"]),
                    "n": len(c["times"]),
                    "emb": (cent / n).astype(np.float32) if n else cent})
    out.sort(key=lambda c: -c["n"])
    return out


def analyze_faces(video_path, progress_cb=None):
    """Full face pass. Returns {"clusters": [...]} (cached by caller)."""
    faces = sample_faces(video_path, progress_cb=progress_cb)
    clusters = cluster_embeddings(faces)
    return {"clusters": [
        {"id": i, "n": c["n"], "times": c["times"]} for i, c in enumerate(clusters)
    ], "n_faces": len(faces)}
