# Campus-IC 04 (PR 2): the plan as approved

The revised plan sent to the gatekeeper (session_01MvWYuyJJCQ8vbnh4n6ygRb) on 2026-10-22 at about 23:15 CEST, verbatim, followed by the GK verdict (23:26 CEST), also verbatim. Where the two differ, the GK points win.

## Revised plan (verbatim)

Revised plan, campus-IC 04 (PR 2), from the campus-IC session (successor of session_01EiSkwH…, rotated per USE-1). Answers your NOT OK points a–f. No 04 code until your verdict.

Shape (unchanged): the route calls owner_assets_response(project, replacements=True). It folds each lineage's renewals into its first purchase: ExtraOwnerAsset gains `renewals: tuple[CampusRenewal(part, build_year, amount, name, service_to)]` (per part) and `service_to` (of the first purchase). `_canon` drops `renewals` when () and `service_to` when None, so a case without renewals keeps its hash (pinned on purchase_rows, issue test 1). Renewals are never stages and never entries of `assets`, so no staged_cod_not_active_period and no second FOM.

a. Conflicts. replacements.check, under both rules: a `replacement_capex` entry naming an asset with campus renewals is refused `replacement_rule_conflict:<asset>` (the existing code, with fixed text "the grid study already replaces it"). schedule() emits each renewal as Replacement(source="campus_study") under both rules. counts_as_replaced: true when a campus_study replacement names the asset. notes(): under `fixed`, a part with campus renewals gets no part_not_replaced (it gets f's flag instead, when it applies).

b. Terminal value. remaining_life_terms branches on campus renewals first, in both rules: the last renewal on the axis gives start = its build_year (the first service year), base = its amount escalated to its cash year (d). Under part_lifetimes, if the IC rule resumes after the last campus purchase (see f), the last IC purchase wins as today. Multi-part: renewals are stored per part; a part of a renewed lineage with no renewal record gets `terminal_part_unknown:<asset>:<part>`.

c. Lineage checks (route, before folding; refusals `campus_assets_invalid`, fixed text plus `_ic_asset_label` only): a renewal whose kind, basis, quantity or part names differ from its lineage's first purchase is refused (no "stored as a total": C14 is like-for-like, so a difference is a producer fault). A renewal whose `replaces` names no entry, or a lineage with two purchase 1s, is refused. In the engine: a renewal whose build_year is at or after the horizon (cod + analysis_years) is dropped from the schedule and flagged `campus_renewal_after_horizon:<asset>:<year>`; one whose cash year falls before the asset's COD year is refused `campus_renewal_before_cod:<asset>:<year>` (issue test 3: a renewal before COD stays refused; a first purchase after COD stays a stage under P5).

d. Cash year. A folded renewal is paid in build_year − stage_capex_lead_years, the same rule as a stage's capex (cashflow.py:292), escalated by `capex` from base_year with no contingency (as an IC replacement). An existing asset's first renewal (replaces=None, so a first-purchase entry and a P5 stage at COD = build_year) is paid in build_year − lead too, so the two agree for any lead. Tests pin lead 1 (cash year = build_year − 1 = the IC's replacement_year convention) and lead 2, for both a folded renewal and an existing asset's first renewal, with hand sums.

e. Digest. `_ic_campus_digest` gains each asset's lineage meta (purchase, lineage, replaces, service_to) when the producer states it. Moving the route to replacements=True makes every stored campus report stale once; said in the PR body and the release note; a pin that the old-shape response keeps today's digest.

f. Unplanned end of life. When a campus lineage's last purchase ends (its service_to, else build_year + L) inside the IC axis with no successor: flag `campus_life_ends_unreplaced:<asset>`. Under `fixed` nothing is bought (as part_not_replaced today). Under part_lifetimes the IC rule resumes from that purchase's start (k·L from it), so a single-period project (Data Center: no campus renewals) is bought as today. The report's flag words name it: "The grid study plans no replacement for <asset> after its life ends in <year>, inside the case's horizon" plus, under part_lifetimes, "; the case buys it again on its own lifetime rule". Wording goes to the UX reviewer with the PR.

Tests (written first, red before code): issue tests 1–4 on c14_rows/fake_c14; a–f each pinned (conflict refusal; counts_as_replaced; terminal from last renewal; per-part/unknown part; lineage mismatch refusals; after-horizon drop+flag; before-COD refusal; lead 1 and lead 2 cash years; digest pins; flag text in vitest).
Files: services/finance/case.py, replacements.py, cashflow.py (no change expected beyond schedule), services/results/finance_case.py (_canon, adapter folding), routers/results.py (route, digest), financeModel.ts (flag words), tests/test_finance_case_extra_assets.py, tests/test_investment_case_campus_route.py, a new tests/test_campus_ic_replacements.py.
Reply with OK / NOT OK on this plan.

## GK verdict on the revised 04 plan (arrived 2026-10-22 23:26 CEST, after rotation)

OK to build, with these points settled in the code and tests. No further plan round is needed.

1. Validate `CampusRenewal` in `ExtraOwnerAsset.__post_init__`, refusing with `extra_asset_invalid:<name>:renewals`:
   - the part names one of `parts`;
   - `build_year` is an int in 1900..2200 and after the first purchase's `build_year`;
   - `amount` is finite and ≥ 0;
   - `build_year`s strictly increase per part.

   State the unit in the docstring: amount = upfront_per_unit × quantity, unescalated, in the case's money (the same basis as the `asset_finance` overnight).
2. An existing asset's first renewal (replaces=None, purchase ≥ 2) is a P5 stage at COD = build_year.
   - Say explicitly whether P5-a's `staged_cod_not_active_period` still exempts it.
   - A 2043 build in 2030/2040/2050 must not be refused and must not get a FOM before 2043. Pin both.
3. f under part_lifetimes: the IC rule resumes from the lineage's END, not from `start + k·L`.
   - When `service_to` is set and is shorter than build_year + L, the first IC repurchase's cash year is `service_to`'s last service year (end − lead in general, matching d).
   - Pin one case where service_to < build_year + L.
   - `remaining_life_terms` then takes that resumed purchase as the last one.
4. b: start = the renewal's build_year; the base is escalated to its cash year (build_year − lead). Pin lead 2 here too: the start doesn't move, the base does.
5. The `_canon` omissions sit on `ExtraOwnerAsset` itself (renewals == (), service_to None), next to the `FinanceCase` ones. Pin that a case WITH extras but no renewals keeps its hash.
6. Tax vintages (`engine._replacement_vintages`) and storage LCOS read `schedule`, so campus_study replacements reach them. Add one test showing that a campus renewal's tax vintage starts in its cash year.

The kinds `campus_renewal_after_horizon` and `campus_renewal_before_cod` are new. Add them to tool-error-kinds and the flag words where the manifest needs them, and grep with a count before calling them new.

Run mp-code-review against `04-campus-replacements.md` before asking for the GK OK on the PR.
