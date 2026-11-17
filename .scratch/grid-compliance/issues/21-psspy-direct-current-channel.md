<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-21: read the PoC current from a direct branch-current channel

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/issues/04-psse-pack.md`
Gated: yes (GK A2, gridspine)

## Why

`gridspine/handoff/psspy_scripts.py` derives `i_poc_pu` from the recorded PoC branch
flow and voltage as `|I| = sqrt(P² + Q²) / (V · S_rated)`. That is undefined at V = 0,
so in a bolted fault at the PoC (the default bolted LVRT cases) every faulted sample
is written with `i_poc_pu` empty and `status` = "not evaluated: V = 0 at PoC", and the
read-back reads the current there as unknown. The current during the fault is exactly
the LVRT evidence those cases exist for.

Found in GC-04 PR4 review (GK A2 ruling on PR #1607); out of scope for PR4.

## What to build

Record the PoC current directly with a PSS/E branch-current channel on `plant_branch`
(measured at the PoC end, like P and Q), convert it to pu on the plant MBASE in
`CONVERSIONS`, and drop the P, Q, V derivation and its V = 0 gap. The channel call
and its units join `CHANNEL_CALLS` and stay marked unverified until the first PSS/E
run.

## Done when

- `i_poc_pu` comes from the direct channel and is evaluated at V = 0; a bolted PoC
  case's CSV has no "not evaluated" status for the current.
- Tests pin the conversion on hand values (including a V = 0 sample) and a mutant on
  the base conversion is killed.
- The channel call is listed as pending verification, like the other psspy calls.
