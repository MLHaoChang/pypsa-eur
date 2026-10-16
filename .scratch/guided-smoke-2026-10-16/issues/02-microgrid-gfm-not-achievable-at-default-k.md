<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Microgrid: GFM recommender is not_achievable at the Guided default k=3

Status: ready-for-human
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

- 2026-10-16 (session_01TP6mmWWALLE8q4y73ypwoq): root cause and fix. k is the campus ranking's hours per criterion: 45 cases at k=1, 137 at k=3. k=1 passed only because it left out 2027 h86 and 2030 h136. There, E3 trips a battery that is charging at full power while the other battery is already at its charge limit, and nothing else can absorb the surplus, so the island reaches the 51.5 Hz ceiling at any droop or H. The hub's I2b seamless rows sized headroom for the islanding step alone, while I6 also judges E2, E3 and E5. A first fix for E3 only moved the dispatch onto an E2 failure (2027 h45). So `claude/guided-control-island-event-rows` adds rows for the whole seamless event set (E2, E3 both ways, E5), credited as I6 credits them. The microgrid at k=3 then gets `recommended` (droop 1.80 %, H unchanged). Objective +1.6 %, pre-tax NPV 43,857,725 → 43,231,055 EUR. Every other template is unchanged. The E1 genset-credit gap is issue 04.

