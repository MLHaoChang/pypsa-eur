"""
The /stream token bucket: per-caller rate limiting for chat turns.

Moved from harness/loop.py (the former services/chat_service.py) on
2026-10-05, chat harness issue 08, by AST selection of whole top-level
nodes; the loop re-imports every function and class, so `chat_service.<name>`
is the same object, and forwards every tunable here (PEP 562), so a read
through the alias is live. A tunable is patched on THIS module (see
harness/README.md, "Splitting the loop").
"""
from __future__ import annotations

import os
import threading
import time


# /stream rate limit (#26, in-memory token bucket, keyed by session_id). 0 =
# DISABLED (default — generous, so the SSE test suite never trips). When > 0,
# each /stream call refills the session's bucket by elapsed*refill (capped at
# capacity) and admits the request iff >= 1 token remains, else 429s with a
# Retry-After header. This 429s at the HTTP layer BEFORE the SSE opens —
# distinct from the SDK-driven rate_limited frame. Read at call time.
STREAM_RATE_CAPACITY: float = float(
    os.environ.get("PYPSA_GUI_CHAT_STREAM_RATE_CAPACITY", "0")
)


STREAM_RATE_REFILL_PER_SEC: float = float(
    os.environ.get("PYPSA_GUI_CHAT_STREAM_RATE_REFILL", "0.5")
)


# ── /stream rate-limit buckets (#26) ──────────────────────────────────────
# key (session_id) -> (tokens, last_refill_monotonic). Mutated from the
# request thread under _RATE_LOCK.
_RATE_BUCKETS: dict[str, tuple[float, float]] = {}


_RATE_LOCK = threading.Lock()


def check_rate_limit(key: str) -> tuple[bool, float]:
    """
    In-memory token-bucket rate-limit check for POST /stream (#26).

    Returns `(allowed, retry_after_seconds)`. Keyed STRICTLY on the caller's
    session_id — per-session is the right granularity (a session is one
    conversation = one rate-limit subject). Per-IP / host keying is deliberately
    NOT done: under TestClient `request.client.host` is the constant
    'testclient' (every request collapses into one bucket) and behind a reverse
    proxy every request shares the proxy IP unless X-Forwarded-For is parsed —
    both out of scope here.

    Disabled when STREAM_RATE_CAPACITY <= 0 (the default) → always allows.
    Reads the module-level capacity / refill at call time so a test can
    monkeypatch them.
    """
    capacity = STREAM_RATE_CAPACITY
    refill = STREAM_RATE_REFILL_PER_SEC
    if capacity <= 0:
        return True, 0.0
    now = time.monotonic()
    with _RATE_LOCK:
        tokens, last = _RATE_BUCKETS.get(key, (capacity, now))
        # Refill by elapsed*rate, capped at capacity.
        tokens = min(capacity, tokens + max(0.0, now - last) * refill)
        if tokens >= 1.0:
            _RATE_BUCKETS[key] = (tokens - 1.0, now)
            return True, 0.0
        # Denied — keep the (sub-1) token count, advance the clock.
        _RATE_BUCKETS[key] = (tokens, now)
        retry_after = (1.0 - tokens) / refill if refill > 0 else 1.0
        return False, retry_after
