<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-16 — RVC row on the voltage form

Status: ready-for-agent
Type: task
Blocked by: AS-04

Spec: [spec.md](../spec.md).

## Scope

The rapid-voltage-change row still reads swing / S_k (P-only). Move it to cos ψ·ΔP_res + sin ψ·ΔQ_res over S_k with the envelope's `tan_phi_swing` and the PoC X/R (`services/adequacy/flicker_inputs.py`, AS-04). This moves the row's 3 % boundary tests, so it needs a decision on the intended before-mitigation value first.

## Acceptance

- [x] Decision recorded on what the row shows before mitigation (AS11B-RVC-1, below).
- [x] Tests first; boundary tests re-derived by hand.

## PRs

## Comments

- 2026-11-02, GK A plan verdict: GO with conditions, assuming the owner picks option A (voltage form). The owner question on what the row shows before mitigation is still open with the coordinator.
  1. An assumed X/R must not make a pass. While X/R is assumed, the row also computes the X/R-free bound √(ΔP² + ΔQ²)/S_k. When the voltage form passes but that bound does not, the verdict is warn and the note says it passes only at the assumed X/R. project_value stays the voltage form. The after-battery value follows the same rule.
  2. IEC 60909 c factor: the note carries the flicker row's wording ("S_k is the entered S_k'' … d may be understated by up to 1.1"). The infrequent-tier row (a ratio screen) is unchanged.
  3. tan φ comes from the envelope's `max.tan_phi_swing` when it is present. The 0.1 default marks the row assumed. Q_res stays floored at 0.
  4. The note keeps the P-only ΔS/S_k reference. The boundary tests sit exactly at the limit and just above it.
  5. If the owner picks other than A, the plan goes back to GK A.
  6. A release note names the verdict change.

- 2026-11-02, decision AS11B-RVC-1 (owner, cards at ~20:17 and ~20:25 CET): "A + GK A's condition".
  - The row and its after-battery value are judged on the voltage form (cos ψ·ΔP + sin ψ·ΔQ)/S_k, ψ = atan(X/R), using the envelope's tan φ_swing and the PoC X/R. The row is marked assumed while either input is assumed. The note keeps the P-only ΔS/S_k figure as a reference.
  - While X/R is assumed (always, until a project X/R input exists), a pass also needs the X/R-free bound √(ΔP² + ΔQ²)/S_k under the limit. When the voltage form passes and the bound does not, the row warns and shows the bound.
  - At 250 MVA, 3 % and tan φ 0.1, the verdict turns at a 7.46 MW swing. The voltage form reaches 3 % at 22.09 MW.
- 2026-11-02, GK A on how condition 1 is built: option 1 GO. project_value is the figure that decided the verdict, so the assurance check re-judges every row to the same verdict. The row always carries `voltage_form_pct` and `xr_free_bound_pct`. Above the voltage-form boundary the displayed value drops from the bound to the voltage form; that is intended. Option 4 (no bound check) was declined.
