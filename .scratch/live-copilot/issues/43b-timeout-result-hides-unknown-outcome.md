<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-43b — A timed-out write tells the model only "tool_timeout"

Status: needs-triage
Type: bug

Found by LC-43 (`tests/test_lc43_d23_wire_gates.py`) against GK B (46) plan condition C4: "D23
tool_timeout: the model-facing outcome must not read success or completed, and the text must
state that the outcome is unknown". The manifest row `LC-D23.timeout_outcome_text` stays
pending until this is fixed.

## What happens

An approved write (`update_component`) outlives `PER_TOOL_TIMEOUT_SECONDS` on the Claude wire.

- The panel's `tool_error` frame is right: `error_kind: tool_timeout`, `_outcome.outcome:
  uncertain`, and the message "It ran longer than … s and was stopped; it may have changed
  the project."
- The `tool_result` the model is sent next is `is_error: true` with content exactly
  `tool_timeout` (`harness/loop.py`, the `TimeoutError` branch near 3911–3960). Nothing tells
  the model that the write may have landed, so it can read the error as "nothing changed" and
  retry, or tell the person nothing happened.

## Done when

The model-facing tool result of a timed-out write or destructive tool says that its outcome
is unknown (it may have changed the project) and that the project should be read back
before a retry. A read keeps the plain kind. A test on the Claude wire pins the text, and
`LC-D23.timeout_outcome_text` in `pypsa-gui/backend/smoke/family_manifest.py` turns covered.
