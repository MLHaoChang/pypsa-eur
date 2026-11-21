<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 59 — `test_ac_pf_second_run.py` describes the pre-PF-SAVE-1 flow

Status: needs-triage
Gated: GK
Type: docs

## What is wrong

The module docstring of `pypsa-gui/backend/tests/test_ac_pf_second_run.py` says the first AC power flow "overwrites the LP flows and voltages in place and keeps the LP's aside in `lopf_results`". Since issue 50 (#1616, PF-SAVE-1) the live network keeps the LP's tables and the flow's tables live only in `solver_state["ac_pf_results"]`. The tests still pass; only the docstring's account of why a second flow is refused is out of date.

Found while landing #1616; not blocking there.

## Acceptance

- The docstring states the refusal reason in terms of the PF-SAVE-1 state (`lopf_results`, `ac_pf_results`), or the refusal is re-examined if it no longer needs to exist.
- No test behaviour changes unless the re-examination finds the refusal unneeded.

## Journey fit

None (test documentation). Background for anyone touching the AC PF second-run guard.
