<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# PSS/E import 01: RAW case to a PyPSA network

Status: in-progress
Type: task
Owner decision: IMPORTS-1, IMPORT-FLOW-1, HOLISTIC-1, RAW-NAMES-1, PR-SIZE-1
Gated: yes (GK A, gridspine)
Blocked by: the shared import contract (`gridspine/ingest/import_result.py`, PR #1600)
Files: `gridspine/ingest/psse_raw_parse.py` (pure reader), `gridspine/ingest/psse_import.py`
(`read_psse`, the only module importing pypsa), `tests/gridspine/test_psse_raw_parse.py`,
`tests/gridspine/test_psse_import.py`, `tests/gridspine/fixtures/psse/` (PowerModels.jl, BSD-3)

## What

Read a PSS/E `.raw` (v33, v35, v36; v34 refused) into an `ImportResult`: buses, loads, fixed
and switched shunts, generators, branches, two- and three-winding transformers (ratio, phase
shift, impedance base conversion), areas, zones and owners. Records without a counterpart go
to `report.unmapped`; values the importer chooses go to `report.assumed`.

Next: 02 `.dyr` dynamic data into a sidecar the GC-04 pack can write back, and `.con`
registered beside it.

## Comments

**GK A (29) plan GO with conditions (2026-11-17).**
- R1 Out-of-range CW/CZ/CM codes are refused (ContractError naming line and field), not
  defaulted; one test each, and the default-to-1 mutant killed.
- R2 Magnetising: exact ShuntImpedance at the winding-1 bus, transformer g = b = 0, sign as in
  the file (b negative = inductive). Hand-computed CM=2 cases at kV1 ≠ NOMV1 and at NOMV1 = 0,
  each factor killed by its own mutant. Three-winding placement cited and pinned by a test.
- R3 Loads with constant-current or constant-admittance parts: one `assumed` row each; the
  pandapower comparison uses a case without such parts; tolerance never widened.
- R4 A branch whose ends differ in kV becomes a 1:1 transformer with an `assumed` row, tested.
- R5 Switched shunts at BINIT and remote regulation: one `assumed` row per element, counted.
- R6 Row accounting in both modules from hand counts, never from the parser under test.
- R7 The off-nominal transformer round trip waits on the GC-04 `trafo_impedance` WINDV2² fix
  at its exact tolerance; until then it is a `_pending` case pinned to today's error, never
  skipped or loosened.
- R8 Cage: pypsa only in `psse_import.py`; the reader and the registry import with pypsa
  blocked in `sys.modules`.
- R9 Targeted mutants per refusal guard and conversion factor; survivors in the PR body.
