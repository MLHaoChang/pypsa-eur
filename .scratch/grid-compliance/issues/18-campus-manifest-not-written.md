<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-18 Campus study: carry the DGS export's `not_written` list into the campus manifest

Status: needs-triage
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
