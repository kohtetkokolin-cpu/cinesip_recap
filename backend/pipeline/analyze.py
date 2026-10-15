"""Cinesip Recap — analysis stage (local-first, zero Gemini by default).

1. Transcribe with faster-whisper (CTranslate2, int8 — no PyTorch, OK on 4GB RAM).
   Audio is split into 10-minute chunks transcribed in parallel threads.
2. Detect scene cuts with PySceneDetect.
3. Merge into an evidence package cached on disk keyed by file hash, so
   re-runs never redo analysis.

Gemini is used ONLY for scene cards (optional, quota-budgeted) in script.py.
"""
import hashlib
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

from .. import config

EVIDENCE_DIR = os.path.join(config.DATA_DIR, "evidence")
os.makedirs(EVIDENCE_DIR, exist_ok=True)

CHUNK_SEC = 600  # 10-minute transcription chunks


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(blk)
    return h.hexdigest()[:16]


def extract_audio(video_path: str, out_wav: str) -> None:
    # check for an audio stream first; sources without audio get silence
    has_audio = subprocess.run(
        [config.FFPROBE, "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", video_path],
        capture_output=True, text=True).stdout.strip() != ""
    if has_audio:
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", video_path,
                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", out_wav],
                       check=True)
    else:
        dur = float(subprocess.check_output(
            [config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path], text=True).strip())
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"anullsrc=r=16000:cl=mono:d={dur:.2f}",
                        "-c:a", "pcm_s16le", out_wav], check=True)


def _transcribe_chunk(args) -> list:
    wav_path, offset, model_name = args
    # Some av builds dropped the metadata_errors kwarg faster-whisper passes;
    # dropping it is harmless (it only controls metadata parse strictness).
    try:
        import av as _av
        _o = _av.open

        def _open(*a, **k):
            k.pop("metadata_errors", None)
            return _o(*a, **k)
        _av.open = _open
    except Exception:
        pass
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(wav_path, beam_size=config.WHISPER_BEAM, vad_filter=True,
                                   vad_parameters={"min_silence_duration_ms": 500})
    out = []
    for s in segments:
        out.append({"start": round(s.start + offset, 2),
                    "end": round(s.end + offset, 2),
                    "text": s.text.strip()})
    return out


def transcribe(video_path: str, model_name: str | None = None,
               max_workers: int = 4, progress_cb=None) -> list:
    """Parallel transcription. Returns [{start, end, text}] in seconds."""
    model_name = model_name or config.WHISPER_MODEL
    with tempfile.TemporaryDirectory(prefix="cinesip_tr_") as td:
        wav = os.path.join(td, "audio.wav")
        extract_audio(video_path, wav)
        # probe duration
        dur = float(subprocess.check_output(
            [config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", wav], text=True).strip())
        chunks = []
        t = 0.0
        while t < dur:
            chunks.append((wav, t, model_name))  # offset applied after per-chunk cut
            t += CHUNK_SEC
        # cut chunk wavs (fast, no re-encode)
        jobs = []
        for i, (_, off, _) in enumerate(chunks):
            cw = os.path.join(td, f"c{i}.wav")
            subprocess.run([config.FFMPEG, "-y", "-v", "error", "-ss", str(off),
                            "-t", str(CHUNK_SEC), "-i", wav, "-c", "copy", cw],
                           check=True)
            jobs.append((cw, off, model_name))
        all_segs: list = []
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for i, segs in enumerate(ex.map(_transcribe_chunk, jobs)):
                all_segs.extend(segs)
                if progress_cb:
                    progress_cb(i + 1, len(jobs))
    all_segs.sort(key=lambda s: s["start"])
    return all_segs


def detect_scenes(video_path: str, threshold: float = 27.0) -> list:
    """Return [(start_sec, end_sec)] scene cuts. Works with scenedetect
    0.6+ (open_video) and 0.5.x (VideoManager)."""
    from scenedetect.detectors import ContentDetector
    try:
        from scenedetect import open_video, SceneManager
        video = open_video(video_path)
        sm = SceneManager()
        sm.add_detector(ContentDetector(threshold=threshold))
        sm.detect_scenes(video)
        scenes = sm.get_scene_list()
    except ImportError:
        from scenedetect import VideoManager, SceneManager
        vm = VideoManager([video_path])
        sm = SceneManager()
        sm.add_detector(ContentDetector(threshold=threshold))
        vm.start()
        sm.detect_scenes(frame_source=vm)
        scenes = sm.get_scene_list()
        vm.release()
    out = [(s[0].get_seconds(), s[1].get_seconds()) for s in scenes]
    if not out:
        dur = float(subprocess.check_output(
            [config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path], text=True).strip())
        out = [(0.0, dur)]
    return [(round(a, 2), round(b, 2)) for a, b in out]


def build_evidence(video_id: str, video_path: str, progress_cb=None) -> dict:
    """Full local analysis with disk cache. Returns evidence dict."""
    fh = file_hash(video_path)
    cache = os.path.join(EVIDENCE_DIR, f"{fh}.json")
    if os.path.isfile(cache):
        with open(cache, encoding="utf-8") as f:
            ev = json.load(f)
        ev["video_id"] = video_id
        ev["cached"] = True
        return ev

    def _p(i, n):
        if progress_cb:
            progress_cb("transcribe", i, n)

    segments = transcribe(video_path, progress_cb=_p)
    if progress_cb:
        progress_cb("scenes", 1, 1)
    scenes = detect_scenes(video_path)
    dur = scenes[-1][1] if scenes else 0.0

    # lightweight stats for the script stage
    words = sum(len(s["text"].split()) for s in segments)
    ev = {"video_id": video_id, "file_hash": fh, "duration": dur,
          "n_segments": len(segments), "n_scenes": len(scenes),
          "transcript_words": words, "segments": segments, "scenes": scenes,
          "cached": False}
    tmp = cache + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False)
    os.replace(tmp, cache)
    return ev


def evidence_summary_for_prompt(ev: dict, max_chars: int = 12000) -> str:
    """Compact transcript+scenes text for Gemini prompts (quota-safe)."""
    lines = []
    for s in ev["segments"]:
        lines.append(f"[{s['start']:.0f}-{s['end']:.0f}] {s['text']}")
    txt = "\n".join(lines)
    if len(txt) > max_chars:
        # keep head + tail (beginning and climax matter most)
        head = txt[: max_chars * 2 // 3]
        tail = txt[-max_chars // 3:]
        txt = head + "\n…[အလယ်ပိုင်း ချုံ့]…\n" + tail
    return (f"Duration: {ev['duration']/60:.1f} min, scenes: {ev['n_scenes']}\n"
            f"Transcript:\n{txt}")
