# Money-year 01 phase 2: hand-off from driver 5 (session_01Vv6okSR3ULeaYUaroSMr1K)

## State (2026-10-27 ~02:45Z)
- **PR #747**, branch `claude/money-year-01-repin-sq`, head on GitHub **703883bca3a5f3478386d7c7f10a3c3271f418bd**.
- **Unpushed to the PR:** `8f60f77` (engine-adapter golden fix) + this note, staged on `claude/money-year-01-repin-sq-pending` (a fast-forward of the PR head). Push it with `git push origin origin/claude/money-year-01-repin-sq-pending:claude/money-year-01-repin-sq` (plain push), then delete the side branch (the proxy refused `--delete` twice; leave it if it still does).
- CI on 703883b: everything is green except **shard 4/4 red** (the 2 tests fixed by 8f60f77). Shards 1/4 and 3/4 were still running at hand-off. Read their failures before pushing, and batch any fix into the same push.
- Coordinator is **session_01G7qCun6YddvZGmjVowZSvY** (rotated; coordinator.md updates in #748).

## OKs
- GK B (session_01DwpBmSU8nT5kBJP18rg9Vi): GK OK @28a1e2a, @93eab4adc97c35bb2162d46d85a0118e1503ff6b, @703883bca3a5f3478386d7c7f10a3c3271f418bd. The next push makes it stale: send GK B the new head for a delta review (8f60f77 is test-only, the same rider-3 WP0 pin pattern).
- Owner approval of the before/after table (MONEY-YR-01-OWNER): pending; the coordinator is asking. The table now includes the QA-journey verdict row (marginal → recommended), added at GK B's request.

## Done this session
- Merged master 10e4a2f (#733) as a merge commit: clean. Overlap tests pass.
- `qa_decision_study` (QA driver) failed 62/67 on the live pin: §4 compares against the frozen WP0 record. Fix ee89aaf + 703883b: `_pin_wp0_pack()` sets `library.PACK_VERSION = WP0_PACK_VERSION` (process-local; run_qa_drivers.py uses one subprocess per driver). GK B accepted it on 3 conditions, all met (PR body has the verdict row and the rider-3 bullet; the docstring line). Green on CI.
- `test_engine_adapter_case_golden.py` (shard 4): `_ledger()` and `_charging_per_mwh_discharged()` read the live pin while the case is built on the WP0 site fixture. Both now read `sf.site_library()`. Locally 32/32; ruff check clean (the file was unformatted on master already; left as is).

## Next
1. Wait for shards 1/3 on 703883b; fix any further stale-pin tests the same way (only where the test's oracle is WP0).
2. One push (above). Add to the PR body's rider-3 list: `test_engine_adapter_case_golden.py` `_ledger()` and `_charging_per_mwh_discharged()` → `sf.site_library()` (WP0 oracle `golden_s5`). Also add `test_a_second_currency_year_is_refused_typed`, which passed on the live pin for the wrong reason (whole ledger 2025).
3. GK B delta review; then wait for CI on the new head.
4. GUI backend green + owner table approval → `scripts/coord/premerge.sh 747` → `premerge ok <sha>` + tail to the coordinator.
