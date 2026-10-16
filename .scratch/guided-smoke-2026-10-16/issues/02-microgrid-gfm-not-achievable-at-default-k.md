<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Microgrid: GFM recommender is not_achievable at the Guided default k=3

Status: needs-triage
Type: bug
Found by: Guided smoke re-run, 2026-10-16, master dc4e4b37

## Error

`eh_microgrid_staged`, campus run with the Guided/Expert default settings
(`k=3, margin=0.2, invest=true, n_minus_1=true`), then "Recommend GFM settings":

```
status: not_achievable   simulations 1500 / 1500   (310 s)
notes: "no setting within the bounds passes every case: capacity is needed (I7)"
       "the GFM/GFL comparison stopped at the budget of 1500 simulations"
recommended (unusable): droop 1.0 %, H 10 s on every BESS
```

Binding cases among the 137 island cases: `2027 h86 seamless E3` steady island
deviation 185 Hz (collapse), `2027 h68 E3` transition RoCoF 2.90 Hz/s vs 1.8,
`2027 h86 E5` 3.29 Hz/s. So "Apply" is not offered.

With the same template and `k=1` (what `tests/test_microgrid_gfm_demo.py` uses,
`_frequency_check` -> `rank_campus(run, k=1)`), it passes as #326 describes:
`recommended`, 1180 / 1500 simulations, droop 2.41 %, H 3.49 s, apply writes the droop and tau_f changes
(per BESS), `hub_resolve_needed: true`, re-solve 13 s.

## Where

- Default `k=3`: `services/campus_electrical_service.py:132` (`DEFAULTS`),
  `pypsa-gui/frontend/src/pages/CampusElectricalPanel.tsx:61` (`DEFAULT_SETTINGS`).
- Status set: `gridspine/static/gfm_tuning.py:317` (`not_achievable`), note at `:333`.
- The test's `k=1` run: `tests/test_microgrid_gfm_demo.py:39` (`_frequency_check`, defined at :32).

## Repro

As issue 01 steps 1-5 with `eh_microgrid_staged`; run the campus with `k=3`
(220 s), recommend (310 s), `GET .../gfm-settings` -> `not_achievable`. Repeat with
`"k": 1` -> `recommended`.

## Comments
