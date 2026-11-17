<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-12 Channel contract: a breaker / trip status channel

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/spec.md` (follow-up of GC-07, GK A2 condition K3)
Gated: yes (GK A, gridspine)
Owners: GC-04 (test battery and channel contract), GC-06 (PSCAD/EMT pack)
Blocks: any fault-ride-through **pass** judged from pack output

## Why

The GC-04 channel contract (`gridspine/handoff/test_battery.py`, `CHANNELS`, contract version 1)
has no channel saying whether the plant is still connected. The GC-07 judge's obligation row for
LVRT/HVRT is "stays connected" (condition J1), and it never infers that from current or power.
So every fault case read back from a PSS/E or PSCAD pack reads **unknown**, never pass, and
`judgment.json` says why.

## What to build

1. A `connected` channel in the contract: 1 while the plant's PoC breaker is closed and the plant
   has not tripped, 0 otherwise; sampled with the other channels. Bump the contract version.
2. GC-06: record it in the PSCAD case schedule and the generated output channels.
3. GC-04: write it in the PSS/E per-case CSV export.
4. GC-07 pack read-back: accept the new contract version and map `connected` to the judge's
   `connected` role.

## Done when

- A pack built at the new contract version carries `connected` for every fault case.
- Pack read-back of a fault case that stays above the LVRT curve and stays connected passes;
  one that trips while V is on or above the curve fails.
