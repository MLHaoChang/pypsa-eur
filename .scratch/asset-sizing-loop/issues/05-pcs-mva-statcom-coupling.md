<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-05 — PCS MVA ↔ STATCOM coupling

Status: done
Type: task
Blocked by: 02
Model: Opus
Reviewer: GK A

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

Add a BESS PCS MVA variable S_x (oversize above rated P) to the LP and use the PQ capability Q_avail = sqrt(S² - P_reserved²) in `size_compensation` / `ibr_q_capability`. Revised scope from the prototype: (1) split the STATCOM into Q_ss (steady, PQ cut) and Q_dyn (dynamic, Pst cut), Q_statcom = Q_ss + Q_dyn, so one Mvar is never counted twice; (2) make the PQ cut per hour and linear in the transformer flow: Q_ss + c·ΣS_x − k·p_tr(t) >= Q_load(t), k = x·p_cap/(pf²·S_n) − tan φ_req, rather than a constant Q_need; (3) cap S_x <= r·P with credit c = sqrt(1 + 2/r) Mvar per MVA (3.3 at r = 0.2); an S_x on a technology with no P is priced as a STATCOM.

## Acceptance

- [x] Tests first: r = 0.2 gives c = sqrt(11) = 3.317.
- [x] A swap of PCS oversize for STATCOM no longer satisfies both the PQ and the Pst cut: Q_ss and Q_dyn are separate variables.
- [x] Per-hour PQ cut: a hand case with two hours of different transformer flow yields different Q needs, and the cut uses the flow variable, not a constant.
- [x] Prototype case (weak grid, flat tariff, intent D) ends with S_x about 7.9 MVA and Q_dyn about 3.4 Mvar, not 28 Mvar of STATCOM.
- [x] Exact PQ circle is used in the forward check, the inner bound in the LP.

## PRs

## Comments

- 2026-10-30, GK A plan GO (session_01G7qCuntDF1DZfES4S8bRwV) with conditions:
  1. Export side bounded: a(t) >= p_tr(t), a(t) >= -p_tr(t); row Q_ss + Σ c_i·S_x,i − (x·p_cap/(pf²·S_n))·a(t) + tan(acos pf_req)·p_tr(t) >= Q_load(t). Test an export hour.
  2. Refuse before the solve a Link whose reachable |p| exceeds pq_p_cap_mw (no clipping).
  3. Refuse, don't skip: a tag on a non-split Store, pq_* tags without a Link, a Link that is not grid→campus; multi-period refused.
  4. Fingerprint carries the cost columns (pcs_capital_cost, statcom_capital_cost) as well as the tags.
  5. Custom columns survive the .nc save/load round-trip (tested).
  6. Reduced hand-checkable case required (P_highC = 119.9, transformer flow fixed); the 168 h prototype week only as a regression with tolerance, under a timeout. A mutation test per row.
  7. Mixed r: each store its own c_i, tested with two stores.
  8. Docstring: PCS Q is credited at rated P (worst case); Q_dyn comes from the STATCOM only (AS-04's Q_res uses it).
  Scope: rows in services/solver/pcs_statcom.py; one wrap line + import in solver_service.py (order envelope_floors then pcs_statcom when AS-03 lands); fingerprint append; gridspine additions with default output pinned.
