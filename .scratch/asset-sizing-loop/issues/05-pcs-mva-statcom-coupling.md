<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-05 — PCS MVA ↔ STATCOM coupling

Status: ready-for-agent
Type: task
Blocked by: 02
Model: Opus
Reviewer: GK A

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

Add a BESS PCS MVA variable S_x (oversize above rated P) to the LP and use the PQ capability Q_avail = sqrt(S² - P_reserved²) in `size_compensation` / `ibr_q_capability`. Revised scope from the prototype: (1) split the STATCOM into Q_ss (steady, PQ cut) and Q_dyn (dynamic, Pst cut), Q_statcom = Q_ss + Q_dyn, so one Mvar is never counted twice; (2) make the PQ cut per hour and linear in the transformer flow: Q_ss + c·ΣS_x − k·p_tr(t) >= Q_load(t), k = x·p_cap/(pf²·S_n) − tan φ_req, rather than a constant Q_need; (3) cap S_x <= r·P with credit c = sqrt(1 + 2/r) Mvar per MVA (3.3 at r = 0.2); an S_x on a technology with no P is priced as a STATCOM.

## Acceptance

- [ ] Tests first: r = 0.2 gives c = sqrt(11) = 3.317.
- [ ] A swap of PCS oversize for STATCOM no longer satisfies both the PQ and the Pst cut: Q_ss and Q_dyn are separate variables.
- [ ] Per-hour PQ cut: a hand case with two hours of different transformer flow yields different Q needs, and the cut uses the flow variable, not a constant.
- [ ] Prototype case (weak grid, flat tariff, intent D) ends with S_x about 7.9 MVA and Q_dyn about 3.4 Mvar, not 28 Mvar of STATCOM.
- [ ] Exact PQ circle is used in the forward check, the inner bound in the LP.

## PRs

## Comments
