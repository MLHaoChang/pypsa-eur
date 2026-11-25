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

## Also: an unjudged market revenue read as 0 (from AA-17 2a, #1970)

GK B2 (2026-11-24) ruled this a follow-up here, as an unjudged value read as 0 (GK B4 ruling 4). In `services/study/engine_adapter.py` (the yearly cash-flow rows, `market_revenue_at_duals=... float(ctx["market"].annual_value or 0.0)`, line 789 at #1970), an LP case whose market revenue is not established (`annual_value` None, no price limit) writes 0.0 into every operating year's `market_revenue_at_duals`. It should be None with a flag, as a MIP year is since #1970. The line predates AA-17; #1970 kept it so an LP payload stays byte-equal (S4). The mutant `annual_value or 0.0 → annual_value` survived #1970's run.

Done adds: an LP case with `annual_value` None has `market_revenue_at_duals` None and a reason flag in each operating year, with a test that kills that mutant.
