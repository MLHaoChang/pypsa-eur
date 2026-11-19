<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-12 Channel contract: a breaker / trip status channel

Status: ready-for-agent
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

## Comments

**2026-11-17, GK A2 plan GO** (three PRs: A contract, B PSS/E CSV export, C EMT pack 06a/06b). Picks: `connected` on **every** case kind, not fault cases only (a trip in a frequency, ROCOF, phase-jump or step case is a ride-through failure too); PSS/E records it by status polls. A v1 pack stays accepted, but any verdict resting on "stayed connected" reads unknown, "no connected channel (contract v1)", never pass; tested on a v1 pack. Conditions:

- T1 No pack declares a channel it does not write. PR-A adds v2 but keeps the writers' default at "1" (`build_battery` and `battery_json` take the contract version); PR-B switches the PSS/E pack and PR-C the EMT pack to v2, each when its script or schedule writes `connected`. Per pack, a test that the manifest's channels equal what its script or schedule records.
- T2 PR-A lists every consumer of `CONTRACT_VERSION`, `cases_from_json` and `validate_battery` with what changes for each. A v1 pack written before PR-A loads byte-equal. Hand-mutate the three refusals (v2 without `connected`, v1 naming it, unknown version) and name each killing test.
- T3 PR-B: event times stay exact breakpoints; the generated tpause sequence holds every fault apply and clear time, not only the sample grid. Tested from the script's ast with a fault time off the sample grid.
- T4 PR-B: poll every plant machine; `connected` = 1 only if the PoC branch and ALL plant machines read 1 (a partial trip is not "stayed connected"). Tested with two machines, one tripping.
- T5 PR-B: latch (a blank never sets it, a later 0 still does); a failed poll (ierr ≠ 0) writes a blank, never 1. Hand-mutate latch removed, AND→OR, blank→1 and blank→0.
- T6 PR-C: the skmin zero sequence "assumed: from max" is carried per case row in the manifest, keyed so the read-back can apply AS11B-RVC-1; an unbalanced-fault verdict at skmin never passes on an assumed z0 alone; said in the pack README. Hand-mutate a max/min pair swap.
- T7 Oracles: the frozen real v1 output, a hand-literal battery.json per version, a hand-literal Z0 per corner; the scalar-pair battery bytes pinned identical.

Merge order: PR-A any time; PR-B after #1608 and #1609; PR-C after PR-A. Item 4 (read-back accepts v2, maps `connected`) waits for GC-07 PR3 (`judge_pack`), in its own PR; its owner is the coordinator's call.
