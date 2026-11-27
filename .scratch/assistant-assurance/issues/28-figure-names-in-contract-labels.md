<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Checked-metric labels take the ruled figure names (investment-case/2, investment-lcoe/2, copt-screening/2)

Status: needs-triage
Type: task
Gated: GK B. Lands after #2044 (it changes `assurance_contracts.v1.json`).
From: guided-results 04 (`.scratch/guided-results/issues/04-figure-names-parity.md`), GK B (72) ruling 2026-11-26.

## Why

Guided-results 04 made `pypsa-gui/backend/services/figure_words.json` the one source of the names of
the project NPV, the expected shortfall and the LCOE. The chat's checked answer still prints the
checked metrics' contract labels, which differ from the ruled names. A label is part of a metric
definition, so under AA-V1-CHANGE-1 a rename needs a new profile version beside the old one, never
an edit inside v1 (`python -m harness.assurance_contracts --write` refuses it).

| Profile / metric | Contract label today | Ruled name (`figure_words.json`) |
|---|---|---|
| `investment-case/1` `project_npv` | Project NPV (post tax, at the WACC) | `project_npv`: Project NPV at the WACC, after tax (unlevered) |
| `investment-lcoe/1` `lcoe_real` | LCOE, real (engine definition) | `lcoe_real`: LCOE (real) |
| `investment-lcoe/1` `lcoe_nominal` | LCOE, nominal (engine definition) | `lcoe_nominal`: LCOE (finance-consistent, nominal) |
| `copt-screening/1` `screening_lole` | Screening LOLE over the modelled horizon (COPT) | `screening_shortfall`: Screening expected shortfall over the modelled horizon (COPT) |

## Scope

- Add `investment-case/2`, `investment-lcoe/2` and `copt-screening/2` whose labels read
  `services.figure_words.name(...)`, beside the v1 profiles; regenerate the snapshot with `--write`.
- `harness/pilot_evaluation.py`'s label map follows.
- Remove each row from `PENDING_CONTRACT_LABELS` in `backend/tests/test_figure_words.py` as it lands.
