<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Key any reuse of a stored checked value on the definition hash

Status: needs-triage
Type: task
Found by: AA-10 S7 (`10-metric-calculation-and-validity.md`); GK B H4.

## Problem

AA-10 S7 records a profile's definition hash (`calculations.definition_hash`:
each metric's label, unit and formula_id, and the renderer version) in new
evidence and answer records, and the answer route reports `definition_changed`.

S7 found no path that serves a stored computed value for a new question. Every
reuse keeps the raw source and recalculates under the current code:

- `services/assurance_records.py` `evidence()`: a retained (`retain`) scoped
  record is re-run through `calculations.scoped_calculate` on its stored raw and
  refused unless facts and slots are equal, and each fact carries its current
  unit and formula_id (`calculations.scoped_calculate`, the `definition(metric)`
  fact). The same function recalculates calculated, run and run-section
  records from their stored raw before returning them.
- `services/assurance_records.py` `capture_scoped()`: a follow-up naming an
  `evidence_id` returns that record only after `evidence()` above.
- `harness/assurance_conversation.py` `remember()`: the retained snapshot is
  recalculated from the evidence's raw before it is saved; comparisons
  (`calculations._comparison`) read the baseline through `evidence()` and use
  its raw.

So a formula_id or unit change already refuses old evidence, and a label-only or
renderer change rewords from current definitions. Nothing is cached today, so
nothing is keyed.

## Wanted

If a cache of computed values (validation results, checked facts or rendered
answers) is ever added, key it on (scope, source fingerprint, definition hash),
and test that a definition change forces a fresh calculation. Until then, a
test that pins "no stored computed value is served without recalculation" would
stop such a cache from landing unkeyed.

## Comments
