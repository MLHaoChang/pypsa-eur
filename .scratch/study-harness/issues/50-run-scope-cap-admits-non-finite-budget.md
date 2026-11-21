<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 50 — The run-scope reservation admits a send against a NaN or infinite cap

Status: needs-triage
Gated: GK B
Type: bug
Parent: SH 07d (#1649), GK B (65) R1 follow-up

## Problem

`harness/send_reservation.py` `cap()` returns `float(limit["cost_eur"])` for
the EUR unit (and the `output_tokens` limit the same way) with no finiteness
check, and `_check_run` admits when `_spent(run, unit) + hold > limit` is
false. A NaN cap makes that comparison false, so a run-scope or research send
is admitted with no budget at all; an infinite cap admits everything. A
negative or non-number spend in `run.budget.used` has the same gap.

#1649 fixed this for the revision scope only (`_check_revision` fails closed
unless the spend, the total, the hold and the cap are finite, spend and hold
>= 0 and cap > 0). The run and research scopes on master still have it.

## Fix

Give `_check_run` the same fail-closed guard as `_check_revision`: refuse with
`budget` (or `no_cap` where the cap is absent) unless the spend, hold and cap
are finite numbers, spend and hold >= 0 and cap > 0. A non-number refuses too.

## Tests

In `tests/test_harness_budget_reservation.py`, one parametrized case per bad
input (NaN, inf, negative, a string, `None`, a bool) for the cap and the spend,
in each mode, each killing its own mutant of the guard.
