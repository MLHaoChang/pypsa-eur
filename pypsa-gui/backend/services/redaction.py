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
SECRET_KV_RE = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[=:]\s*(\S+)"
)
BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")
SK_ANT_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


# Values shorter than this are never substituted, even if they are a live
# managed value — an Ollama user routinely sets OPENAI_API_KEY=ollama (6
# chars), and substituting a short common word would rewrite it everywhere
# in transcripts and logs, not just where it is actually a secret.
MIN_SUBSTITUTION_LENGTH = 8


def _substitute_managed_values(text: str, values: frozenset[str]) -> str:
    """Blot out every value in `values` that is >= the length floor."""
    for value in values:
        if len(value) >= MIN_SUBSTITUTION_LENGTH and value in text:
            text = text.replace(value, "[REDACTED]")
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
    Apply the secret patterns to one string, then substitute managed values.

    `_values` lets a caller that redacts many strings in one logical
    operation (e.g. `chat_service._redact_for_persist`'s recursive walk)
    snapshot `app_secrets.live_secret_values()` ONCE and thread it through,
    instead of this function re-reading `user.env` from disk per string.
    Leave it unset for a single, self-contained call — the value set is then
    read fresh, matching the per-call-read guarantee `app_secrets` promises.
    """
    text = SECRET_KV_RE.sub(r"\1=[REDACTED]", text)
    text = BEARER_RE.sub("bearer [REDACTED]", text)
    text = SK_ANT_RE.sub("[REDACTED-API-KEY]", text)
    if _values is None:
        _values = _snapshot_values()
    text = _substitute_managed_values(text, _values)
    return text


def redact_for_log(value: Any, _values: frozenset[str] | None = None) -> str:
    """
    Strip plausible secrets from a string before logging. Phase 3 invariant
    (i) — the ANTHROPIC_API_KEY literal value MUST NEVER appear in backend
    logs. We belt-and-suspender this by redacting any substring that LOOKS
    like an API key (matches the `sk-ant-*` prefix the Anthropic SDK uses)
    in addition to never explicitly passing the value to log calls. Task 4
    widens this to every managed secret value currently in effect (see
    `redact_secrets_in_str` for the `_values` snapshot-threading contract).
    """
    text = str(value)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        text = text.replace(key, "[REDACTED-API-KEY]")
    # Generic shape: sk-ant-<...non-whitespace...>
    text = re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "[REDACTED-API-KEY]", text)
    if _values is None:
        _values = _snapshot_values()
    text = _substitute_managed_values(text, _values)
    return text
