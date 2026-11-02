"""Gemini access for a single video chunk.

The SDK is imported lazily so the app still starts (and M1 works) when
`google-genai` is not installed or no API key is configured.

Newer flash models are sometimes overloaded (503 UNAVAILABLE). The SDK's own
retry loop hides that for many minutes, so it is disabled here and replaced
with our own chain: primary model -> fallbacks -> wait -> repeat.

`infinite=True` (used by the chunk pipeline) never gives up on a transient
error: it cycles every model, waits with an exponential backoff, and starts
again. Only configuration mistakes (missing/invalid key, permission denied)
still stop the job, because retrying those forever cannot succeed.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import config


class GeminiError(RuntimeError):
    pass


def configured() -> bool:
    return bool(config.GEMINI_API_KEY)


def ready() -> bool:
    return config.FAKE_GEMINI or configured()


def missing_reason() -> str:
    if config.FAKE_GEMINI:
        return ""
    if not configured():
        return "GEMINI_API_KEY မရှိပါ — .env သို့မဟုတ် env var ထဲ ထည့်ပါ"
    return ""


def _genai():
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise GeminiError(
            "google-genai မထည့်ရသေးပါ — `pip install google-genai` လုပ်ပါ"
        ) from exc
    return genai, types


def _client(timeout_sec: int):
    genai, types = _genai()
    # attempts=1: no silent SDK retries; we control the fallback chain.
    options = types.HttpOptions(
        timeout=int(max(30, timeout_sec) * 1000),
        retry_options=types.HttpRetryOptions(attempts=1),
    )
    return genai.Client(api_key=config.GEMINI_API_KEY, http_options=options)


def models() -> list[str]:
    """Primary model followed by the configured fallbacks (deduplicated)."""
    out: list[str] = []
    for name in [config.GEMINI_MODEL, *config.GEMINI_FALLBACKS]:
        name = (name or "").strip()
        if name and name not in out:
            out.append(name)
    return out


_RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}
_HTTP_CODE = re.compile(r"\b(\d{3})\s+[A-Z_]{3,}")


def _code(exc: Exception) -> int | None:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    match = _HTTP_CODE.search(str(exc))
    return int(match.group(1)) if match else None


def _retryable(exc: Exception) -> bool:
    code = _code(exc)
    if code is not None:
        return code in _RETRYABLE_CODES
    text = str(exc).lower()
    # 400-family answers are final; anything without a status is a transport
    # problem (reset connection, timeout, TLS drop) and is worth retrying.
    if any(
        key in text
        for key in (
            "api key",
            "invalid argument",
            "permission denied",
            "not found",
            "no longer available",
        )
    ):
        return False
    return True


def _permanent(exc: Exception) -> bool:
    """Errors that retrying or another model cannot fix."""
    code = _code(exc)
    if code in (400, 401, 403):
        return True
    text = str(exc).lower()
    return any(
        key in text
        for key in (
            "api key",
            "permission denied",
            "unauthenticated",
            "invalid argument",
        )
    )


def _delay(attempt: int) -> float:
    """Exponential backoff with a cap, in seconds."""
    base = max(2.0, float(config.GEMINI_RETRY_DELAY_SEC))
    return min(
        max(2.0, float(config.GEMINI_RETRY_MAX_WAIT_SEC)),
        base * (2 ** min(5, max(0, attempt - 1))),
    )


def _wait(seconds: float, on_status=None, message: str = "") -> None:
    """Sleep while keeping the UI message alive (shows the remaining time)."""
    end = time.time() + max(0.0, seconds)
    while True:
        left = end - time.time()
        if left <= 0:
            return
        if on_status and message:
            on_status(f"{message} · ကျန် {int(left) + 1}s")
        time.sleep(min(5.0, left))



def _state_name(file_obj) -> str:
    state = getattr(file_obj, "state", None)
    name = getattr(state, "name", None)
    if name:
        return str(name)
    return str(state or "")


def _generate(client, contents, on_status=None, label: str = "",
              *, infinite: bool = False) -> tuple[str, str]:
    """Try every model, then wait and retry.

    `infinite=True` repeats the whole fallback chain until a model answers.
    Overload (503), rate limits (429), timeouts, dropped connections and empty
    replies all count as transient. Auth/config errors still raise, because
    waiting cannot fix an invalid key.
    """
    chain = models()
    if not chain:
        raise GeminiError("Gemini model မသတ်မှတ်ထားပါ")
    rounds = max(1, config.GEMINI_RETRY_ROUNDS)
    deadline = time.time() + max(60, config.GEMINI_CHUNK_BUDGET_SEC)
    last: Exception | None = None
    attempt = 0

    while True:
        attempt += 1
        for model in chain:
            if not infinite and time.time() > deadline:
                raise GeminiError(
                    "Gemini က အလုပ်များလွန်းနေပါသည် — ခဏနေရင် ပြန်ကြိုးစားပါ"
                )
            if on_status:
                on_status(f"{label} ({model})".strip())
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config={"temperature": 0.55},
                )
            except Exception as exc:  # noqa: BLE001
                last = exc
                if _permanent(exc):
                    raise GeminiError(f"Gemini analysis failed: {exc}") from exc
                if not infinite and not _retryable(exc):
                    raise GeminiError(f"Gemini analysis failed: {exc}") from exc
                continue

            text = (getattr(response, "text", None) or "").strip()
            if text:
                return text, model
            last = GeminiError(f"{model} က စာသား မပြန်ပါ")

        if not infinite and attempt >= rounds:
            break

        wait = _delay(attempt)
        message = (
            f"Gemini မအောင်မြင်ပါ — {int(wait)}s စောင့်ပြီး "
            f"{len(chain)} model ကို ပြန်စမ်းမည်"
        )
        if on_status:
            on_status(message)
        _wait(wait, on_status, message)

    detail = str(last or "unknown error")
    raise GeminiError(
        f"Gemini က ပြန်မဖြေပါ ({', '.join(chain)}): {detail}"
    )


def analyze_chunk_video(
    path: str | Path, prompt: str, on_status=None, *, infinite: bool = False
) -> tuple[str, str]:
    """Upload one chunk, wait until Gemini activates it, return (text, model).

    With `infinite=True`, upload/activation/generation failures are retried
    forever with backoff. The uploaded file is always deleted between tries.
    """
    if config.FAKE_GEMINI:
        raise GeminiError("fake mode has no client")

    attempt = 0
    while True:
        attempt += 1
        upload_client = _client(config.GEMINI_UPLOAD_TIMEOUT_SEC)
        name = ""
        try:
            if on_status:
                on_status("Gemini သို့ chunk တင်နေသည်...")
            uploaded = upload_client.files.upload(file=str(path))
            name = getattr(uploaded, "name", "")

            deadline = time.time() + config.GEMINI_TIMEOUT_SEC
            active = None
            while time.time() < deadline:
                info = upload_client.files.get(name=name)
                state = _state_name(info)
                if state == "ACTIVE":
                    active = info
                    break
                if state in ("FAILED", "ERROR"):
                    raise GeminiError("Gemini could not process this chunk")
                time.sleep(2)

            if active is None:
                raise GeminiError("Gemini file did not become ACTIVE in time")

            if on_status:
                on_status("Gemini က chunk ကို ဖတ်နေသည်...")
            generate_client = _client(config.GEMINI_CALL_TIMEOUT_SEC)
            text, model = _generate(
                generate_client,
                [active, prompt],
                on_status=on_status,
                label="Gemini က chunk ကို ဖတ်နေသည်...",
                infinite=infinite,
            )
            return text, model
        except Exception as exc:  # noqa: BLE001
            if not infinite or _permanent(exc):
                if isinstance(exc, GeminiError):
                    raise
                raise GeminiError(f"Gemini analysis failed: {exc}") from exc
            wait = _delay(attempt)
            message = f"Gemini chunk error — {int(wait)}s စောင့်ပြီး ပြန်တင်မည်"
            if on_status:
                on_status(message)
            _wait(wait, on_status, message)
        finally:
            if name:
                try:
                    upload_client.files.delete(name=name)
                except Exception:  # noqa: BLE001
                    pass
