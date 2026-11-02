"""Background queues: ingest, script/TTS, and render workers."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import db
import ingest
import render
import scriptgen
import tts

_ingest_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cinesip-ingest")
_ingest_lock = threading.Lock()
_ingest_queued: set[str] = set()

_script_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cinesip-script")
_script_lock = threading.Lock()
_script_queued: set[tuple[str, str]] = set()

_render_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cinesip-render")
_render_lock = threading.Lock()
_render_queued: set[str] = set()


def enqueue(pid: str) -> bool:
    with _ingest_lock:
        if pid in _ingest_queued:
            return False
        _ingest_queued.add(pid)
    db.update_project(pid, status="queued", progress=0, message="တန်းစီထားသည် — မကြာမီ စမည်")
    db.append_log(pid, "Ingest တန်းစီထားသည်")
    _ingest_executor.submit(_run_ingest, pid)
    return True


def _run_ingest(pid: str) -> None:
    try:
        ingest.run_ingest(pid)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or exc.__class__.__name__
        db.update_project(pid, status="error", message="Ingest မအောင်မြင်ပါ", error=msg)
        db.append_log(pid, f"❌ Ingest error: {msg}")
    finally:
        with _ingest_lock:
            _ingest_queued.discard(pid)


def is_queued(pid: str) -> bool:
    with _ingest_lock:
        return pid in _ingest_queued


def enqueue_script(pid: str, action: str = "analyze") -> bool:
    action = "tts" if action == "tts" else "analyze"
    key = (pid, action)
    with _script_lock:
        if key in _script_queued:
            return False
        _script_queued.add(key)
    if action == "analyze":
        db.update_project(pid, script_status="queued", script_progress=0,
                          script_message="တန်းစီထားသည်...", script_error="")
        db.append_log(pid, "Script တန်းစီထားသည်")
    else:
        db.update_project(pid, tts_status="queued", tts_progress=0,
                          tts_message="တန်းစီထားသည်...", tts_error="")
        db.append_log(pid, "TTS တန်းစီထားသည်")
    _script_executor.submit(_run_script, pid, action)
    return True


def _run_script(pid: str, action: str) -> None:
    try:
        if action == "analyze":
            # Analyze + voice each 10-minute chunk in order, then merge.
            # Render is NOT queued here: the user adjusts settings/edit first
            # and presses Render when the output is ready to build.
            scriptgen.run_analysis(pid, with_voice=True)
            db.append_log(pid, "Script + Voice ပြီး — Setting ချိန်ပြီး Render နှိပ်ပါ")
        else:
            tts.run_tts(pid)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or exc.__class__.__name__
        if action == "analyze":
            db.update_project(pid, script_status="error",
                              script_message="Script မအောင်မြင်ပါ", script_error=msg,
                              tts_status="", tts_message="", tts_error="")
            db.append_log(pid, f"❌ Script error: {msg}")
        else:
            db.update_project(pid, tts_status="error",
                              tts_message="TTS မအောင်မြင်ပါ", tts_error=msg)
            db.append_log(pid, f"❌ TTS error: {msg}")
    finally:
        with _script_lock:
            _script_queued.discard((pid, action))


def script_busy(pid: str) -> bool:
    with _script_lock:
        return (pid, "analyze") in _script_queued or (pid, "tts") in _script_queued


def enqueue_render(pid: str) -> bool:
    with _render_lock:
        if pid in _render_queued:
            return False
        _render_queued.add(pid)
    db.update_project(pid, render_status="queued", render_progress=0,
                      render_message="တန်းစီထားသည်...", render_error="")
    db.append_log(pid, "Render တန်းစီထားသည်")
    _render_executor.submit(_run_render, pid)
    return True


def _run_render(pid: str) -> None:
    try:
        render.run_render(pid)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or exc.__class__.__name__
        db.update_project(pid, render_status="error",
                          render_message="Render မအောင်မြင်ပါ", render_error=msg)
        db.append_log(pid, f"❌ Render error: {msg}")
    finally:
        with _render_lock:
            _render_queued.discard(pid)


def render_busy(pid: str) -> bool:
    with _render_lock:
        return pid in _render_queued
