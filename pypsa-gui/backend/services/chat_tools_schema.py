"""
Shim — moved to `harness.catalogue` (chat harness phase 0, 2026-10-05).

The module object is REPLACED in `sys.modules`, so `services.chat_tools_schema`
and `harness.catalogue` are the same module: `TOOLS`, `TOOL_ROUTES`,
`safety_tier_for` and every enum are identical objects, and a monkeypatch on
either is seen by both. Import from `harness.catalogue` in new code; this
path stays for the tests and smoke scripts that still use it.
"""
from __future__ import annotations

import sys as _sys

import harness.catalogue as _target

_sys.modules[__name__] = _target
