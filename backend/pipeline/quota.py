"""Cinesip Recap — Gemini quota manager.

Free tier: ~50 requests/day per API key (AI Studio). The manager rotates
keys round-robin, counts usage per key per day (persisted), and budgets
calls BEFORE a job starts so a 2-hour movie never dies mid-pipeline.

TTS and script-writing share the same pool — budget script calls first,
TTS second, edge-tts as overflow.
"""
import json
import os
import threading
import time
from datetime import date

from .. import config

QUOTA_FILE = os.path.join(config.DATA_DIR, "quota.json")
DAILY_LIMIT = 50


class QuotaManager:
    def __init__(self, keys=None):
        self.keys = keys if keys is not None else list(config.GOOGLE_API_KEYS)
        self._lock = threading.Lock()
        self._usage = self._load()  # {key_suffix: {"date": "YYYY-MM-DD", "count": n}}

    # ---------- persistence ----------
    def _load(self):
        try:
            with open(QUOTA_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self):
        tmp = QUOTA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._usage, f)
        os.replace(tmp, QUOTA_FILE)

    @staticmethod
    def _kid(key: str) -> str:
        return key[-8:] if len(key) >= 8 else key

    # ---------- rotation ----------
    def next_key(self) -> str | None:
        """Return a key with remaining quota, or None if all exhausted."""
        with self._lock:
            today = date.today().isoformat()
            # reset stale days
            for kid, u in self._usage.items():
                if u.get("date") != today:
                    u["date"] = today
                    u["count"] = 0
            # round-robin: least-used first
            cands = []
            for k in self.keys:
                u = self._usage.get(self._kid(k), {"date": today, "count": 0})
                if u["count"] < DAILY_LIMIT:
                    cands.append((u["count"], k))
            if not cands:
                return None
            cands.sort(key=lambda x: x[0])
            return cands[0][1]

    def record(self, key: str, n: int = 1):
        with self._lock:
            kid = self._kid(key)
            u = self._usage.get(kid, {"date": date.today().isoformat(), "count": 0})
            if u.get("date") != date.today().isoformat():
                u = {"date": date.today().isoformat(), "count": 0}
            u["count"] += n
            self._usage[kid] = u
            self._save()

    def remaining_total(self) -> int:
        with self._lock:
            today = date.today().isoformat()
            tot = 0
            for k in self.keys:
                u = self._usage.get(self._kid(k), {"date": today, "count": 0})
                c = 0 if u.get("date") != today else u.get("count", 0)
                tot += max(0, DAILY_LIMIT - c)
            return tot

    # ---------- budgeting ----------
    @staticmethod
    def estimate_calls(source_minutes: float, target_minutes: float,
                       beats_per_call: int = 6) -> dict:
        """Estimate Gemini calls for a recap job. Conservative (rounds up)."""
        windows = max(1, int(-(-source_minutes // 5)))          # 5-min analysis windows
        scene_calls = max(1, -(-windows // 4))                   # 4 windows batched per call
        n_beats = max(4, int(target_minutes / 3))                # ~3 min per beat
        beat_calls = max(1, -(-n_beats // beats_per_call))
        fit_calls = max(1, n_beats // 12)                        # ~8% beats need rewrite
        return {"scene_cards": scene_calls, "bible": 1, "outline": 1,
                "beats": beat_calls, "fit_rewrites": fit_calls, "metadata": 1,
                "total": scene_calls + 1 + 1 + beat_calls + fit_calls + 1,
                "n_beats": n_beats}

    def check_budget(self, source_minutes: float, target_minutes: float) -> dict:
        est = self.estimate_calls(source_minutes, target_minutes)
        # reserve 30% of the estimate for TTS (shares the pool)
        need = int(est["total"] * 1.3) + 2
        have = self.remaining_total()
        return {"ok": have >= need, "need": need, "have": have,
                "estimate": est,
                "hint": ("Quota မလုံလောက်ပါ — key အသစ်ထပ်ထည့်ပါ (GOOGLE_API_KEYS) "
                         "ဒါမှမဟုတ် မနက်ဖြန် ပြန်လုပ်ပါ။" if have < need else "")}
