<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-12 Channel contract: a breaker / trip status channel

Model tier: opus
Size: L (three PRs)
Status: ready-for-agent; audit 2026-11-19: PR-A merged (#1647); PR-C open in #1650, PR-D in #1708; left: PR-B PSS/E CSV `connected` export
Type: task
Parent: `.scratch/grid-compliance/spec.md` (follow-up of GC-07, GK A2 condition K3)
Gated: yes (GK A, gridspine)
Blocked by: #1608, #1609
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

**2026-11-17 triage (SCALE-1; GK A2 4 priority view).** One package with 12, 06a and 06b (one builder). Plan GO must name slices, one PR each (PR-SIZE-1): (a) contract channel + version bump + readback, (b) PSS/E CSV export, (c) EMT pack side (06a connected channel + 06b per-corner Z0). Blocked by #1608 and #1609 (they change psse_pack / psspy_scripts). The only item that blocks a verdict: no fault-ride-through pass without `connected`. Gated GK A.

**2026-11-17, GK A2 plan GO** (three PRs: A contract, B PSS/E CSV export, C EMT pack 06a/06b). Picks: `connected` on **every** case kind, not fault cases only (a trip in a frequency, ROCOF, phase-jump or step case is a ride-through failure too); PSS/E records it by status polls. A v1 pack stays accepted, but any verdict resting on "stayed connected" reads unknown, "no connected channel (contract v1)", never pass; tested on a v1 pack. Conditions:

- T1 No pack declares a channel it does not write. PR-A adds v2 but keeps the writers' default at "1" (`build_battery` and `battery_json` take the contract version); PR-B switches the PSS/E pack and PR-C the EMT pack to v2, each when its script or schedule writes `connected`. Per pack, a test that the manifest's channels equal what its script or schedule records.
- T2 PR-A lists every consumer of `CONTRACT_VERSION`, `cases_from_json` and `validate_battery` with what changes for each. A v1 pack written before PR-A loads byte-equal. Hand-mutate the three refusals (v2 without `connected`, v1 naming it, unknown version) and name each killing test.
- T3 PR-B: event times stay exact breakpoints; the generated tpause sequence holds every fault apply and clear time, not only the sample grid. Tested from the script's ast with a fault time off the sample grid.
- T4 PR-B: poll every plant machine; `connected` = 1 only if the PoC branch and ALL plant machines read 1 (a partial trip is not "stayed connected"). Tested with two machines, one tripping.
- T5 PR-B: latch (a blank never sets it, a later 0 still does); a failed poll (ierr ≠ 0) writes a blank, never 1. Hand-mutate latch removed, AND→OR, blank→1 and blank→0.
- T6 PR-C: the skmin zero sequence "assumed: from max" is carried per case row in the manifest, keyed so the read-back can apply AS11B-RVC-1; an unbalanced-fault verdict at skmin never passes on an assumed z0 alone; said in the pack README. Hand-mutate a max/min pair swap.
- T7 Oracles: the frozen real v1 output, a hand-literal battery.json per version, a hand-literal Z0 per corner; the scalar-pair battery bytes pinned identical.

Merge order: PR-A any time; PR-B after #1608 and #1609; PR-C after PR-A. Item 4 (read-back accepts v2, maps `connected`) waits for GC-07 PR3 (`judge_pack`), in its own PR; its owner is the coordinator's call.

**2026-11-17, GK A2 plan GO for PR-D** (read-back v2; stacks on #1659 and #1650):
- Q1: every judged kind gets a `stays_connected` obligation row; a trip from t_event to the end is a fail at the trip time. LVRT/HVRT keep J1's carve-out.
- Q2: PR-B writes a blank `connected` cell with the reason in `status`; PR-D reads it per case, never refusing other channels or cases.
- D-1: first 0 is the trip even if a later sample reads 1 (any tool); a 0 anywhere → fail at that time (subject to J1), even with blanks; pass only if every sample from t_event to the end is 1 and none is blank; blanks without a 0 → unknown "status poll failed at t=…". Each tested.
- D-2: a v1 pack reads unknown "no connected channel (contract v1)" on every kind's stays_connected row, never pass; the v1 fixture is a real master EMT pack written before #1650; the v2 acceptance test uses a real write_emt_pack output.
- D-3: unit not "status", or a value outside {0, 1, blank} → unknown naming the sample; the judge's raise is caught per case.
- D-4: KNOWN_CONTRACT_VERSIONS = test_battery.CONTRACT_VERSIONS; an unknown version is still refused for the whole pack.
- D-5: hand-written result CSVs with literal statuses and times, plus an f_step trip → fail, blank-then-0 → fail, 0-then-1 → fail at the first 0. Hand mutants: version gate, unit gate, v1 reason, 0/1 guard, "last sample decides", "blank → 1".
- D-6: the body says the trip verdict reaches the user through the judgment.json rows GC-22 serves; a release-note fragment.
- GK A2 (4), 2026-11-17, PR-B CSV dialect (option A: one reader dialect; the writer conforms). Master's PSS/E results are unreadable by channels.read_csv today (`t_s` header, bare names, a text `status` column, blank i_poc_pu at V = 0).
- T8 PR-B: psspy_scripts writes `time [s]` and `<name> [<unit>]` with names and units from test_battery.CHANNELS (never hard-coded); `connected [status]` only on v2.
- T9 PR-B: text diagnostics go to a sidecar `<case_id>.status.txt`, not a skipped column; read_csv stays strict on non-numeric columns. The pack lists the sidecar as allowed so the extra-file check accepts it; tested that it is read as a reason, never as data.
- T10 PR-B: a blank i_poc_pu cell (V = 0) becomes a per-sample unknown in channels.py (the blank_status mechanism), never 0 or a substitute (ruling 2); a check needing I at that sample reads unknown "no current at V = 0 (t=…)". channels.py is GC-07's area: tell session_01G7qCun9zjyvyqzyBHA7hda before pushing and say so in the body.
- T11 PR-B: contract test: CSV text from psspy_scripts' converter on a hand-literal table (a V = 0 row, a failed poll, a trip) is read by channels.read_csv and checked against literal values; the old header is refused, the new one read.
- T12 PR-B: the body states master's PSS/E results are unreadable today (#1607), with a one-line release fragment. Hand mutants: bare-name header, status column restored, blank I read as 0; name the killing test for each.
- PR-D: a literal -1 (the not-read marker) in a `connected` status column is refused per case, tested in both the CSV and PSCAD readers.

- 2026-11-23 status sweep: landed in #1647 (edd9e7bb); #1708 (4643ab46): v2 contract, EMT pack connected channel, read-back v2. Remains: PR-B, the PSS/E per-case CSV connected export.
