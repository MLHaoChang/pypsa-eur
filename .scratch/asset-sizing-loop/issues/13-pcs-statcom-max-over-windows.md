<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-13 — PCS/STATCOM sizes over every rolling window or myopic period

Status: needs-triage
Type: task
Blocked by: 12
Reviewer: GK A

Spec: [spec.md](../spec.md). Raised by GK A's plan review of AS-12 (condition 2).

## Scope

The AS-05 rows are rebuilt per LP, so a rolling-horizon or myopic solve sizes S_x, Q_ss and Q_dyn once per window or period. It also charges their cost in each one. AS-12 persists only the last LP's sizes. It marks them with `n.meta["pcs_statcom_basis"] = "last_solve"` and adds a `cost_breakdown.pcs_statcom_notes` entry that reads "last solve only (rolling/myopic)".

The size every window needs is the maximum over windows. Persisting that needs a per-LP hook that reads the solution after each window or period. Either add that hook and persist the max, together with a cost basis that matches what the LPs charged, or refuse the PCS/STATCOM rows on rolling and myopic solves.

## Acceptance

- [ ] A rolling or myopic solve persists the maximum S_x per store, and the maximum Q_ss and Q_dyn, over its LPs. The alternative is a refusal, with the reason.
- [ ] `cost_breakdown` states the cost basis of those sizes, and it matches what the LPs charged.

## PRs

## Comments
