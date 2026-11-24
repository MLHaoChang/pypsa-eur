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

- 2026-11-23, GK A2 (6) plan GO (to session_01G7qCujhF7aEd1Qf6ggRhpo), verbatim (recovered by GK A2 (7) from its transcript, event 0216690c):

  > It matches GC-02 V1: c_max 1.1 on S_k''min, c_min on S_k''max.
  > Your question: no, don't default vn_kv to "above 1 kV". Each Thevenin source already holds its own Un, because Z = c·Un²/Sk needs it. Take c_min from that Un (1.0 above 1 kV, 0.95 at or below 1 kV), so build_battery needs no vn_kv argument and psse_pack is untouched. A silent 1.0 on an LV PCC would quietly break the bracket.
  > T1. Hand-written literal oracles at both ends:
  > - HV: 13.31 Ω (1.1·110²/1000) and 110²/3000 Ω;
  > - LV: 0.4 kV with c_min 0.95.
  > Never compute the expectation with the module's own formula or constants (M6a).
  > T2. Legacy. A pre-c battery.json reads back c=None and re-serialises byte-equal, compared against the committed fixture bytes. Its impedance stays as before (c treated as 1 for legacy only), and a note says c was not applied.
  > T3. Validation. c is finite and within 0.95–1.1. A bool, NaN or text value is refused as a format error naming the field (ruling 5). Test each.
  > T4. Disclosure. c appears per row in battery_frame, thevenin_set and battery.json. C_NOTE states the bracket. The psspy golden Z1/Z0 moves by exactly ×1.1 on skmin rows, and only there: show this per row in the test.
  > T5. If the battery's c is user-visible in the app (the EMT pack, a report), check it end to end in the real app (E2E-DONE-1). Otherwise the body says where it shows.

- Builder note: a `Thevenin` holds no Un; Un arrives only at `thevenin_impedances_ohm(grid, vn_kv)`. So a source names its c (`c_kind`: `c_max` / `c_min` / `unity`) and `voltage_factor(grid, vn_kv)` resolves the number at that Un. T3 is met by refusing any `c_kind` outside those names. Under GK A2 (7) review on #1916.
