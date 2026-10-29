<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-30 — FMEA criticality is per horizon but labelled per year

Status: needs-triage

Found by LC-06 (7) while building S11 (LC-D11). Every FMEA failure mode carries
`criticality_eur_per_year` and `occurrence_per_year`. The occurrence is annual
(8760·q/MTTR). The criticality is the horizon's ΔEUE times VoLL, with no
annualising:

- class A, `services/adequacy/copt.py` `attribute_criticality`: `crit_eur = delta_eue * voll`;
- class B, `services/adequacy/sweep.py` `run_class_b_sweep`: `crit = q * delta * voll`;
- class C: frequency × the horizon's ΔEUE × VoLL (`tests/test_adequacy_stress.py`).

On a full-year network the two agree. On a shorter or weighted horizon the
"per year" figure is per horizon, and `severity_eur` = criticality / occurrence
mixes the two. Example: the `test_copt_assurance` classical fleet, 2 snapshots
× 3 h. thermal1 gets ΔEUE 28.8 MWh, criticality 28 800 EUR "per year" at VoLL
1000, and severity 1 643.8 EUR. Annualised, the criticality would be
28.8 × 8760/6 × 1000.

`tests/test_lc06_d11_fmea_health.py` pins today's numbers (ΔEUE·VoLL) and
cites this ticket.

Options for whoever takes this:
- Annualise: multiply ΔEUE by 8760/Σ snapshot weights in all three classes, then
  update the pinned tests and the worksheet's ranking text.
- Relabel the fields as per horizon (`criticality_eur`), and say the horizon in the payload.
