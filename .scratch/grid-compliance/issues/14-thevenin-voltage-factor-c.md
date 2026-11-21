<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-14: IEC 60909 voltage factor c in the test battery's Thevenin source

Status: needs-triage
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
