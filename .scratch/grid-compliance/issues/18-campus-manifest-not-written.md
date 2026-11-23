<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-18 Campus study: carry the DGS export's `not_written` list into the campus manifest

Status: done
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

**2026-11-23 GK A2 plan GO on M1–M4 (verbatim, GK A2 (6)).**

M1. Literal expectations. Test 1 compares the record against a hand-written dict, and the key order against a hand-written list of column names. Never use NOT_WRITTEN_COLUMNS or the code's own constants (#1786 M6a, ruling 10). The SHUNT_P_NOT_WRITTEN reason likewise appears as its literal text.

M2. JSON-safe values. to_dict("records") can carry numpy scalars and NaN. The manifest must hold plain JSON:
- a NaN or None value is written as null, never the bare NaN token;
- numpy ints and floats become Python numbers.

Test it by reading manifest.json back with json.loads(..., parse_constant=<raise>) on a record whose value is NaN.

M3. The reader. Name in the PR body every consumer of the case manifest.json (the GUI, a report, the importer, a pack source). If one reads it, add a writer→reader contract test for not_written (ruling 2). If none reads it, say so.

M4. If not_written is user-visible anywhere today, show it end to end (E2E-DONE-1). If nothing reads it yet, the PR body says so and no end-to-end check is needed.
