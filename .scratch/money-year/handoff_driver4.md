# Money-year 01 phase 2: hand-off from driver 4 (session_014F1HxCjib5ueVkmdvFtkaJ)

## State
- **PR #747**, branch `claude/money-year-01-repin-sq` (the WIP branch `claude/money-year-01-repin` squashed onto master 1d80850 plus small commits; the repo hook blocks amend/force-push, so a new branch). The old WIP branch is superseded, do not push to it.
- PR body: conditions (1)–(6), riders (1)–(5), R1–R4, C1–C5 with where met; 16 load_defaults callers; moved constants; rider-3 tests; table summary; skipped/risk.
- CI started on head 9aa828d (2026-10-27 00:35Z). The local full suite in batches was too slow (3+ h); CI is the full-suite evidence.
- GK B review requested from session_01DwpBmSU8nT5kBJP18rg9Vi at ~00:36Z (no verdict yet).
- Owner approval of the table (MONEY-YR-01-OWNER): not yet confirmed by the coordinator. Never report premerge ok before it.

## Done this session
- All 46 test ids that failed after the pin move pass. TOU_DC is stated in 2025 at its source in test_study_tornado_routes.py (report-routes wrapper and import swaps reverted). test_study_m0 / test_study_compile_commercial pin the converted literal / year 2025 again.
- test_energy_hub_templates_pack.py:69 asserted the old 2026-10-22 pin (missed by the 47-id list): now 2026-10-26.
- New C2 route test: test_study_runner::test_a_ledger_naming_an_unshipped_pack_is_refused_never_read_on_the_pin.
- R1–R4 verbatim added to gkb_conditions_and_riders.md.
- review-gk.md sidecar byte-identity check: identical for every TEMPLATE_META id (price_series by diff -r).
- mp-code-review run: no hard standards findings; spec findings answered in the PR body.

## Next
1. Subscribe to #747; drive CI (steward). Expect possible further stale-pin test failures like the templates_pack one: fix tests per `.scratch/money-year/fixture_fix_brief.md` rules, list any pack-argument change in the PR body (rider 3).
2. GK B verdict; fix NOT OK items.
3. #733 (eh_templates.py wording): merge master if it lands first.
4. premerge.sh 747 → `premerge ok <sha>` to the coordinator once the owner approved the table.
