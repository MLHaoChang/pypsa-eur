<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-14 — PCS/STATCOM sizes over every rolling window or myopic period

Status: ready-for-agent
Type: task
Blocked by: 12
Reviewer: GK A

Spec: [spec.md](../spec.md). Raised by GK A's plan review of AS-12 (condition 2).

## Scope

The AS-05 rows are rebuilt per LP, so a rolling-horizon or myopic solve sizes S_x, Q_ss and Q_dyn once per window or period. It also charges their cost in each one. AS-12 persists only the last LP's sizes. It marks them with `n.meta["pcs_statcom_basis"] = "last_solve"` and adds a `cost_breakdown.pcs_statcom_notes` entry that reads "last solve only (rolling/myopic)".

The size every window needs is the maximum over windows. Persisting that needs a per-LP hook that reads the solution after each window or period. Either add that hook and persist the max, together with a cost basis that matches what the LPs charged, or refuse the PCS/STATCOM rows on rolling and myopic solves.

## Decision

Owner decision AS14-MAX-1 (2026-10-31, "Keep the max, cost it once"): after each window's or period's LP, keep the largest PCS/STATCOM size seen so far and persist that. `cost_breakdown` charges the investment once, at the largest size. This replaces the "matches what the LPs charged" basis below: each LP charged the size for its own window, and the breakdown counts the one build instead, and says so.

Folded in: the PCS/STATCOM cost rows had period None, so they were missing from `by_period` on multi-period networks. Each now carries its period: a period pays for what its LP's size adds over the largest size before it, so the parts sum to the largest size's cost.

## Acceptance

- [x] A rolling or myopic solve persists the maximum S_x per store, and the maximum Q_ss and Q_dyn, over its LPs. The alternative is a refusal, with the reason.
- [x] `cost_breakdown` states the cost basis of those sizes: the largest over the LPs, counted once (AS14-MAX-1).
- [x] The PCS/STATCOM cost rows carry their period and reach `by_period` on a multi-period network.

## PRs

## Comments

GK A plan verdict (2026-10-31): GO, with these conditions.

1. The tracker reads the previous LP's S_x, Q_ss and Q_dyn into plain numbers at the first line of the next call, then drops the model. A call never reads its own model. A two-window test with different sizes catches an off-by-one.
2. The tracker survives every myopic iteration. The 2-period test uses the real myopic runner.
3. Book increments: in period k, Δ_k = max(0, size_k − the largest size before k). The by_period rows sum exactly to the `cost_terms` total, tested with a size that grows in period 2. Flat or single-period snapshots get one row.
4. An LP built with no solution is named in the basis (count and which LP), never skipped silently. A failed window already fails the solve; test that.
5. Q_ss and Q_dyn are maxed apart, so the pair can come from two LPs. The note says so. No row caps either from above (the Pst cut has Q_dyn on the ≥ side only).
6. `max_over_solves` carries the LP count; `horizon` is unchanged; an old .nc with "last_solve" still loads and reads, with a test.
7. The note says the reported total differs from the sum of the window objectives.
