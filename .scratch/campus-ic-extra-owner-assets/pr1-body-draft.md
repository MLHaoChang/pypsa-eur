Closes campus-IC issues 02 and 05 (`.scratch/campus-ic-extra-owner-assets/issues/02-*.md`, `05-*.md`).

## Changes proposed in this Pull Request

### Summary

```diff
 Commercial & finance › Investment case
   <Report>
     <Headlines> <Counterfactual>
+    <CampusEquipmentBlock>          # only with campus extra assets or unmet needs
+      method line                   # "From the grid study's joint optimisation | least cost result."
+      table: item [assumed] · kind · quantity · overnight cost · build year · COD   (the report's own money)
+      "Generic default (illustrative): N of M campus costs are assumed …"
+      unmet needs: "The grid study found no equipment for N needs, so this design does not meet the grid-code profile: …"
+        See Electrical design ›  (Expert)  |  Grid study ›  (Guided)
     <Cashflows> …
   run row
+    campus_study_stale               → "… Run the grid study again in Grid study ›"
+    campus_assets_money_year_mismatch → "Fix the money in Grid connection › Commercial & finance ›"
```

Backend (`routers/results.py`, IC route):

```text
_ic_campus_from_response(resp)
  unresolved   → list of {need, reason}, else refused campus_assets_invalid (fixed text)
  _ic_cost_tags → (name, provenance cut to 40, illustrative bool); wrong type / meta not a record → refused, label only
  summary       → CampusSummary(study_method, cost tags, unmet needs deduped and cut)  — None when the study states none of them
  flags         → campus_study_stale | campus_needs_unresolved (only for an applying study)
  digest        → old formula + unresolved / study_method / cost_tags, each only when stated
  stale         → no extras, no summary
```

- `FinanceCase.campus` (left out of `_canon` when None: an older producer's case keeps its hash, pinned on the master hash `64e7a34307ea1af3`).
- Report: `extra_assets` rows gain `provenance` and `illustrative`; the project payload gains `campus_study` (method and unmet needs) only with a summary.
- `solve_ppa_price` carries `campus_needs_unresolved`.

**Every stored investment case report with a campus study reads stale once** (naming the campus study): today's producer states a method and a cost tag for every asset, and these now enter the `extra_assets` digest (GK plan condition 1). A response that states none of them keeps today's digest (pinned).

### Evidence

- Backend: `tests/test_investment_case_campus_route.py` + `tests/test_finance_case_extra_assets.py` pass; the wider finance suites (`test_finance_*`, `test_investment_case_*`, `test_chat_tools_investment_case`, `test_finance_types_ts_parity`) pass locally (counts below).
- Frontend: `src/pages/results/investment/` vitest 182 passed (10 new campus cases: rows and none; method words for both methods; assumed tag and Generic default; the report's one stale line; stale study link; money refusal links; other refusal no line; unmet needs Expert and Guided, Guided stays Guided); `tsc --noEmit` clean.
- UX screenshots (condition 5): to follow in a comment.

GK conditions: 1 digest keys + pins; 2 strict types refused with the label only; 3 flag in `CampusSource.flags`; 4 `campus=None` hash pin beside S0b/P5; 5 no summary for a stale or refused study (backend test; the tab's stale line test carries a summary and ignores it).
UX conditions: 1 `openPanel`, links end in "›" (review-ux.md separator); 2 words; 3 the report's costs, no second total; 4 no freshness line of its own; 5 evidence to follow.

mp-code-review: findings fixed (summary None when nothing stated, so the assumptions hash holds for an older producer; non-record `meta` refused; "›"; Electrical design link through `openPanel`; money line no longer repeats the refusal text). Kept: the `(name, provenance, illustrative)` tuple (a plain record in the frozen case, already hashed as such); method names listed both in the route allow-list and the tab's words (the route must allow-list them).

### Merge Danger

**Door:** two-way

**Blast Radius:** investment-case

Stored campus reports go stale once (above). No change for a case without a campus study.

## Checklist

**Required:**
- [x] Changes are tested locally and behave as expected.
- [x] Code and workflow changes are documented.
- [x] A release note is added as a new file under `doc/release-notes.d/` (see its `README.md`); `doc/release_notes.md` is not edited directly.
- [x] The description is human-written and any AI-generated content is marked. (AI-generated: this PR was written by Claude Code.)

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_015y8UvMa2qFjYnZymbMv31q
