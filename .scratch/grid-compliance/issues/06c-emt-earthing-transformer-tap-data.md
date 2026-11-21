<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-06c EMT workbook: earthing-transformer and tap-changer data

Status: done (#1790)
Landed in #1790.
Model tier: sonnet
Blocked by: none
Type: task
Parent: `06-pscad-emt-pack.md` (GC-06 follow-up, GK A2 on #1596)
Gated: **yes** (GK A2, grid-compliance, GC-PACE-1)
Size: S
Likely files: `gridspine/handoff/emt_workbook.py`, `tests/gridspine/test_emt_workbook.py`

## What

No workbook sheet carries earthing transformers (zero-sequence source impedance, neutral resistor) or transformer tap-changer data (range, step, neutral tap, side). An EMT model needs both. Add them as columns or a sheet, each with its unit in `PARAM_UNITS` and its source tag; a value the campus does not hold goes to `to_be_supplied.csv` with a reason, never 0.

## Comments

**2026-11-17 triage (SCALE-1; GK A2 4 priority view).** Bundle with 06d (shared pack/workbook test area; one builder, `gridspine/handoff/emt_workbook.py`, `emt_pack.py`, tests/gridspine).

GK A2 (4) plan GO, one PR with 06d (builder session_01G7qCuiaRKEqzVpt6GHTVEw). Conditions:

- E1 winding earthing is a mode (solid, resistance, isolated, delta), written per winding beside `r_n_hv_ohm`/`r_n_lv_ohm`; a blank resistor never reads as solid; ingest defaults are tagged `assumed` with the reason from `Campus.assumptions`; each mode and each assumed default tested.
- E2 earthing transformers: no NaN in any cell; the bus kV the ohms are referred to is its own column; an empty table still writes the sheet header, and the RSCAD note says so.
- E3 taps: `tap_pos` is written (default neutral, said so), with a note naming `campus_invest_taps.csv`; a unit without a tap gets empty cells and the reason "no tap changer entered; modelled at nominal ratio".
- E4 expected values are hand literals from a small fixture, not the workbook's own reader.
- E6 hand mutants per guard, including the added `PARAM_UNITS` entries.
- E7 generic engineering text only. E8 release-note fragment.
