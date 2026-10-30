<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-38 — Adequacy scope refusals reach the chat with only the generic error kind

Status: done
Type: enhancement

Found by LC-36 (#954), recorded in the `LC-D13.unsupported_scope` manifest row.

The adequacy runners refuse out-of-scope inputs with a plain-string `HTTPException(422)`, and
the chat seam reports each one as `error_kind: 'tool_error'`. Examples:
- `frontier_loop_runner.py:57`: VoLL 0.
- `margin_loop_runner.py:189`: the 'rolling' solve strategy.
- the other 422s that `margin_loop_runner.py`, `mc_loop_runner.py` and `eh_study_runner.py`
  raise.

The model cannot tell a scope refusal from any other failure without parsing the message.
LC-36's tests therefore pin the guard's own text. The campaign budget refusal is typed
(`campaign_budget_exhausted`) and shows the pattern.

## Acceptance

- [x] Scope refusals of the adequacy planning tools carry a typed kind (for example
      `unsupported_scope`, or one per class). The kind is declared in
      `pypsa-gui/tool-error-kinds.d/`.
- [x] LC-36's `test_lc36_d13_scope_campaign.py` pins the typed kind in place of the guard text.

## Comments
