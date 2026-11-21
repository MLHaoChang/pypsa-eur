<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Imported PSS/E and DGS networks fail Run LOPF: an unrated line keeps s_nom = 0, and the DGS external grid comes in with p_nom = inf

Status: needs-triage
Owner decisions: IMPORT-ZERO-RATING-1, IMPORTS-1, RAW-NAMES-1
Source: project-io 04 Slice A end-to-end check (2026-11-21, session_01G7qCuntcK5Xsp2Xx628rDX).
Review: GK A (importers).
Blocks: project-io 04 Slice B's end-to-end "Run LOPF completes".

## Problem

The import routes from project-io 04 create a project from a model file, and the project opens with its buses and lines. But the app's preflight (`POST /api/simulation/preflight`) refuses the network, so Run LOPF ends `validation_failed`, and AC power flow is refused because it needs an LOPF first.

- **PSS/E** (`gridspine/ingest/psse_import.py` ~49 and ~421–443). A branch with RATEA (v33) or RATE1 (v35+) = 0 keeps `s_nom = 0`, and the docstring says so ("A line without a rating keeps s_nom = 0"). In PSS/E, a zero rating means the branch is unrated. Repro: `tests/gridspine/fixtures/psse/case14.raw`. All 17 lines get s_nom = 0, and preflight gives 17 × `line_s_nom_invalid` ("s_nom must be > 0 when s_nom_extendable=False (got 0)"). The 3 transformers are fine; they take SBASE1-2.
- **PowerFactory DGS** (`gridspine/ingest/dgs_import.py`). The external grid (`ElmXnet`) becomes a Slack generator `ext_grid_0` with `p_nom = inf`, and preflight gives `generator_p_nom_invalid`. Repro: pandapower `example_simple()` with `net.ext_grid[["s_sc_max_mva","s_sc_min_mva","rx_max","rx_min"]] = [1000, 800, 0.1, 0.1]`, written with `gridspine.handoff.dgs_writer.write_dgs`. The same file is built in `pypsa-gui/backend/tests/test_model_import.py::_dgs_bytes` (on branch claude/project-io-04a-model-import).

## Expected behaviour

The reader states a finite, runnable value for every rating the source leaves unset or unbounded, and lists each one under `report.assumed` with the reason, in plain words. For example: "Thermal rating: <value> MVA, the case gives no rating (RATEA = 0 means unrated)". The value is either a stated default or one that reads as unlimited for the solver (for example, from the bus voltages and SBASE). The owner of this issue decides which and writes it in the reader's docstring. Nothing is filled in silently, and the app's import service does not patch values (IMPORT-ZERO-RATING-1 option 1).

## Acceptance criteria

- [ ] Reading case14.raw gives lines with s_nom > 0, and each line's rating appears in `assumed` with why.
- [ ] Reading the DGS file above gives a finite external-grid p_nom, stated in `assumed`.
- [ ] Preflight on each imported network has no error-severity issue, and Run LOPF completes in the real app (import via `/api/model-import`, open, Run).
- [ ] The existing PSS/E and DGS oracle tests (`tests/gridspine/test_psse_import.py`, `test_dgs_import.py`) still pass, or their changed expectations are justified.

## Likely files

`gridspine/ingest/psse_import.py`, `gridspine/ingest/dgs_import.py`, and their tests under `tests/gridspine/`.
