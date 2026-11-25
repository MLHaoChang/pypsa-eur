<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09a Ratings assumptions in plain text: split the design-cases sentence, write the heat-constant unit

Status: done
Blocked by: none
Source: issue 09: UX 29's OK on #1983 and GK A (37)'s OK on #1981, non-blocking follow-ups.
Severity: minor
Model: sonnet. Review: GK A + UX.

## Problem

The equipment schedule's Assumptions list shows two engine texts from `gridspine/static/campus_ratings.py` as written:

- The cables' "Design cases" value joins two clauses with "; " ("the study's selected hours, intact and every N-1 transformer outage; the study has no cable-outage case"). The rule is short sentences, cause first.
- The conductor heat constant's unit reads "A s^0.5/mm2" (shown as "A s^0.5/mm²"). Write it "A·√s/mm²".

Two nits from GK A (37)'s OK on #1981, in `pypsa-gui/backend/services/equipment_schedule.py`:

- The bus-voltage fallback reads `buses.get(str(r.bus))`, which misses a YAML integer bus key. Normalise the keys with `str()`, as `gridspine/ingest/campus.py:623` does.
- A PCC bus's voltage lives in `pcc.vn_kv`, not in `buses`.

## Fix

Change the two texts where the engine writes them (`assumptions_table` in `campus_ratings.py`). Update `tests/gridspine/test_campus_ratings.py` and the ratings content pin in `tests/gridspine/test_campus_run_pin.py`, then retake one Hand-off › Equipment schedule shot with the Assumptions list in frame.

## Comments

Built on branch `claude/dc-09a-ratings-text`. Shot: `shots/09a-ratings-text/handoff-table-expert-light.png` (Assumptions list in frame).
