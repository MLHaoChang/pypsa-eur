# IC G3 gate: findings

**Date:** 2026-10-12/13. **Gate head:** 669e407df (suite), 44bf43c4e (the round-2 nits: touched tests, QA, vitest, tsc) (`claude/energy-tool-features-research-fdixs0`, master at bf0b6b5f6
merged in). **Plan:** `docs/superpowers/plans/2026-10-12-ic-g3-campus-route-and-followups.md` (plan PASS round 2,
code PASS round 2, §6).

## What landed

- **R1, the route.** `POST /results/investment_case` and the chat PPA solve (`solve_ppa_price`) read the active
  project's campus study once per request through the seam `_ic_campus_source(finance=…)`
  (`routers/results.py`). The project is the session's `project_uuid`; the DB row never leaves the request. In this
  order:
  1. no project, no row, or no `campus_electrical/run` directory → no extras, no flag (no directory is created);
  2. not a capacity-expansion project → none;
  3. a run directory without the gridspine engine → none, flagged `campus_study_unavailable`;
  4. the producer raising, a malformed response, or an asset `ExtraOwnerAsset` refuses → refused report
     `campus_assets_invalid`;
  5. a currency (always) or price year (when the case states one) other than the case's, or `meta` missing or
     disagreeing → refused `campus_assets_money_year_mismatch`;
  6. a stale study → no extras, flagged `campus_study_stale`;
  7. otherwise the assets go in as `extra_assets` (an empty list with a `source_hash` is a valid study).

  `campus_study_network_unsaved` is added when a study applies and the in-memory network is not the saved one.
  Refusals are `FinanceRefused` raised in `build()`, so they are refused reports (and the PPA tool's 422
  `investment_case_refused`). Their details are fixed text plus the asset's name; exception text and asset values
  are only logged.
- **R2, staleness.** `assumptions_digest` gains `extra_assets` (always present; `None` without a study; a digest of
  `source_hash`, `stale`, the flags except the unsaved one, and the assets without `meta`; on GET a source that
  cannot be read digests as an error, never a 500). The POST's worker and the GET compute the same key for an
  unchanged state.
- **F1.** `ExtraOwnerAsset` validates as before (bools and strings refused), then stores `quantity` and the part
  numbers as float, so an int and a float of the same value hash alike; `derived_from_capital_cost` must be a `bool`
  or `numpy.bool_` and is stored as `bool`; a part name must be a non-empty `str`. Signatures unchanged; the
  no-extras hash pin unchanged.
- **F2.** `owner_assets_digest(n, commercial)` (`services/results/finance_case.py`): the raw investment columns
  (`overnight_cost`, `lifetime`, `capital_cost`, `discount_rate`, `build_year`, the part columns, `max_hours`,
  `carrier`, the static capacity; `_opt` only for Transformer) of every `value_flows.asset_owners` entry, read
  under the network lock, never raising. A changed `build_year`, part cost, lifetime, `max_hours` or carrier of an
  owner asset marks the report stale naming `owner_assets`; a non-owner asset does not.
- **Frontend.** The stale marker names the parts in words ("the campus study", "the owner assets' investment
  columns").

## Closed

- G1/G2 findings limit 2 (the route refuses a money-year mismatch), limit 7 (the route exists and the staleness key
  covers it) and limit 9 (the follow-ups).
- S0b findings limit 4 (see R-5 below: every stored report reads stale once) and limit 6 (the owner assets'
  investment columns are in the key).

## Evidence

- Probes by the code reviewer (rounds 1 and 2, the real producer on a saved hub project): POST then GET reads
  current; one byte added to the study's `campus_investment.csv` → stale naming only `extra_assets`, reverted →
  current; an owner asset's `build_year` → only `owner_assets`; a non-owner asset's → current. A
  `quantity="SECRET_VALUE"` asset through POST, the stored report, GET and the PPA tool: the value appears nowhere.
- Touched tests: `test_investment_case_campus_route.py` 32 (new), `test_finance_case_extra_assets.py` 44 → 58,
  `test_investment_case_runner.py` 23 → 24, `test_investment_case_routes.py` 21 → 23.
- **Gate.**
  - Backend on 669e407df: **11,746 passed, 282 skipped, 0 failed** (1 h 46 min). The 26 assistant test files that
    came with master import `jsonschema`, which the local venv lacked (CI installs it through pixi); installed locally,
    no dependency change in this PR.
  - On 44bf43c4e (the round-2 nits: a name cut at 80 characters, the seam's `finance` required): the touched
    backend files plus the facade and tripwire tests **234 passed**; all **31** QA drivers pass
    (`qa_investment_case.py` 259/259); vitest **3,689** tests in 313 files pass; tsc clean.

## Known limits (disclosed, not defects)

1. **R-5. Every stored report reads stale once** after deploy (the key gained two parts) and must be re-run. No
   migration.
2. **Each GET with a stored report re-reads the campus study**, including a sha256 of the whole saved `network.nc`
   (the producer's `_stale`). The page polls every 2 s while a run is in flight. The file is read without the
   netcdf IO lock, so a save during a poll can make one poll read stale. A cache on (path, mtime, size) is the fix
   if it shows up.
3. **`campus_study_network_unsaved` is common**: a solve leaves results unsaved, so a run straight after a solve
   carries it. It means "save the project so the campus study's staleness check sees this network". After that
   save the new `network.nc` may mark the campus study stale, and the report then names `extra_assets`: expected.
4. **Unresolved campus needs** are dropped by the producer without a flag (the campus session's call; told, R-6).
5. A stale study is never used (default B); there is no option to value it anyway.

## Gate assessor

ASSESSOR
