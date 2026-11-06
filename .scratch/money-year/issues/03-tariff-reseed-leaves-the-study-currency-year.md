# A tariff change re-seeds an older ledger but leaves the study's currency year behind

Status: needs-triage

Follow-up to money-year issue 02 (MY-02, PR #1166); GK B (26) asked for this ticket on 2026-11-06.

## What happens

`PATCH /{study_id}` with a `tariff` in the intake re-seeds the stored ledger (`routers/studies.py`, the Gate S2 [S9] block) with `study_ledger.reseed_ledger(..., _library_or_500())`.
This moves a ledger seeded on pack 2026-10-05 (2020 EUR) onto the pin (2025 EUR), the same way `PUT .../ledger {"reseed": true}` does.
Issue 02 made the ledger `PUT` re-seed also move `study.currency_year` when it matched the old ledger's money-row year, using `study_ledger.money_years`.
The tariff path does not do this. The study stays in 2020, the ledger carries the BC-S2-4 "study year differs" note, and the case read refuses `currency_year_mixed` until the user sets the year in settings.

## Possible fix

Apply the issue-02 rule in the tariff re-seed too: if the study's year equals the old ledger's only money-row year, move it to the pin's `finance.currency_year`.
If the PATCH body itself sets `currency_year`, that value wins.
Test: a stored 10-05 study with year 2020, given a tariff change by PATCH, runs and its case reads 200 in 2025.
