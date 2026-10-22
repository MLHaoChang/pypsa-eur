Closes campus-IC issue 04 (`.scratch/campus-ic-extra-owner-assets/issues/04-campus-replacements.md`). The plan as approved and the GK's six points are in `04-plan.md`, in the same folder.

Stacked on #647 (02+05). This branch starts from that PR's head, because both change the IC route's campus reader and `_canon`.

## Changes proposed in this Pull Request

### Summary

```text
route  owner_assets_response(project, replacements=True)
       _ic_fold_lineages: first purchase ← its renewals (like for like, else campus_assets_invalid, label only)
       ExtraOwnerAsset(..., renewals=(CampusRenewal(part, build_year, amount, name, service_to), …),
                       service_to, existing_renewal)
       digest + [name, purchase, lineage, replaces, service_to] when stated

engine replacements.schedule, under both rules
         renewal  → Replacement(build_year − lead, source="campus_study")
                    ≥ horizon: dropped, flag campus_renewal_after_horizon:<a>:<year>
                    paid before the asset's COD: refused campus_renewal_before_cod:<a>:<year>
         part_lifetimes: no `_generated` for a lineage; resumes at end = last purchase + L
                    (< horizon), paid floor(end + j·L + 0.5) − lead
         end < horizon → flag campus_life_ends_unreplaced:<a>
       remaining_life_terms: resumed purchase › last renewal on the axis › first purchase
       check: replacement_capex naming a campus asset → replacement_rule_conflict (both rules)
       counts_as_replaced: a campus renewal counts; no part_not_replaced for a lineage
       scale_capex: renewals move with their asset or part
adapter existing asset's first renewal (existing_renewal): P5 stage at COD = build_year, exempt from P5-a
        FOM: kept in the period holding a stage's COD, started in its COD year by the cash flow
tab    flag words under the campus table (financeModel.ts campusLifeNotes)
```

**Every stored investment case report with a campus study reads stale once.** The route now takes the study's replacements and digests the lineage meta. A producer table from before C14 states no lineage: it gives the same list either way, and its digest does not change (pinned).

### Plan points → tests (`tests/test_campus_ic_replacements.py` unless named)

- a: conflict refused under both rules `test_a_replacement_capex_entry_for_a_campus_asset_is_refused`; `counts_as_replaced` `test_a_campus_renewal_is_a_replacement_for_the_lifetime_check`; no `part_not_replaced` (in the f tests).
- b, GK 4: `test_the_terminal_value_takes_the_last_renewal` (lead 1 and lead 2, both rules: start fixed, base moves); per part `test_a_part_of_a_renewed_lineage_without_a_renewal_is_not_established`.
- c: like-for-like refusals `test_a_renewal_unlike_its_lineage_is_refused_with_its_label_only` (quantity, kind, basis, part names, replaces no entry, other lineage, two first purchases); after the horizon `test_a_renewal_at_or_after_the_horizon_is_dropped_and_flagged`; before COD `test_a_renewal_paid_before_the_asset_cod_is_refused` (issue test 3).
- d: `test_each_renewal_is_bought_once_in_its_build_year_less_the_lead` (lead 1 and 2, both rules), `test_the_stage_and_its_folded_renewals_are_paid_by_one_rule` (lead 1 and 2).
- e: `test_the_lineage_meta_enters_the_digest`, `test_a_table_without_lineage_folds_nothing_and_keeps_its_digest` (issue test 1).
- f: `test_under_fixed_a_lineage_ending_inside_the_horizon_is_flagged_and_not_bought` (issue test 4), `test_under_part_lifetimes_the_ic_rule_resumes_where_the_last_life_ends`; words in vitest (`InvestmentCaseView.test.tsx`).
- GK 1: `test_an_invalid_renewal_is_refused_naming_renewals` (13 cases), `test_the_lineage_fields_validate_themselves`, `test_the_renewal_amount_is_the_whole_purchase_unescalated`.
- GK 2: `test_an_existing_assets_first_renewal_off_a_period_is_a_stage_not_refused` (2043 in 2030/2040/2050; P5-a exempts it, a first purchase off a period is still refused), `test_a_stage_off_a_period_pays_no_fom_before_its_cod`.
- GK 3: `test_under_part_lifetimes_the_ic_rule_resumes_where_the_last_life_ends`, `test_a_service_to_cut_by_the_study_horizon_is_not_the_end_of_life` (see the decision below).
- GK 5: `test_a_case_with_extras_but_no_renewals_keeps_its_hash` (`03e09dc020aa2c04`, the value before this PR).
- GK 6: `test_a_campus_renewal_tax_vintage_starts_in_its_cash_year`.
- Issue test 2: `test_the_c14_study_through_the_case_buys_each_campus_purchase_once` (both rules, sum by hand); route `test_the_route_takes_the_studys_replacements_folded_into_their_lineage` (`test_investment_case_campus_route.py`).

**Decision for the GK (point 3).** The study's `service_to` for a purchase with no successor is the study horizon's end (`campus_invest._lineage`: `min(next renewal, horizon_end)`). It is not an end of life: for a single-period project it is one year after the build. So the IC resumes at the last purchase's build year + L, not at `service_to`. Resuming at `service_to` would buy the Data Center's equipment again a year after COD. Where `service_to` < build_year + L, the pin shows the case bought exactly as an asset without a lineage at lead 1.

**Data note.** For an existing asset's first renewal, the producer sends `purchase` 1 with `action` "renew", not purchase ≥ 2. The route keys on `action == "renew"` and `replaces is None`.

### Evidence

- Backend: `test_campus_ic_replacements.py` 56 passed; the wider suites (counts below).
- Frontend: vitest `src/pages/results/investment/` 183 passed; `tsc --noEmit` clean.
- Ruff: no new findings (5 already on the base).

### Merge Danger

**Door:** two-way

**Blast Radius:** investment-case

Stored campus reports go stale once. A case without a campus lineage is unchanged: no lineage means the old path, and the hash and digest are pinned.

## Checklist

**Required:**
- [x] Changes are tested locally and behave as expected.
- [x] Code and workflow changes are documented.
- [x] A release note is added as a new file under `doc/release-notes.d/` (see its `README.md`); `doc/release_notes.md` is not edited directly.
- [x] The description is human-written and any AI-generated content is marked. (AI-generated: this PR was written by Claude Code.)

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01CpAMEX2FeZ9nroh4C6b1fX
