<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-12 — Persist PCS oversize and STATCOM results

Status: needs-triage
Type: task
Blocked by: 05
Reviewer: GK A

Spec: [spec.md](../spec.md). Raised by GK A's review of AS-05 (#974).

## Scope

AS-05 adds the LP variables `PCS-s_x`, `Statcom-q_ss` and `Statcom-q_dyn`, read only from `n.model` by `services.solver.pcs_statcom.solution(n)`. Persist them after the solve (with the network, so the forward check and the AS-06 loop can read them once the model is gone), and add their cost (PCS and STATCOM, typed annual x `fom_horizon_factor(n)`) to `cost_breakdown`, so the objective reconciliation still closes on a tagged network.

## Acceptance

- [ ] S_x per store, Q_ss and Q_dyn survive the solve and a save/load round-trip.
- [ ] `cost_breakdown` carries the PCS and STATCOM cost; the objective reconciles on a tagged network.

## PRs

## Comments
