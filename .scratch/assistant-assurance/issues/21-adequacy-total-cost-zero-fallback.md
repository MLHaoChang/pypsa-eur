<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Adequacy report: a non-finite objective reads as a total system cost of 0

Status: needs-triage
Type: task
Found by: GK B on the AA-10 S4 plan (`10-metric-calculation-and-validity.md`, A4.6 metric parity), 2026-11-17.

## Problem

`services/adequacy/report.py:514-515` sets `total_system_cost = 0.0` when `n.objective` is not finite (missing, NaN or not a number). A cost that was not established reads as a real zero, which breaks the never-0 rule (G3) outside checked answers: an unavailable value is never shown as 0 or blank.

The value leaves the report as `cost.total_system_cost_eur` and is read by:

- `GET /results/adequacy`;
- the results workbook (`services/results_workbook/views_adequacy.py:108`, "System cost excluding shed");
- `services/adequacy/frontier.py` (frontier points and their cost deltas);
- `services/adequacy/eh_study.py` (cost at target);
- `services/adequacy/margin_loop_runner.py` (each margin step's cost);
- `services/chat_tools.py` `_adequacy_payload` (the in-app assistant).

Several of these do arithmetic on it (deltas, comparisons), so a 0 propagates as a number.

## Scope

Needs its own plan, reviewed by GK B before any code change: what each reader does with a cost that is not established (None with a reason, as checked answers do), and the tests that pin it for each reader above. No code change is made by S4.
