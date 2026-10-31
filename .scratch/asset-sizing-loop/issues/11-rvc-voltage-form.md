<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-11 — RVC row on the voltage form

Status: needs-triage
Type: task
Blocked by: AS-04

Spec: [spec.md](../spec.md).

## Scope

The rapid-voltage-change row still reads swing / S_k (P-only). Move it to cos ψ·ΔP_res + sin ψ·ΔQ_res over S_k with the envelope's `tan_phi_swing` and the PoC X/R (`services/adequacy/flicker_inputs.py`, AS-04). This moves the row's 3 % boundary tests, so it needs a decision on the intended before-mitigation value first.

## Acceptance

- [ ] Decision recorded on what the row shows before mitigation.
- [ ] Tests first; boundary tests re-derived by hand.

## PRs

## Comments
