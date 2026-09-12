"""
The model list, and the one place that historically did not follow it.

`chat_tools.py` hardcoded `model="claude-sonnet-4-6"` for the vision sub-call
rather than referencing `DEFAULT_MODEL`, so it did not move when the constants
did. The literal-scan test below is the one that catches that class of defect;
asserting the constants alone would pass against a stranded vision path.
"""
from __future__ import annotations

import inspect
import re

from services import chat_service, chat_tools


def test_models_are_the_current_generation():
    assert chat_service.DEFAULT_MODEL == "claude-sonnet-5"
    assert chat_service.OPUS_MODEL == "claude-opus-5"


def test_legacy_model_strings_resolve_to_builtin_profiles():
    from services import llm_config
    assert llm_config.resolve_legacy_model("claude-sonnet-5").id == "anthropic-sonnet"
    assert llm_config.resolve_legacy_model("claude-opus-5").id == "anthropic-opus"
    # unknown → active profile, not a refusal (documented passthrough contract)
    assert llm_config.resolve_legacy_model("gpt-4").id == llm_config.resolve_active().id
    assert llm_config.resolve_legacy_model(None).id == llm_config.resolve_active().id


def test_no_module_hardcodes_a_model_literal():
    """
    The vision sub-call is the known offender. Scanning the source is
    deliberate: a test that called the vision path would need an API key, and
    the defect is visible statically.
    """
    offenders = []
    for module in (chat_tools, chat_service):
        source = inspect.getsource(module)
        for lineno, line in enumerate(source.splitlines(), start=1):
            if re.search(r'model\s*=\s*["\']claude-', line):
                offenders.append(f"{module.__name__}:{lineno}: {line.strip()}")
    assert offenders == [], (
        "model= must reference DEFAULT_MODEL/OPUS_MODEL, not a literal:\n"
        + "\n".join(offenders)
    )
