<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-04 — GPU envelope → flicker and RVC inputs

Status: done
Type: task
Blocked by: none
Model: Sonnet
Reviewer: GK A

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

`flicker_units` and `_flicker_row` read the residual step and rate (60/T) from the GPU envelope after smoothing, instead of process units only. Revised scope: the envelope also carries the load's reactive swing tan φ_swing (to come from vendor data; default marked assumed), and the Pst check uses the voltage form cos ψ·ΔP_res + sin ψ·ΔQ_res <= d_lim·S_sc, with ψ the PoC impedance angle and both residuals floored at zero (the form that AS-06 turns into a cut). Also feeds the RVC row. Read IEC TR 61000-3-7 text before setting defaults.

## Acceptance

- [x] Tests first: S = 30 MW, A = 10 MW, tan φ_swing = 0.1, P_sm = 39.9 MW gives P_res = 0.1 MW and Q_res = 0.1 x 40 = 4.0 Mvar before STATCOM; hand-check the voltage form on a 250 MVA, X/R = 4 grid.
- [x] With tan φ_swing = 0 the Q term vanishes and the result equals the P-only form.
- [x] Residuals never go negative when smoothing exceeds S + A.
- [x] `_flicker_row` reports Pst from the envelope and still works for process-unit loads.
- [x] Defaults marked "assumed"; public sources only (SAGC-1).

## PRs

Built on `claude/as-04-flicker-inputs`. Skipped: the RVC row still reads the P-only swing/S_k (switching it to the voltage form moves its 7.5 MW boundary tests; it needs its own decision). X/R is a module default of 4 (assumed) until the project carries one.

## Comments
