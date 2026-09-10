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


def redact_secrets_in_str(text: str) -> str:
    """Apply the secret patterns to one string. Order: key=val, bearer, sk-ant."""
    text = SECRET_KV_RE.sub(r"\1=[REDACTED]", text)
    text = BEARER_RE.sub("bearer [REDACTED]", text)
    text = SK_ANT_RE.sub("[REDACTED-API-KEY]", text)
    return text


def redact_for_log(value: Any) -> str:
    """
    Strip plausible secrets from a string before logging. Phase 3 invariant
    (i) — the ANTHROPIC_API_KEY literal value MUST NEVER appear in backend
    logs. We belt-and-suspender this by redacting any substring that LOOKS
    like an API key (matches the `sk-ant-*` prefix the Anthropic SDK uses)
    in addition to never explicitly passing the value to log calls.
    """
    text = str(value)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        text = text.replace(key, "[REDACTED-API-KEY]")
    # Generic shape: sk-ant-<...non-whitespace...>
    text = re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "[REDACTED-API-KEY]", text)
    return text
