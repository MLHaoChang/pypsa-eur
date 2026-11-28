<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 12 Plain names in the checked answers' method notes

Status: needs-triage
Source: expert-catalogue 05 (the pilot scorer's raw-name count, RAW-NAMES-1). Follow-up, not blocking 05.

## What

Issue 05's pilot scorer (`harness/pilot_evaluation.py` `raw_model_names`) counts catalogue model names a reply shows as written. A live (model-written) reply with one fails; the deterministic suite's checked answers are counted, not failed, because their method notes still name model columns. On 2026-11-28 the suite showed these (the `*_calculations.py` modules under `harness/`):

- "Rating (s_nom_opt if extendable, else s_nom)" (`branch_calculations.py`, `ac_branch_calculations.py`, the pilot label in `pilot_evaluation.py`)
- "the carrier's co2_emissions" (`emissions_calculations.py`)
- "time-varying availability (p_max_pu) … capacity is p_nom_opt if extendable, else p_nom" (`curtailment_calculations.py`)
- "the grid_import connection's p_nom_opt x p_max_pu" and "The import limit is p_nom_opt x p_max_pu … p_nom_opt alone" (`site_import_calculations.py`)
- "(p_nom, e_nom or s_nom, optimised when extendable) … (s_max_pu, p_max_pu)" (`capacity_calculations.py`)
- "upper bound (p_nom_max)", "Generator p_nom in MW", "(p_nom x max_hours for a StorageUnit, e_nom for a Store)", "capital_cost charges"

Rewrite each note in plain names (spec §3.6), then make `report` fail a deterministic reply with a raw name too.

## Acceptance

- `report(...)["metrics"]["deterministic"]["raw_model_names"]["count"] == 0` on the suite, and `report` fails a raw name in either mode.
- Each changed label's oracle and receipt pins move in the same PR (`ORACLE_VERSION` if a public label changes).
