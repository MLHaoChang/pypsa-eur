<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-08f Two gensets of different x''d on one bus: fault level above the sum

Status: needs-triage
Type: bug
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none
Gated: **yes** (GK A2, gridspine)
Owner decision: GC-SCOPE-1, GC-TEXT-1
Likely files: `gridspine/static/island_sc.py`, `gridspine/static/campus_sc.py`, `tests/gridspine/test_island_sc.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering.

## Why

Found while building GC-08b. Islanded (`machine_net(..., island=True)`), two gensets on the same 20 kV bus
with the same R/X but different x''d give a combined Ik'' above the sum of each alone, which parallel
passive impedances cannot do: with S_r 12.5 / 8 MVA and x''d 0.15 / 0.2 (R/X 0.07), S''k is 90.61 and
44.69 MVA alone but 137.80 MVA together (sum 135.30, +1.8%); 0.15 / 0.25 gives about +4%. With equal x''d
they add exactly. pandapower prints its "faults near generators" warning. The excess raises SC_tot and,
in the SSTI screen, overstates UIF (the safe side), but it also overstates switchgear duty in `campus_sc`.

## What to build

Diagnose (`diagnosing-bugs`): whether `machine_net` builds the gensets wrongly (K_G, rdss, cos phi) or
pandapower's near-generator handling merges same-bus generators; fix or record the reason.

## Done when

- A test pins two gensets of different x''d on one bus against the hand IEC 60909 sum of admittances, or
  documents why IEC 60909 gives otherwise.
