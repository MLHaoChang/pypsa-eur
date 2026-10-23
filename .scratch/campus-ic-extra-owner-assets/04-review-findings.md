# Campus-IC 04 (#656): mp-code-review findings, 2026-10-23 (R4)

Run against `04-campus-replacements.md` and `04-plan.md` at f389009b. Fixed so far: Standards 1 (raw flag key `(campus_life_ends_unreplaced)` dropped from the user copy, UI-2).

## Spec: to fix test-first before the GK SHA review
1. `backend/services/finance/replacements.py:186-191` (`check`) refuses `replacement_rule_conflict` for every lineage, meaning any extra with `service_to` set. Plan a scopes it to "an asset with campus renewals". A Data Center asset (single period, no renewals) under `fixed` can no longer take a `replacement_capex` entry, and the refusal text ("the grid study already replaces it") is then false. Narrow it to lineages that have renewals.
2. `replacements.py:272` has the same overreach: `named |= set(campus)` drops `part_not_replaced` for every lineage. Plan a: "a part with campus renewals gets no part_not_replaced".
3. Renewals can be silently dropped. `lineages()` keys on `service_to is not None`. `ExtraOwnerAsset.__post_init__` (case.py ~487-505) accepts non-empty `renewals` with `service_to=None`, and the route copies `service_to` unchecked (`routers/results.py:1887`). Refuse that combination (`extra_asset_invalid:<name>:service_to`), or key lineages on renewals.
4. The flag words in `frontend/.../financeModel.ts:697-700` differ from plan f. There is no year, because the flag `campus_life_ends_unreplaced:<asset>` carries none. The sentence prints both rule clauses instead of only the active rule's. Either the flag carries `<year>`, and the report gives the active rule, or the body says why not. The UX reviewer sees the words.
5. Acceptance test 1, "pin the case hash on `purchase_rows`", is only partly done: test :412 pins the digest only. Add a `finance_case_hash` pin for a case built from `purchase_rows`.

## Standards: judgement calls; answer each in the PR body or fix
- About 100 lines of lineage folding sit in `routers/results.py:1656-1708,1880-1893` (pypsa-gui/AGENTS.md: routers thin). Candidate move into services/finance.
- Duplicated meta guard at results.py:1660, 1677, 1866. `upfront_per_unit * quantity` restates the basis rule of `ExtraOwnerAsset.asset_finance`.
- `CampusRenewal.service_to` is stored and hashed but read nowhere.
- New terms "lineage" and "renewal" are not in pypsa-gui/CONTEXT.md.
- review-ux.md asks for screenshots of the new `ic-campus-life` lines, in Guided and Expert.
