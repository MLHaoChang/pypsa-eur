<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-18 Campus study: carry the DGS export's `not_written` list into the campus manifest

Status: ready-for-agent
Model tier: sonnet
Size: S
Blocked by: #1578, #1584, #1589 merged (they change `gridspine/drivers/campus_study.py`)
Likely files: `gridspine/drivers/campus_study.py`, `tests/gridspine/`
Type: task
Parent: `16-dgs-shunt-p-and-line-g.md`
Gated: **yes** (GK A2, gridspine)

## Problem

GC-16 (#1602) lists a shunt's active power, which no verified DGS attribute can carry, in
`DgsExport.not_written` instead of dropping it silently. Only the writer's return value carries that list
today. The campus study's export manifest (`gridspine/drivers/campus_study.py`) does not record it, so the
loss the file leaves out is not seen from a campus run's outputs.

## Ask

Write `not_written` into the campus study's export manifest, with the same columns
(`NOT_WRITTEN_COLUMNS`), and test that a campus with a lossy shunt lists it there.

## Waiting on

Open PRs #1578, #1584 and #1589 change `gridspine/drivers/campus_study.py`. Start after they merge. The
DGS-import session (IMPORTS-1) has the manifest's layout and names.

## Comments

**2026-11-17 triage (SCALE-1).** Still needed: GC-16 (#1602) lists `not_written` only in the writer's return value. Ready once the three PRs merge; the DGS-import session (IMPORTS-1) has the manifest layout, so check it before the plan.

**2026-11-23 GK A2 plan GO on M1–M4.**
- M1: test expectations are literal (record dict, key list, reason text), never `NOT_WRITTEN_COLUMNS` or the code's constants.
- M2: `not_written` is plain JSON: NaN/None as null, numpy scalars as Python numbers; tested by reading back with `parse_constant` raising on a NaN record.
- M3: the PR body names every reader of the case `manifest.json`; a reader of `not_written` gets a writer→reader contract test (ruling 2), or the body says none reads it.
- M4: if `not_written` is user-visible, show it end to end (E2E-DONE-1); otherwise the body says nothing reads it yet.
