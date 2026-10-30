<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-12 — Persist PCS oversize and STATCOM results

Status: ready-for-agent
Type: task
Blocked by: 05
Reviewer: GK A

Spec: [spec.md](../spec.md). Raised by GK A's review of AS-05 (#974).

## Scope

AS-05 adds the LP variables `PCS-s_x`, `Statcom-q_ss` and `Statcom-q_dyn`, read only from `n.model` by `services.solver.pcs_statcom.solution(n)`. Persist them after the solve (with the network, so the forward check and the AS-06 loop can read them once the model is gone), and add their cost (PCS and STATCOM, typed annual x `fom_horizon_factor(n)`) to `cost_breakdown`, so the objective reconciliation still closes on a tagged network.

## Acceptance

- [x] S_x per store, Q_ss and Q_dyn survive the solve and a save/load round-trip.
- [x] `cost_breakdown` carries the PCS and STATCOM cost; the objective reconciles on a tagged network.

## PRs

- #985

## Comments

GK A plan verdict (2026-10-30): GO with conditions.

1. No silent zero. A store or Link tagged without a solved variable stays NaN. `persist_solution` returns a warning naming it, which the solve logs as `[PQ] WARN`. `cost_terms` skips it, and `cost_notes` names it in `cost_breakdown.pcs_statcom_notes`.
2. Rolling and myopic keep the last LP's sizes, with basis `n.meta["pcs_statcom_basis"] = "last_solve"` and the note "last solve only (rolling/myopic)". Max over windows is filed as AS-14.
3. The cost coefficient is `fom_horizon_factor(n)` × the typed annual cost, as in AS-05. `residual_gap_eur` ≈ 0 before and after a save and load, with nonzero S_x and Q.
4. Writing the result columns leaves both fingerprint modes unchanged. This is tested after `test_extras_passthrough`, and no Link-network hash is pinned.
5. Every solve with AS-05's rows goes through `run_simulation`, the only caller of `_wrap_with_pcs_statcom`, and so through the post-restore persist.
6. A re-solve after untagging clears the sizes (tested). A failed re-solve also clears them, because `clear_solution` runs at solve start.
7. `test_cost_breakdown*` and the objective-decomposition tests were run at the trial merge, and a release-note fragment was added.
