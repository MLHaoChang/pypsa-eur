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


def test_allowed_models_is_exactly_those_two():
    assert chat_service.ALLOWED_MODELS == frozenset(
        {"claude-sonnet-5", "claude-opus-5"}
    )


def test_an_unknown_model_is_not_in_the_allow_list():
    """
    NOTE: this only checks set membership in `ALLOWED_MODELS`, not that a
    request naming an unknown model is refused. `ALLOWED_MODELS` is
    currently declarative, not enforced: `routers/chat.py` types the
    request's `model` field as `str | None` and passes it straight through
    to the SDK without checking it against this set. If callers need actual
    refusal of unknown models, that enforcement does not exist yet.
    """
    assert "claude-sonnet-4-6" not in chat_service.ALLOWED_MODELS
    assert "gpt-4" not in chat_service.ALLOWED_MODELS


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
