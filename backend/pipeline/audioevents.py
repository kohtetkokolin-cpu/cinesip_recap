"""Cinesip Recap — YAMNet audio event timeline (100% local, free).

Scans the movie's audio with Google's YAMNet (TFLite, ~16MB) and produces a
timeline of semantic sound events: speech, crying, laughter, gunshots,
explosions, music, silence, ...

Used by SmartSync to match narration mood to footage (e.g. narration says
"cries" -> prefer segments where YAMNet heard crying).
"""
import os
import urllib.request

import numpy as np

from .. import config

MODEL_DIR = os.path.join(config.DATA_DIR, "models")
os.makedirs(MODEL_DIR, exist_ok=True)

YAMNET_URL = "https://tfhub.dev/google/lite-model/yamnet/tflite/1?lite-format=tflite"
CLASSMAP_URL = ("https://raw.githubusercontent.com/tensorflow/models/master/"
                "research/audioset/yamnet/yamnet_class_map.csv")

WIN_SEC = 3.0   # classify in 3-second windows
TOP_K = 3       # keep top-3 classes per window
MIN_SCORE = 0.15


def ensure_model(progress_cb=None):
    p = os.path.join(MODEL_DIR, "yamnet.tflite")
    if not os.path.isfile(p) or os.path.getsize(p) < 1_000_000:
        if progress_cb:
            progress_cb("downloading yamnet")
        urllib.request.urlretrieve(YAMNET_URL, p + ".tmp")
        os.replace(p + ".tmp", p)
    cp = os.path.join(MODEL_DIR, "yamnet_classes.txt")
    if not os.path.isfile(cp):
        raw = urllib.request.urlopen(CLASSMAP_URL, timeout=30).read().decode()
        names = [ln.split(",")[2].strip().strip('"').strip()
                 for ln in raw.strip().split("\n")[1:]]
        with open(cp, "w", encoding="utf-8") as f:
            f.write("\n".join(names))
    with open(cp, encoding="utf-8") as f:
        names = [ln.strip() for ln in f if ln.strip()]
    return p, names


def _wav16k(video_path, tmp_wav):
    import subprocess
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", video_path,
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tmp_wav],
                   check=True)
    import wave
    with wave.open(tmp_wav, "rb") as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def analyze_audio_events(video_path, progress_cb=None):
    """Returns [{"t": sec, "events": [(class, score), ...]}, ...]."""
    from ai_edge_litert.interpreter import Interpreter
    import tempfile

    model_p, names = ensure_model(progress_cb)
    it = Interpreter(model_path=model_p)
    inp = it.get_input_details()[0]
    win_n = int(WIN_SEC * 16000)
    it.resize_tensor_input(inp["index"], [win_n])
    it.allocate_tensors()
    out0 = it.get_output_details()[0]

    with tempfile.TemporaryDirectory(prefix="cinesip_yam_") as td:
        wav = os.path.join(td, "a.wav")
        audio = _wav16k(video_path, wav)

    n_win = int(len(audio) // win_n)
    timeline = []
    for i in range(n_win):
        chunk = audio[i * win_n:(i + 1) * win_n]
        it.set_tensor(inp["index"], chunk.astype(np.float32))
        it.invoke()
        scores = it.get_tensor(out0["index"]).reshape(-1, len(names)).mean(axis=0)
        top = np.argsort(scores)[-TOP_K:][::-1]
        evs = [(names[j], round(float(scores[j]), 2))
               for j in top if scores[j] >= MIN_SCORE]
        if evs:
            timeline.append({"t": round(i * WIN_SEC, 1), "events": evs})
        if progress_cb and i % 100 == 0:
            progress_cb(f"audio {i}/{n_win}")
    return {"timeline": timeline, "n_windows": n_win}
