"""SQLite storage for Cinesip Recap.

One process, a handful of tables, no ORM. Every call opens a short-lived
connection so background ingest threads and request handlers never share a
cursor.
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
import time
from typing import Any

import config

PROJECT_FIELDS = {
    "name", "updated", "status", "progress", "message", "error", "log",
    "source_path", "proxy_path", "audio_path", "thumb_path",
    "duration", "width", "height", "fps", "video_codec", "audio_codec",
    "size_bytes", "settings_json", "upload_filename", "upload_size",
    "upload_chunk_size", "upload_total_chunks", "meta_json",
    "script_status", "script_progress", "script_message", "script_error",
    "tts_status", "tts_progress", "tts_message", "tts_error",
    "render_status", "render_progress", "render_message", "render_error",
    "render_path", "render_meta_json",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    config.ensure_dirs()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created REAL NOT NULL,
                updated REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                progress REAL NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                log TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                proxy_path TEXT NOT NULL DEFAULT '',
                audio_path TEXT NOT NULL DEFAULT '',
                thumb_path TEXT NOT NULL DEFAULT '',
                duration REAL NOT NULL DEFAULT 0,
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                fps REAL NOT NULL DEFAULT 0,
                video_codec TEXT NOT NULL DEFAULT '',
                audio_codec TEXT NOT NULL DEFAULT '',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                settings_json TEXT NOT NULL DEFAULT '{}',
                upload_filename TEXT NOT NULL DEFAULT '',
                upload_size INTEGER NOT NULL DEFAULT 0,
                upload_chunk_size INTEGER NOT NULL DEFAULT 0,
                upload_total_chunks INTEGER NOT NULL DEFAULT 0,
                meta_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        _migrate(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS presets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created REAL NOT NULL,
                updated REAL NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                edit_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.commit()


SCRIPT_COLUMNS = {
    "script_status": "TEXT NOT NULL DEFAULT ''",
    "script_progress": "REAL NOT NULL DEFAULT 0",
    "script_message": "TEXT NOT NULL DEFAULT ''",
    "script_error": "TEXT NOT NULL DEFAULT ''",
    "tts_status": "TEXT NOT NULL DEFAULT ''",
    "tts_progress": "REAL NOT NULL DEFAULT 0",
    "tts_message": "TEXT NOT NULL DEFAULT ''",
    "tts_error": "TEXT NOT NULL DEFAULT ''",
    "render_status": "TEXT NOT NULL DEFAULT ''",
    "render_progress": "REAL NOT NULL DEFAULT 0",
    "render_message": "TEXT NOT NULL DEFAULT ''",
    "render_error": "TEXT NOT NULL DEFAULT ''",
    "render_path": "TEXT NOT NULL DEFAULT ''",
    "render_meta_json": "TEXT NOT NULL DEFAULT '{}'",
}


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations only — older databases keep working."""
    have = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    for name, ddl in SCRIPT_COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {name} {ddl}")


def new_id() -> str:
    return "p" + str(int(time.time() * 1000)) + "".join(
        random.choice("0123456789abcdef") for _ in range(4)
    )


def now() -> float:
    return time.time()


