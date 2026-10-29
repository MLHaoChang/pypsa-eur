<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-30 — FMEA criticality is per horizon but labelled per year

Status: ready-for-agent

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

Sites (GK B (9), 2026-10-29): class A `copt.py:1160`; the class A/B aggregation
`copt.py:1292`; class C `eh_stages.py:1023` (and `tests/test_adequacy_stress.py`);
the cross-class ranking consumers `copt_endpoint.py:187` and `eh_review.py:273`.
This is adequacy-engine code, so GK A reviews the fix (GK-SPLIT-1). GK B recommends
annualising by × 8760/Σw, matching the per-year occurrence, with the basis stated in
a field. `tests/test_lc06_d11_fmea_health.py` asserts ΔEUE, occurrence and
severity × occurrence = criticality only. The manifest row `LC-D11.fmea_criticality`
stays pending until the fix lands; S11 then adds the annualised assertions.

Options for whoever takes this:
- Annualise: multiply ΔEUE by 8760/Σ snapshot weights in all three classes, then
  update the pinned tests and the worksheet's ranking text.
- Relabel the fields as per horizon (`criticality_eur`), and say the horizon in the payload.

## Comments

GK A (5) plan verdict, 2026-10-29: GO with conditions.

- Classes A and B (incl. per-block and common-mode rows) annualise ΔEUE·VoLL by 1/nyears, nyears = Σ `snapshot_weights(n, "generators")` incl. period `years` / 8760.
- Class C stays per event: a scenario mutates the whole horizon, so the event is the horizon. severity = horizon ΔEUE × VoLL, criticality = freq × severity (already €/yr), `criticality_basis: "per_event"` plus `horizon_years`. When freq × horizon hours > 8760 the row carries a note that the scenario's events overlap the year; the number stays.
- Conditions: (1) class C test at a non-8760 horizon: severity unscaled, the note fires when freq × W > 8760. (2) On a full 8760 h single-period horizon, A and B equal the pre-fix numbers, a test each. (3) A multi-period test with years ≠ 1 for A. (4) Removing /nyears in A, B and common-mode each turns a named test red; the PR states which. (5) Every reader of stored FMEA rows is listed; a row without `criticality_basis` is legacy per-horizon and is marked or recomputed, never shown as annualised €/yr (FmeaTab changes need UX). (6) Severity is re-derived after the by-name merge, tested on a two-block case. (7) If #900 (results-workbook FMEA sheet) merges first, check its `criticality_basis` labels and cover them in tests.
