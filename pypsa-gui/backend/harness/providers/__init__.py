"""
The provider layer: the only place in the harness where a wire is named.

    anthropic.py      the Anthropic Messages API (SDK client injected)
    openai_compat.py  any OpenAI-compatible chat-completions endpoint (httpx)
    fake.py           the scripted test double that runs the real loop

Each implements `harness.protocol.LLMProvider` and maps its own errors into
`harness.protocol.ERROR_KINDS`. Cache intent arrives as the `stable` flag;
`anthropic.py` turns it into `cache_control`, `openai_compat.py` ignores it.
Moved from `services/llm_*.py` on 2026-10-05 (chat harness issue 07); the
old paths are `sys.modules` aliases. The profile store (`services/llm_config`)
is settings, not a provider, and stays where it is.
"""
from __future__ import annotations
