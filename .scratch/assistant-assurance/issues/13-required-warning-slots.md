<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Required warning slots (A2.8)

Status: needs-triage
Type: task
Blocked by: no warning disposition exists in the result-slot contract

## Problem

Test-plan case A2.8 asks a key-results profile to fulfil the requested
**warnings** as well as the requested metrics: "A non-converged interval stays
visible and extra metrics cannot drop a required warning slot" (GK B, AA-03 S3).

Today a result slot's disposition is `answered`, `unavailable` or
`needs_clarification` (`harness/profile_common.facts_and_slots`,
`harness/assurance.py` result_slots). No profile emits a warning slot, and no
calculation marks a non-converged interval. So this requirement can't be tested:
AA-03 S3 records it as `_pending`, not covered (#918/#920 rule).

S3 pinned only the nearest behaviour it has. Unanswerable slots
(`unverified_solve`, `available_capacity_not_checked`) stay in the receipt
next to an answered slot, and `request_complete` stays false
(`tests/assurance_acceptance/test_p2_request_slots.py::test_unanswerable_slots_stay_in_the_receipt_beside_answered_ones`).
That test does **not** count as coverage for this ticket.

## Scope

- Define a warning disposition (or a required warning slot kind) in the
  receipt's result-slot contract, with the browser receipt schema.
- Emit it from at least one profile with a real non-converged source. The
  AC power-flow convergence in ticket 12 is the likely first one.
- Test first: a non-converged interval gives a required warning slot that stays
  in `result_slots` whatever extra metrics are asked, and `request_complete` is
  false while it is unresolved. Kill a mutant that drops it.

## Comments
