<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-25: record the PoC current at the PoC end, once a channel is confirmed

Status: needs-info
Type: task
Parent: `.scratch/grid-compliance/issues/21-psspy-direct-current-channel.md`
Gated: yes (GK A, gridspine)

## Why

GC-21 records `i_poc_pu` from the plant machine's terminal current (ITERM,
`machine_array_channel` code `ITERM_CODE`), because no psspy branch-current
channel could be confirmed without a PSS/E licence (GK A ruling, condition B1).
ITERM is measured at the machine terminal, not at the PoC end of
`plant_branch`. In pu the two differ by the plant branch's shunt and charging
current and by any off-nominal ratio on it.

## What to build

On the first licensed PSS/E run:

- Confirm the ITERM code (`UNVERIFIED["ITERM channel"]`).
- Check whether a branch-current channel at the from (PoC) end exists. If one
  does, switch `i_poc_pu` to it in `gridspine/handoff/psspy_scripts.py`.

## Done when

- `i_poc_pu` is measured at the PoC end of `plant_branch`, or the PSS/E run shows
  no such channel and this file says so.
- `UNVERIFIED` no longer lists the current channel.

## Needs

A PSS/E licence run (needs-info).
