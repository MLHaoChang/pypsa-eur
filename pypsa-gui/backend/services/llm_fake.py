"""
Shim — moved to `harness.providers.fake` (chat harness issue 07, 2026-10-05).

The module object is REPLACED in `sys.modules`, so `services.llm_fake` and
`harness.providers.fake` are the same module: every name is identical and a
monkeypatch on either is seen by both. Import from `harness.providers` in
new code; this path stays for the tests and smoke scripts that still use it.
"""
from __future__ import annotations

import sys as _sys

import harness.providers.fake as _target

_sys.modules[__name__] = _target
