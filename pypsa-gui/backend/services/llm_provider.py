"""
Shim — moved to `harness.protocol` (chat harness phase 0, 2026-10-05).

The module object is REPLACED in `sys.modules`, so `services.llm_provider`
and `harness.protocol` are the same module: every name is identical, and a
monkeypatch on either is seen by both. Import from `harness.protocol` in new
code; this path stays for the tests and smoke scripts that still use it.
"""
from __future__ import annotations

import sys as _sys

import harness.protocol as _target

_sys.modules[__name__] = _target
