# A re-seed onto a newer pack leaves the study's currency year behind

Status: needs-triage
Model tier: opus
Size: S

Follow-up to money-year issue 01 (LG-02 repin, branch `claude/money-year-01-repin`); GK B chose this split on 2026-10-26.

## What happens

A guided study stored with a ledger seeded on pack 2026-10-05 has `currency_year` 2020.
`PUT .../ledger {"reseed": true}` moves its rows onto the pin, 2026-10-26, in 2025 EUR, but `study.currency_year` stays 2020.
The ledger then carries the BC-S2-4 "study year differs" note, and the case read refuses `currency_year_mixed`.
Issue 01 made that refusal name the fix ("set the study's currency year to 2025 in its settings"), and `PATCH` settings `currency_year` already works.
So the user isn't stuck, but it takes an extra step they shouldn't need.

## Proposed fix (option (b))

The re-seed also sets `study.currency_year` to the pin's currency year when the study's old year equalled the old ledger's money-row year.
A study year the user set on purpose to something else is left alone.
The fix is in the `PUT` ledger handler in `routers/studies.py`. Test: a stored 10-05 study, re-seeded, runs and its case reads 200 in 2025.

## Where the current behaviour is tested

`tests/test_study_runner.py::test_a_reset_row_on_an_older_ledger_comes_back_in_its_own_money_year` (its last block asserts the refusal and its hint).
