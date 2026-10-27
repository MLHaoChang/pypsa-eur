# Brief: fix test fixtures after the money-year 01 repin (branch claude/money-year-01-repin)

Repo /home/user/pypsa-eur, backend at pypsa-gui/backend. Run tests with `cd pypsa-gui/backend && timeout 570 python -m pytest -q -p no:cacheprovider -W ignore <ids> 2>&1 | grep -E '^E |^FAILED|passed|failed' | cut -c1-300 | head -40`.
Python is /root/.venv-pypsa-gui (already on PATH). Keep tool output small: grep -n then sed -n ranges, never whole files.

## What changed on this branch (all intended, owner-approved)
- The guided study's pinned defaults pack moved: `services/study/library.py` PACK_VERSION = "2026-10-26" (was 2026-10-05 or similar, 2020 EUR).
- In 2026-10-26 the technology rows (battery, PV) keep their VALUES but are labelled currency_year 2025 (MONEY-YEAR-1 relabel). The two seed tariffs `de_industrial_illustrative` and `tou_reference_illustrative` have every money figure × factor = 1.2573346011408 (exact Decimal in `services/library/defaults_pack/versions/2026-10-26/manifest.json` → `money_year_conversion.factor`). E.g. demand charge 9000 → 11316.0114102672 EUR/MW/month. The finance defaults' currency_year is 2025. New studies therefore have currency_year 2025.
- `currency_year_mixed` refuses any case mixing a 2020 value with 2025 rows (that refusal is intended and must stay).
- A STORED ledger is priced on the pack it was seeded from: `library.library_for_ledger(ledger)` / `packs.library_for(ledger)`. `library.WP0_PACK_VERSION = "2026-10-05"`; `tests/golden/site_fixture.py::site_library()` = load_defaults(WP0_PACK_VERSION) for the frozen WP0 parity numbers (tests/u2_targets, u2_pre_numbers.json). Legacy LIBRARY_VERSION ledgers read 2026-10-05.
- On master (pre-branch) every one of your test ids passes, so each failure is caused by the pin move.

## Rules (hard)
- Fix TESTS only. Never edit services/, routers/, packs under services/library/defaults_pack/versions/, or any tests/fixtures/u2_* / u2_pre_numbers file (must stay byte-identical). If a failure looks like a real product bug (not a fixture stating 2020 money), STOP on that test and report it with the evidence; do not fix it.
- Never loosen intent: no wider tolerances, no deleted asserts, no skip/xfail, no `in` instead of `==` where `==` was meant. The test must still check what it checked, in the new money year.
- Typical legitimate fixes:
  (a) A custom/user tariff or row fixture that states currency_year 2020 alongside seeded rows → state 2025 (the study's money now). Same for expected texts like "2020 price data" / "2020_eur" that describe the live study → 2025.
  (b) Expected seed values → the 2026-10-26 value; compute from the old literal × the manifest factor (Decimal, or read the pack), never a hand-rounded number. Or read the value from `L.load_defaults()` where the test already reads the library.
  (c) A test whose point is FROZEN WP0 parity (compares to u2_targets / WP0 numbers) → keep the WP0 numbers and give the study the WP0 pack: seed/run it on `site_fixture.site_library()` / `L.load_defaults(L.WP0_PACK_VERSION)`, e.g. by storing a 10-05-seeded ledger (and currency_year 2020) on the study. Every test whose pack argument changes this way MUST be listed in your report (the PR body needs a line each, GK B rider 3).
  (d) A test that pins the pack version string or a library-version literal → the new value read from the constant, not a new literal, when the test is about "the pin", or keep it on the old version if the test is about that old version.
- Do not commit or push. Do not touch files outside your list except new small helpers inside those test files.
- Another agent edits other test files in parallel; never edit files outside your list.

## Report (final message, ≤ 40 lines)
A table: test id | cause (one line) | fix (one line) | pack argument changed? (y/n). Then: any test you did NOT fix and why; any suspected product bug; the final pytest summary line for your ids.
