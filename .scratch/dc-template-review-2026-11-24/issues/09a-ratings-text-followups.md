<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09a Ratings assumptions in plain text: split the design-cases sentence, write the heat-constant unit

Status: ready-for-agent
Blocked by: none
Source: UX 29's OK on #1983 (issue 09), two non-blocking follow-ups.
Severity: minor
Model: sonnet. Review: GK A + UX.

## Problem

The equipment schedule's Assumptions list shows two engine texts from `gridspine/static/campus_ratings.py` as written:

- The cables' "Design cases" value joins two clauses with "; " ("the study's selected hours, intact and every N-1 transformer outage; the study has no cable-outage case"). The rule is short sentences, cause first.
- The conductor heat constant's unit reads "A s^0.5/mm2" (shown as "A s^0.5/mm²"). Write it "A·√s/mm²".

## Fix

Change the two texts where the engine writes them (`assumptions_table` in `campus_ratings.py`). Update `tests/gridspine/test_campus_ratings.py` and the ratings content pin in `tests/gridspine/test_campus_run_pin.py`, then retake one Hand-off › Equipment schedule shot with the Assumptions list in frame.

## Comments
