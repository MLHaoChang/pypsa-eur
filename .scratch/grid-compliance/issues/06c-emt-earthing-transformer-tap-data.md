<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-06c EMT workbook: earthing-transformer and tap-changer data

Status: ready-for-agent
Model tier: sonnet
Blocked by: none
Type: task
Parent: `06-pscad-emt-pack.md` (GC-06 follow-up, GK A2 on #1596)
Gated: **yes** (GK A, gridspine)
Size: S
Likely files: `gridspine/handoff/emt_workbook.py`, `tests/gridspine/test_emt_workbook.py`

## What

No workbook sheet carries earthing transformers (zero-sequence source impedance, neutral resistor) or transformer tap-changer data (range, step, neutral tap, side). An EMT model needs both. Add them as columns or a sheet, each with its unit in `PARAM_UNITS` and its source tag; a value the campus does not hold goes to `to_be_supplied.csv` with a reason, never 0.

## Comments

**2026-11-17 triage (SCALE-1; GK A2 4 priority view).** Bundle with 06d (shared pack/workbook test area; one builder, `gridspine/handoff/emt_workbook.py`, `emt_pack.py`, tests/gridspine).