def create_project(name: str, settings: dict[str, Any]) -> dict[str, Any]:
    pid = new_id()
    ts = now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO projects (id, name, created, updated, status, settings_json)
            VALUES (?, ?, ?, ?, 'created', ?)
            """,
            (pid, name.strip()[:120] or "Untitled", ts, ts, json.dumps(settings, ensure_ascii=False)),
        )
        conn.commit()
    return get_project(pid) or {}


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("settings_json", "meta_json", "render_meta_json"):
        raw = d.get(key) or "{}"
        try:
            d[key] = json.loads(raw)
        except Exception:
            d[key] = {}
    return d


def get_project(pid: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    return _row_to_dict(row) if row else None


def list_projects(limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY updated DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_project(pid: str, **fields: Any) -> None:
    clean = {k: v for k, v in fields.items() if k in PROJECT_FIELDS}
    if not clean:
        return
    clean["updated"] = now()
    if "settings_json" in clean and not isinstance(clean["settings_json"], str):
        clean["settings_json"] = json.dumps(clean["settings_json"], ensure_ascii=False)
    if "meta_json" in clean and not isinstance(clean["meta_json"], str):
        clean["meta_json"] = json.dumps(clean["meta_json"], ensure_ascii=False)
    if "render_meta_json" in clean and not isinstance(clean["render_meta_json"], str):
        clean["render_meta_json"] = json.dumps(clean["render_meta_json"], ensure_ascii=False)
    cols = ", ".join(f"{k} = ?" for k in clean)
    vals = list(clean.values()) + [pid]
    with _connect() as conn:
        conn.execute(f"UPDATE projects SET {cols} WHERE id = ?", vals)
        conn.commit()


def append_log(pid: str, message: str, max_chars: int = 24000) -> None:
    stamp = time.strftime("%H:%M:%S")
    line = f"[{stamp}] {message}\n"
    with _connect() as conn:
        row = conn.execute("SELECT log FROM projects WHERE id = ?", (pid,)).fetchone()
        if not row:
            return
        log = (row["log"] or "") + line
        if len(log) > max_chars:
            log = log[-max_chars:]
        conn.execute(
            "UPDATE projects SET log = ?, updated = ? WHERE id = ?",
            (log, now(), pid),
        )
        conn.commit()
    try:
        print(f"[{pid}] {message}", flush=True)
    except (UnicodeEncodeError, OSError):
        sys.stdout.write(f"[{pid}] <log message with non-ASCII text>\n")
        sys.stdout.flush()


def delete_project(pid: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
        conn.commit()


# --------------------------------------------------------------------------
# named settings presets
# --------------------------------------------------------------------------

def _preset_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("settings_json", "edit_json"):
        raw = d.get(key) or "{}"
        try:
            d[key] = json.loads(raw)
        except Exception:
            d[key] = {}
    return d


def list_presets() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM presets ORDER BY updated DESC"
        ).fetchall()
    return [_preset_row(r) for r in rows]


def get_preset(preset_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM presets WHERE id = ?", (preset_id,)).fetchone()
    return _preset_row(row) if row else None


def create_preset(name: str, settings: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any]:
    preset_id = "s" + str(int(time.time() * 1000)) + "".join(
        random.choice("0123456789abcdef") for _ in range(4)
    )
    ts = now()
    clean_name = (name or "").strip()[:80] or "Preset"
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO presets (id, name, created, updated, settings_json, edit_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                preset_id,
                clean_name,
                ts,
                ts,
                json.dumps(settings or {}, ensure_ascii=False),
                json.dumps(edit or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
    return get_preset(preset_id) or {}


def delete_preset(preset_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
        conn.commit()
    return bool(cur.rowcount)


def mark_interrupted() -> int:
    """After a server restart, running jobs cannot still be running."""
    running = ("queued", "probing", "proxying", "audio", "chunking", "thumbnail")
    placeholders = ", ".join("?" for _ in running)
    with _connect() as conn:
        cur = conn.execute(
            f"""
            UPDATE projects
               SET status = 'error',
                   message = 'Server restarted — click Retry ingest',
                   error = 'ingest interrupted by server restart',
                   updated = ?
             WHERE status IN ({placeholders})
            """,
            (now(), *running),
        )
        ingest_count = cur.rowcount
        cur = conn.execute(
            """
            UPDATE projects
               SET script_status = 'error',
                   script_message = 'Server restarted — run the script again',
                   script_error = 'script interrupted by server restart',
                   updated = ?
             WHERE script_status IN ('queued', 'analyzing')
            """,
            (now(),),
        )
        script_count = cur.rowcount
        cur = conn.execute(
            """
            UPDATE projects
               SET tts_status = 'error',
                   tts_message = 'Server restarted — run the voice again',
                   tts_error = 'tts interrupted by server restart',
                   updated = ?
             WHERE tts_status IN ('queued', 'synthesizing')
            """,
            (now(),),
        )
        tts_count = cur.rowcount
        cur = conn.execute(
            """
            UPDATE projects
               SET render_status = 'error',
                   render_message = 'Server restarted — render again',
                   render_error = 'render interrupted by server restart',
                   updated = ?
             WHERE render_status IN ('queued', 'rendering')
            """,
            (now(),),
        )
        render_count = cur.rowcount
        conn.commit()
    return ingest_count + script_count + tts_count + render_count
