"""
Secret scrubbing shared by chat_service and the LLM provider modules.

Moved out of chat_service so providers can import it without a cycle
(providers must never import chat_service). Bodies are verbatim moves;
chat_service re-exports under the old underscore names because tests and
callers pin those.
"""
from __future__ import annotations

import os
import re
from typing import Any

# Module-level so the patterns compile once. SECRET_KV catches
# password=/passwd=/secret=/api_key=/token= followed by a value; BEARER
# catches 'bearer <token>'; the sk-ant-* pattern is shared with redact_for_log.
# A6 — the optional quotes are load-bearing. This used to require `=`/`:`
# IMMEDIATELY after the keyword, so `{"api_key": "sk-…"}` did not match: the
# closing quote sat between them. That is exactly the shape of a provider
# error body, and `llm_openai_compat` wraps `str(exc)` from an SDK whose
# `APIStatusError.__str__` renders precisely that JSON.
SECRET_KV_RE = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|token)[\"\']?\s*[=:]\s*[\"\']?([^\s\"\',}]+)"
)
BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")
SK_ANT_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


# Values shorter than this are never substituted, even if they are a live
# managed value — an Ollama user routinely sets OPENAI_API_KEY=ollama (6
# chars), and substituting a short common word would rewrite it everywhere
# in transcripts and logs, not just where it is actually a secret.
MIN_SUBSTITUTION_LENGTH = 8


def will_be_substituted(value: str) -> bool:
    """
    Whether the substitution pass can actually blot `value` out of a string.

    A8 — the floor above is a real trade-off, but it was invisible: nothing
    on the WRITE side stopped an operator storing a value below it, and
    nothing anywhere told them that value would travel verbatim into logs
    and `chat.jsonl`. `app_secrets.status` now reports this so the UI can
    say so; exposing it as a PREDICATE rather than re-testing the constant
    at the call site is what stops the two from drifting apart, because
    `_substitute_managed_values` asks the same question below.

    Refusing short values instead was the obvious alternative and is wrong:
    `OPENAI_API_KEY=ollama` is six characters and is exactly the case the
    floor exists to protect.
    """
    return len(value) >= MIN_SUBSTITUTION_LENGTH


def _substitute_managed_values(text: str, values: frozenset[str]) -> str:
    """
    Blot out every value in `values` that is >= the length floor.

    Longest-first (Fix round 1): if one configured secret is a substring of
    another (e.g. "shortsecret12" inside "prefixshortsecret12suffix"),
    replacing the shorter one first would consume only the middle of the
    longer value, leaving its un-matched prefix/suffix in plaintext — the
    exact original longer string would then no longer be present in `text`,
    so its own substitution pass would silently no-op. Sorting by length
    descending guarantees the longer (superset) value is always replaced as
    a whole unit before any of its substrings get a turn.
    """
    for value in sorted(values, key=len, reverse=True):
        if not will_be_substituted(value) or value not in text:
            continue
        # A managed value that is itself shaped like an Anthropic key (the
        # common case: ANTHROPIC_API_KEY's live value) keeps the specific
        # "[REDACTED-API-KEY]" marker the sk-ant-* regex pass would have
        # produced — this substitution now runs BEFORE that regex pass (Fix
        # round 1), so it must reproduce the marker or existing callers that
        # pin the specific marker text would regress.
        marker = (
            "[REDACTED-API-KEY]" if SK_ANT_RE.fullmatch(value) else "[REDACTED]"
        )
        text = text.replace(value, marker)
    return text


def _snapshot_values() -> frozenset[str]:
    """
    Read the current managed-secret value set once.

    Function-local import: `redaction` is imported by `chat_service` (and by
    the LLM provider modules, which must never import chat_service), so a
    module-level import here would need `services.app_secrets` to stay clear
    of that cycle forever. `app_secrets` currently imports only stdlib +
    `app_paths`, so there is no cycle today — this is a defensive placement,
    not evidence one exists.
    """
    from services.app_secrets import live_secret_values  # noqa: PLC0415

    return live_secret_values()


def redact_secrets_in_str(text: str, _values: frozenset[str] | None = None) -> str:
    """
    Substitute managed values, THEN apply the shape-based regex patterns.

    `_values` lets a caller that redacts many strings in one logical
    operation (e.g. `chat_service._redact_for_persist`'s recursive walk)
    snapshot `app_secrets.live_secret_values()` ONCE and thread it through,
    instead of this function re-reading `user.env` from disk per string.
    Leave it unset for a single, self-contained call — the value set is then
    read fresh, matching the per-call-read guarantee `app_secrets` promises.

    ORDER MATTERS (Fix round 1): value-substitution runs FIRST. A managed
    secret's literal value can itself contain "password=", "token=", "bearer "
    etc (people paste header fragments and full tokens as key values) — if
    the regex passes ran first, they would partially consume the secret,
    the exact original string would no longer be present in `text`, and
    `_substitute_managed_values` would silently no-op, leaking the
    un-consumed fragment.
    """
    if _values is None:
        _values = _snapshot_values()
    text = _substitute_managed_values(text, _values)
    # A7 — unconditional, i.e. NOT subject to MIN_SUBSTITUTION_LENGTH. The
    # length floor exists so a short COMMON word (an Ollama user's
    # `OPENAI_API_KEY=ollama`) is not rewritten everywhere; it must never
    # exempt the one variable the Phase 3 invariant names by hand.
    _anthropic = os.environ.get("ANTHROPIC_API_KEY")
    if _anthropic:
        text = text.replace(_anthropic, "[REDACTED-API-KEY]")
    text = SECRET_KV_RE.sub(r"\1=[REDACTED]", text)
    text = BEARER_RE.sub("bearer [REDACTED]", text)
    text = SK_ANT_RE.sub("[REDACTED-API-KEY]", text)
    return text


def redact_for_log(value: Any, _values: frozenset[str] | None = None) -> str:
    """
    Strip plausible secrets from a string before logging. Phase 3 invariant
    (i) — the ANTHROPIC_API_KEY literal value MUST NEVER appear in backend
    logs. We belt-and-suspender this by redacting any substring that LOOKS
    like an API key (matches the `sk-ant-*` prefix the Anthropic SDK uses)
    in addition to never explicitly passing the value to log calls. Task 4
    widens this to every managed secret value currently in effect (see
    `redact_secrets_in_str` for the `_values` snapshot-threading contract and
    the Fix round 1 note on why value-substitution must run before the
    shape-based patterns below).
    """
    # A7 / C-15 — delegate, do not reimplement.
    #
    # These two functions were asymmetric in BOTH directions. `redact_for_log`
    # omitted `SECRET_KV_RE`/`BEARER_RE`, so an unmanaged bearer token or
    # `api_key=` pair survived into the log. But it ALSO replaced the
    # `ANTHROPIC_API_KEY` literal unconditionally, where `redact_secrets_in_str`
    # reached it only through `_substitute_managed_values` — which is gated on
    # `MIN_SUBSTITUTION_LENGTH`. So for a short live key the DURABLE
    # `chat.jsonl` path was weaker than the log: the file you keep was less
    # scrubbed than the line you print, which inverts the intent.
    #
    # `redact_secrets_in_str` now owns every pass, including the unconditional
    # literal replace, and this is a thin `str()` wrapper over it. One
    # pipeline, so neither sink can be weaker than the other again.
    return redact_secrets_in_str(str(value), _values)
