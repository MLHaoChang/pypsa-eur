# Money-year 01 phase 2: hand-off from driver 3 (session_01JNVayPpS24pK9hQzqib7sa)

## State

Branch `claude/money-year-01-repin`, WIP commits on master 1d80850. No PR yet. Squash to one commit before opening it (own branch; force-push is fine before the PR exists). Overlap: #733. Merge master if it lands first.

## Done in this session

- **Blocker fixed** (GK B session_01WQthJT plan A, OK'd with C1–C5): a stored ledger is priced on the pack it was seeded from.
  - `library.library_for_ledger` + `WP0_PACK_VERSION` (2026-10-05). `LIBRARY_VERSION` ledgers read 10-05, which is what master did.
  - `packs.library_for` (PackError `ledger_defaults_unknown`) and `routers/studies.py::_library_for_or_500` (500 `study_library_unreadable`).
  - Call sites: runner start and worker, the `build_site_network` default, findings `load_inputs`, the router case read, and the router `reset_rows` (C1, read after any re-seed).
  - `tests/golden/site_fixture.py` imports `WP0_PACK_VERSION` (still rider 3's pin).
- **Tests**:
  - `test_study_runner.py`: `test_a_study_seeded_on_an_older_pack_runs_on_its_own_tariff` (10-05 and LIBRARY_VERSION; bill == the 10-05 recompute and pin == ×factor, rel 1e-9; red before the fix).
  - `test_a_reset_row_on_an_older_ledger_comes_back_in_its_own_money_year` (C1, plus the re-seed refusal hint).
  - `test_library_defaults_pack_money_year_2025.py::test_a_stored_ledger_reads_the_defaults_it_was_seeded_from`.
  - The legacy runner test now stores its study with currency_year 2020, as a study saved on master would have.
- `compile.currency_year_of`: when only the study's year differs, the message adds "; or set the study's currency year to <year> in its settings" (GK B condition for choosing (c)).
- Follow-up issue `.scratch/money-year/issues/02-reseed-moves-the-study-currency-year.md` (needs-triage). The PR body names it under skipped/risk.
- GK B conditions (1)–(6), riders (1)–(5) and the C1–C5 notes are verbatim in `.scratch/money-year/gkb_conditions_and_riders.md`.
- **Table**: study rows re-measured after the fix. `after_study_rerun.jsonl` matches `after_306f072.jsonl` exactly (209 leaves, rtol 1e-9). Note: the measure harness reads `site_fixture.site_library()` (WP0); the "after" needs `SF.site_library = L.load_defaults` (env MY_LIVE_PIN in the patched copy).
- The table was sent to the coordinator 2026-10-26 ~23:58Z with a 9-point summary and an owner question: (a) approve / (b) hold for a bess_pv_2h explanation. **Waiting on the owner's answer.**

## The 47 test ids

- **Master**: 46 pass. Only `test_packaging_requirements` fails there, environment-only (defusedxml).
- **Branch after the fix**: 45 still fail. Two subagents were fixing test fixtures in disjoint files under the brief at scratchpad `brief.md`: tests only, never loosen intent, and list every test whose pack argument changes for rider 3.
- Their results: SEE BELOW (appended when they report).

## Remaining

1. Review the agents' diffs (`git diff --stat`, spot-check that no tolerance was loosened), then rerun the 44 ids.
2. Run the full backend suite. Shards 06/11/18 one file per run, each under 580 s.
3. Squash to one commit.
4. Run mp-code-review.
5. Open the PR. The body covers:
   - conditions 1–6, riders 1–5, R1–R4 and C1–C5, each with where it's met;
   - the moved constants (TEMPLATE / AMMONIA / eh_ipp PACK_VERSION, REDUNDANCY, study PACK_VERSION);
   - the tests whose pack argument changed (site_fixture, wp0_defaults, plus the agents' list);
   - all 16 `load_defaults()` callers classified (C5): stored → library_for_ledger (runner 395/629, packs 623, findings 1206, studies 1075 reset, 1183 case read); seeding or re-seed → live pin (runner 892 stale check of an unstored ledger, findings 1159, studies 325 /tariffs listing, 362 preview, 471 intake tariff re-seed, 649 intake tariff check, 681 create, 957 the helper, 972 _current_ledger seed, 1071 re-seed);
   - the table;
   - skipped/risk: issue 02.
6. Send the PR number to GK B; drive CI; report `premerge ok <sha>` to the coordinator once the owner has approved the table.
