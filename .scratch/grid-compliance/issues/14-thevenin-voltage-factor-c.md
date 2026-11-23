<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-14: IEC 60909 voltage factor c in the test battery's Thevenin source

Status: ready-for-agent
Type: task
Parent: `.scratch/grid-compliance/issues/04-psse-pack.md`
Gated: yes (GK A2, gridspine)

## Why

`gridspine/handoff/test_battery.py` `thevenin_impedances_ohm` derives the source
impedance from the entered short-circuit power as `|Z1| = Un² / S_k''`, with no IEC 60909
voltage factor c. The GK engineering rule (`docs/agents/review-gk.md`, grid source
impedance) asks for `Z = c·Un² / S_k''` in every screen. Every consumer inherits the gap:
`fault_impedance_ohm` and the GC-04 psspy scripts (`gridspine/handoff/psspy_scripts.py`),
which build their Thevenin source from it.

Found in GC-04 PR4 review; out of scope for PR4/PR5 by coordinator ruling.

## What to build

Follow GK A2's standing ruling (GC-02 V1): a conservative c bracket. Use c_max on the
case's S_k''min (weak grid, larger Z) and c_min on its S_k''max (strong grid, smaller Z),
and disclose the c used per case in the battery output and the pack manifest.

## Done when

- `thevenin_impedances_ohm` (or a successor) applies c per the bracket above, the value
  is named in each case's output, and the tests pin both ends of the bracket.
- Fault impedances and the psspy scripts use the corrected source; packs written before
  the change are not silently re-labelled.

## Triage (2026-11-20, master 87cf52a7)

Checked by code reading only. Acceptance is the 'Done when' section above. Still true: `gridspine/handoff/test_battery.py` `thevenin_impedances_ohm` uses `vn_kv**2 / sk_mva` with no voltage factor c. Follow GK A2's standing ruling GC-02 V1 (c_max on S_k''min, c_min on S_k''max; disclose c per case). Gated: GK A2. Blocked by: none.

## Comments

- 2026-11-23, GK A2 plan GO (to session_01G7qCujhF7aEd1Qf6ggRhpo), matching GC-02 V1. c_min is taken from the source's own Un, not a "above 1 kV" default. Conditions:
  - T1: hand-written literal oracles at both ends (HV 13.31 Ω = 1.1·110²/1000 and 110²/3000 Ω; LV 0.4 kV with c_min 0.95), never computed with the module's own formula or constants.
  - T2: a pre-c battery.json reads back with c=None and re-serialises byte-equal against the committed fixture; its impedance is unchanged (c = 1); a note says c was not applied.
  - T3: c is validated; bool, NaN and text are refused as format errors naming the field, each tested.
  - T4: c appears per row in battery_frame, thevenin_set and battery.json; C_NOTE states the bracket; the psspy Z1/Z0 move by exactly ×1.1 on Sk-min rows only, shown per row.
  - T5: if c is visible in the app, check it end to end (E2E-DONE-1); otherwise the PR body says where it shows.
- Builder note: a `Thevenin` holds no Un; Un arrives only at `thevenin_impedances_ohm(grid, vn_kv)`. So a source names its c (`c_kind`: `c_max` / `c_min` / `unity`) and `voltage_factor(grid, vn_kv)` resolves the number at that Un. T3 is met by refusing any `c_kind` outside those names.
