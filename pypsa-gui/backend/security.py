"""
Trust-boundary primitives for the multi-tenant backend (Step 0a).

Three concerns live here because they are one decision, not three:

  * **Replica identity** — an opaque `X-PyPSA-Replica` response header. It is
    mandated *test* infrastructure: without it, a sticky proxy makes every
    "two replicas" assertion pass while state is still process-local. It must
    never leak topology, so it is a keyed hash of the process identity rather
    than a hostname or pid.
  * **Origin allowlist** — an explicit, env-driven set. It replaced
    `allow_origin_regex=r"https://.*\\.cursorusercontent\\.com"`, under which
    *any* page on that domain was an allowlisted **credentialed** origin: it
    passed the Origin check, CORS let it read the response, so it could read
    the double-submit token below and forge with it.
  * **CSRF** — double-submit token plus the Origin/Referer check. The session
    cookie is `SameSite=None; Secure` on any HTTPS non-local host, so the
    browser attaches it to cross-site requests; `POST /api/projects/{name}` is
    a destructive save and `DELETE /{name}?cascade=true` removes scenario
    trees. Both were cross-site forgeable before this module existed.

Widening `cors_allowed_origins` is therefore equivalent to handing CSRF-forging
capability to the added origin. Treat the two as a single review.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
import uuid
from functools import lru_cache
from urllib.parse import urlparse

from settings import get_settings

# ── Replica identity ────────────────────────────────────────────────────────

REPLICA_HEADER = "X-PyPSA-Replica"

# Regenerated per process start. Two uvicorn workers on the SAME host must get
# different ids (they are different replicas for every purpose this header
# serves), so the pid alone is not enough after a restart reuses one.
_BOOT_NONCE = uuid.uuid4().hex


@lru_cache
def replica_id() -> str:
    """
    Stable-per-process, opaque replica identifier.

    Keyed with `secret_key` so the digest cannot be reversed into a pid or host
    by a client that can guess the inputs. Stability within a process is what
    S0.7 asserts; a per-request seed would make that case pass vacuously.
    """
    key = get_settings().secret_key.encode("utf-8")
    material = f"{os.getpid()}:{_BOOT_NONCE}".encode("utf-8")
    return hmac.new(key, material, hashlib.blake2s).hexdigest()[:16]


# ── Origin allowlist ────────────────────────────────────────────────────────


def _normalise_origin(value: str) -> str:
    return value.strip().rstrip("/").lower()


@lru_cache
def allowed_origins() -> frozenset[str]:
    raw = get_settings().cors_allowed_origins or ""
    return frozenset(
        _normalise_origin(part) for part in raw.split(",") if part.strip()
    )


def is_allowed_origin(origin: str | None) -> bool:
    if not origin:
        return False
    return _normalise_origin(origin) in allowed_origins()


def origin_of_referer(referer: str | None) -> str | None:
    """Reduce a Referer URL to its scheme://host[:port] origin."""
    if not referer:
        return None
    parsed = urlparse(referer)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


# ── CSRF double-submit ──────────────────────────────────────────────────────

CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_tokens_match(cookie_token: str | None, header_token: str | None) -> bool:
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token, header_token)


# ── Login throttle ──────────────────────────────────────────────────────────
#
# Deliberately in-process: it is a real control on one host today and becomes a
# Redis/DB counter when Step 2 introduces multiple replicas. Keying on
# (client ip, email) rather than ip alone means one attacker cannot lock a
# victim out by spending that victim's budget from an unrelated address.

_attempts: dict[tuple[str, str], list[float]] = {}
_blocked_until: dict[tuple[str, str], float] = {}
_throttle_lock = threading.Lock()


def _now() -> float:
    return time.monotonic()


def login_retry_after(ip: str, email: str) -> int | None:
    """Seconds the caller must wait, or None when the attempt may proceed."""
    key = (ip, email.strip().lower())
    with _throttle_lock:
        until = _blocked_until.get(key)
        if until is None:
            return None
        remaining = until - _now()
        if remaining <= 0:
            _blocked_until.pop(key, None)
            _attempts.pop(key, None)
            return None
        return max(1, int(remaining))


def record_failed_login(ip: str, email: str) -> None:
    settings = get_settings()
    key = (ip, email.strip().lower())
    cutoff = _now() - settings.login_attempt_window_seconds
    with _throttle_lock:
        window = [t for t in _attempts.get(key, []) if t > cutoff]
        window.append(_now())
        _attempts[key] = window
        if len(window) >= settings.login_max_attempts:
            _blocked_until[key] = _now() + settings.login_block_seconds


def clear_login_attempts(ip: str, email: str) -> None:
    key = (ip, email.strip().lower())
    with _throttle_lock:
        _attempts.pop(key, None)
        _blocked_until.pop(key, None)


def reset_login_throttle_for_tests() -> None:
    with _throttle_lock:
        _attempts.clear()
        _blocked_until.clear()


def client_ip(request) -> str:
    """
    Best-effort client address.

    `X-Forwarded-For` is honoured only because the deployed topology puts a
    proxy in front; it is spoofable by a direct caller, which is acceptable for
    a throttle (worst case an attacker rotates their own bucket) and would not
    be acceptable for an authorization decision.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def reset_caches_for_tests() -> None:
    """Drop settings-derived caches after a monkeypatched settings change."""
    allowed_origins.cache_clear()
    replica_id.cache_clear()
