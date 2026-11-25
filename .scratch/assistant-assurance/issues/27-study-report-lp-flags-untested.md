<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Study report's LP-path "not available" flags are untested

Status: needs-triage
Type: task

## Problem

`build_decision_report` in `pypsa-gui/backend/services/study/report.py` builds the economics facts `lcos_incl_charging` and `market_revenue_at_duals`. When one of those figures is missing on an LP, it carries a flag: `"asset_economics_row_missing"` or `"not_computed"`. A guard decides when market revenue is shown (`case.market_revenue_at_duals is not None`).

These flags and the guard are on master, and no test asserts them. Mutants of them survived the AA-17 slice 1 mutant run (#1957), against `tests/test_mip_price_readers.py` and `tests/test_study_decision_report.py`. In #1957 they sit at `report.py:1046`, `:1049` and `:1050`. GK B2 ruled they do not block #1957 and asked for this issue.

## What done looks like

A `build_decision_report` unit test that covers both cases and kills those mutants:
- an LP case whose run detail has no battery `asset_economics` row: `lcos_incl_charging` is null with `unavailable == "asset_economics_row_missing"`;
- an LP case whose `market_revenue_at_duals` is None: the fact is null with `unavailable == "not_computed"`.
