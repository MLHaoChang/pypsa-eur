<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-03c — LC-T19 injection matrix on a past run's kept sections

Status: in-progress
Type: task

Split from LC-03 at its close-out (acceptance map, `_pending` row). LC-T19 asks that
injected extra numbers, wrong units or source, altered paraphrases and unsupported
result claims from the model are withheld or refused, and that every released
engineering number maps to an admitted native fact.

Covered today: a checked answer cannot publish an unsupported or wrong-scope number
(`tests/test_assurance_metrics.py::test_unsupported_or_wrong_scope_cannot_publish_a_number`),
including branch loading (`tests/test_branch_loading_assurance.py`). A checked read of a
`not_kept` stub is not data (`tests/test_retained_not_kept.py::test_a_checked_read_of_a_stub_is_not_data`).

Not covered: the same injections (an extra number, a wrong unit, a current value
paraphrased as the past run's, a claim from a section the run did not keep) against a
checked answer about a **past run** (`checked_run_section`), one per kept family
(loadflow, results_summary, results_capacity, results_dispatch, results_prices,
results_emissions). Ordinary (unchecked) turns are not evidence for LC-T19 (LC-C3).

Acceptance: one parametrised backend test per family with a scripted model that injects
each case; each is withheld or refused with a typed reason; a mutant that admits the
current run's facts for the past run fails.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `tests/test_assurance_metrics.py`, `tests/test_retained_not_kept.py`, `checked_run_section`.
- Blocked by: none.
- Model tier: sonnet.
- Gated: GK B.
- Size: M.
- Add the injection matrix per kept family against a past run; tests only unless it finds a leak.

### 2026-11-08: GK B plan verdict, GO WITH CHANGES (M1–M6)

- M1: no vacuous case. Per family, assert run-new's quoted value differs from run-old's; add a (c) variant quoting run-new's value rounded, still outside run-old's.
- M2: a positive control per family and per case path (same read and draft with the true run-old value passes; (d) reads a stub).
- M3: assert "limited" exactly and that the injected number is not a checked fact in the receipt or answer record; a typed refusal asserts the exact `error_kind`.
- M4: (b) uses a dimensionally wrong unit (MWh for MW, EUR for t); add a scale-equivalent unit (GW for MW/1000) and pin what the checker does.
- M5: mutants: the past run reads the current run's facts; a `not_kept` stub read as evidence. Each is listed with its killing test.
- M6: a leak goes to GK B with the failing test first; small fix in this PR, else a `_pending` row with a ticket. No xfail or skip.
