# The investment case takes the campus study's replacements without buying them twice

Status: done (#656, 8b4bae14)
Type: task
Blocked by: none. The IC route that passes the campus extra assets (issue 01) is on master (#148,
`routers/results.py`, `tests/test_investment_case_campus_route.py`); it calls `owner_assets_response(project)`,
the default list described below. Numbered 04 after 01-03.

This edits IC-owned files (`services/finance/*`, `services/results/finance_case.py`, `models/finance.py`). If you
are not the IC session, confirm with the owner before you start. The campus side is done and read only here.

## Background

Campus plan C14 ("replacement at end of life", `docs/superpowers/plans/2026-10-05-gridspine-campus-electrical.md`,
"C14" and "C14 as built"; owner answers of 2026-10-12, option (a) on every question) buys a campus asset again,
like for like, when its life ends inside the campus study's horizon. The horizon is the project's
investment-period weightings: a single-period project spans **one year**, so there only an asset already past
its life is bought again (in the first period, flagged overdue); a multi-period project spans its periods.
`campus_investment.csv` now has one row per purchase (`action == "renew"` for a replacement, with `purchase`,
`build_year`, `service_to`, `replaces`, `lineage`, `commissioned`, `commissioned_source`).

The producer `campus_electrical_service.extra_owner_assets(project, replacements=False)` (and
`owner_assets_response(project, replacements=False)`; the run is the MILP's or the least-cost one as `_owner_source`
picks, `meta.study_method` says which):

- **default (`replacements=False`)**: exactly the list before C14, the first purchases only, same names, same
  `build_year`. `meta` gains `purchase` (1), `lineage`, `replaces` (None), `service_to` and
  `commissioned_source` (None). Nothing changes for the IC.
- **`replacements=True`**: one entry per purchase, renewals included (an existing asset's renewal too, although
  the kept asset itself is no entry). The first purchases keep their names; renewals count on
  (`campus:<library_id>#<k>`, unique). Each has its own `build_year`, the **first service year** of the new
  unit. `meta.replaces` is the name of the entry it replaces (None for a first purchase and for an existing
  asset's first renewal), `meta.lineage` is the same for every purchase of one asset, `meta.purchase` is k.
  `owner_assets_response(project, replacements=True)` passes it on.

The default stays the old list because the IC, as it is on master, would get the opt-in list wrong.
Checked in the code on 2026-10-12:

1. **Build year after COD is refused.** `services/results/finance_case.py::_extra_cod_flags` raises
   `FinanceRefused("extra_asset_staged_build:<name>")` for any extra asset whose `build_year` is after the
   case COD's year, and `cod_mismatch` for one before it. Every renewal is after COD by design, so the whole
   case would be refused. (The refusal is unconditional, not a default that can be switched off.)
2. **Double counting under `part_lifetimes`.** Extra assets are appended to `FinanceCase.assets`
   (`assets + extra_finance`), and `services/finance/replacements.py::_generated` re-buys every asset with a
   finite part lifetime in `cod_year + floor(k*L + 0.5) - 1` while `k*L < analysis_years`. A campus entry
   whose renewals are listed as entries of their own would be bought again by this rule as well.
   Under the default `fixed` rule the IC generates nothing and flags `part_not_replaced:<asset>:<part>` for
   a part shorter than the axis.
3. **The year convention differs by one.** The IC's generated year is the last service year of the old unit
   (`replacement_year`, financial close a year before COD); a campus `build_year` is the first service year of
   the new one. The IC escalates its own replacements from `base_year` (capex class); the campus applies no
   escalation (library price year, D1a).
4. **The horizons differ.** The campus horizon is the project's periods; the IC's is `analysis_years` from
   COD. For a single-period project (the Data Center template among them) the campus lists no renewal but an
   overdue one, so the IC's own rule is what covers the asset's later life, and the default list is the right
   input. Only with a multi-period project does the campus list renewals the IC could take.

## What to build (the recommendation of plan C14, open question 8 (a))

1. When the case has campus entries from `replacements=True`: the IC does **not** generate `part_lifetimes`
   replacements for an extra asset with `source == "campus_study"` (its replacements are entries of their own);
   `remaining_life_terms` still values the last purchase alive at the IC horizon as for any asset.
2. Accept `build_year > COD` for campus entries with `meta.purchase >= 2` or `meta.action == "renew"` (a
   renewal), placed in the year the entry gives (decide and document whether the IC moves it one year earlier
   to its own convention, point 3, or takes it as given). First purchases keep today's COD rule.
3. Flag a campus asset whose `meta.service_to` falls inside the IC's analysis horizon with no successor entry
   (`campus_life_ends_unreplaced:<name>`), since with point 1 the IC adds nothing for it.
4. Then call `owner_assets_response(project, replacements=True)` from the route. Until 1-3 land, keep the
   default (`replacements=False`), as the route on master does: it is unchanged by C14 (its tests pass on the
   C14 branch).

## Acceptance tests (write them first)

Use `tests/test_campus_electrical_service.py::c14_rows` / `fake_c14` (a hand-made run over 2030/2040/2050 with
renewals in 2043, 2055, 2040 and 2050) and the IC fixtures in `tests/test_finance_case_extra_assets.py`.

1. The default list builds a case exactly as before C14 (pin the case hash on `purchase_rows`).
2. `replacements=True` with `part_lifetimes`: each campus purchase appears once in the capex (sum by hand), no
   generated replacement for a `campus:` asset, no `extra_asset_staged_build` for a renewal.
3. A renewal before COD, or a first purchase after it, is still refused as today.
4. A campus asset whose `service_to` ends inside `analysis_years` with no successor entry is flagged.

## Comments
