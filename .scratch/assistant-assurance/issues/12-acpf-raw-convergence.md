<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A1.7: AC power flow records unknown convergence as converged

Status: needs-triage
Type: task
Blocked by: none

Split out of [02](02-evidence-identity-and-access.md) (GK B (21) plan condition A5; owner decision AA02-SCOPE-1).

## Problem

`services/ac_pf_service.py` builds the per-snapshot convergence map from PyPSA's `converged` frame. Two branches mark every snapshot as converged without evidence:

- `pf_result` has no `converged` frame (the "No info — assume all converged" branch);
- reading the frame raises (the bare `except Exception` branch).

The run then records `ac_pf_converged_count == ac_pf_total_snapshots`. The convergence gate added by issue 02 (`run_evidence._convergence`) accepts that run as fully converged, so its numbers can be checked although nothing proved convergence. The UI convergence bar also shows "n of n converged".

Spec: capture raw validity before sanitization; missing is not converged (reliability contract, A1.7).

## Acceptance

- Missing or unreadable convergence records unknown, not converged. Each snapshot is `ok: null`, or the run carries an explicit flag, and the counts no longer read `n of n`.
- `run_evidence` publishes a run with unknown convergence so that `_convergence` returns `not_known_to_have_converged`.
- The load-flow UI shows unknown convergence as unknown, not as converged and not as failed. This is a UI change, so it needs the UX reviewer.
- Test first, with a fake `pf()` result that has no frame and one whose frame raises. No test asserts the current all-converged output as correct.
