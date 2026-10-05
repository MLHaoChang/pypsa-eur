"""
Shim — moved to `harness.loop` (chat harness issue 08, 2026-10-05).

The module object is REPLACED in `sys.modules`, so `services.chat_service`
and `harness.loop` are the same module: every name is identical, every
`monkeypatch.setattr(chat_service, ...)` in the suite lands on the loop the
route runs, and the lazy `from services import chat_service` sites keep
working. Import from `harness.loop` in new code.
"""
from __future__ import annotations

import sys as _sys

import harness.loop as _target

_sys.modules[__name__] = _target
