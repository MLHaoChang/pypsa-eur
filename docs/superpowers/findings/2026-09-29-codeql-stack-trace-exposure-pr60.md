# CodeQL `py/stack-trace-exposure` on PR #60 — three alerts, three fixes

Date: 2026-09-29. Branch `claude/epic-allen-k2t1c4`. Precedent:
`2026-09-12-codeql-the-other-seventeen.md`, section `py/stack-trace-exposure`:
a typed, app-authored message to an authorised principal about their own
action is acceptable, but that argument fails wherever the message can carry
OS or library text. Two of these three alerts did carry such text, so they were
fixed rather than dismissed. The third alert carries our own validator text
only, and that is now explicit in the code.

Tests: `pypsa-gui/backend/tests/test_codeql_stack_trace_exposure.py`. Each test
plants the input the alert describes, and asserts that the response contains
no path, no errno and no parser or numpy text. The same tests assert that the
real exception still reaches the server log. Each fix was mutation-checked:
reverting it turns its tests red.

## 1. `routers/adequacy_worksheet.py:124` `get_stress_scenarios`

Source: `services/adequacy/stress.py` `load_scenarios_checked`, which returned
`f"stress-scenario registry unreadable: {exc}"`. For an `OSError` that text is
`[Errno 13] Permission denied: '/abs/server/path/...'`.

**Fixed.** The new helper `stress._read_failure` logs the exception with
`exc_info` and returns one of two messages:
`"... unreadable: not valid JSON (line L, column C)"` for a `JSONDecodeError`,
or `"... unreadable: could not be read"` for an `OSError`. The "unreadable"
wording is kept because the sweep refusal (`useStartFmeaSweep`) and the
existing tests use it. The frontend only passes the string through; nothing
parses the text after the prefix.

## 2. `routers/adequacy_worksheet.py:150` `get_stress_profile_packs`

Source: `stress.list_profile_packs`, `out.append({"id": sid, "error": str(exc)})`
on a `StressValidationError`. That exception type is our own, but
`load_synthetic_profile_pack` built one from `f"... unreadable: {exc}"` on
`OSError`/`JSONDecodeError`. So the typed wrapper did carry OS text: the path
of a shipped pack on the server.

**Fixed at the wrap site** with the same `_read_failure`. Every
`StressValidationError` message is now a sentence authored in `stress.py`.
Its only interpolations are the pack id, which comes from a filename matching
`[a-z0-9_-]{1,64}`, and the list of shipped pack ids. The class docstring and a
comment at the `list_profile_packs` catch record this. **Remaining alert,
if CodeQL still reports the `str(exc)` flow: dismiss as won't fix**, under the
precedent. The message is our validator's own, and the caller is an authorised
project principal viewing the pack picker.

## 3. `routers/results.py` `get_eh_readiness`, `except ValueError: 422 str(exc)`

CodeQL reported four sources. `ValueError` is also what numpy and pandas raise
from deep inside the readiness walk, for example "operands could not be
broadcast ...".

**Fixed.** The route now shows `str(exc)` only for
`eh_study.readiness_refusal_classes()`. That is `_refusal_classes()` from the
P19–P22 gate (`LeverScenarioError`, `RedundancyScenarioError`,
`DtcStressError`, `DtcPlanningError`, `DtcConfigError`, `ArchetypePackError`,
`HubBoundaryError`) plus a new `StageSelectionError`. `validate_stages` used to
raise a bare `ValueError`. It now raises `StageSelectionError`, a `ValueError`
subclass, so the existing `except ValueError` in the `POST /eh_study` path is
unchanged. Any other `ValueError` is logged with `logger.exception` and
returns 422 `"readiness could not be computed for this network"`. Every
existing readiness 422 is unchanged: unknown archetype, budget, dtc_attribution,
`pack_overrides` (which raises `HTTPException` itself, via `raise_http=True`)
and bad stages (message now pinned by a test).

## What would change these verdicts

* If a new `StressValidationError` is ever built from an exception's text, the
  alert-2 argument fails again. Route it through `_read_failure`.
* If the readiness walk gains a new typed refusal, add it to
  `readiness_refusal_classes()`. Until then its message becomes the generic
  422, which is safe but less helpful.
