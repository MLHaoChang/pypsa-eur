<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 36 — eh_site: run_investment_case for the investment chapter

Status: ready-for-agent (Opus; GK B plan first; finance code)
Type: task
Gated: yes (GK B)
Model tier: opus
Parent: [07-report-handoff-and-pilot.md](07-report-handoff-and-pilot.md) (GK B (33) P7)
Design: [design.md](../design.md) §9.2, §9.3 eh_site

§9.3 names the investment chapter, which needs `run_investment_case`. `eh_site@2` does not run
it, so its report lists the investment chapter as a gap (SC-15). Add it after the solve stage (built: at the head of the report stage, see below) in
a new profile version (L8), charged to the run's budget, and read its KPIs in the hand-off.
Investment/finance code: GK B plan first.

## Comments

### 2026-11-11: triage

Checked on master: `eh_site@2` lists the investment chapter as a gap (SC-15) and does not run `run_investment_case`. Verdict: ready-for-agent.

- Blocked by: none.
- Likely files: `pypsa-gui/backend/services/run_profiles.py` (a new `eh_site@3` profile, L8), `harness/run_eh.py`, `services/run_gates.py`, `tests/test_harness_run_eh_site_v2.py` and a sibling for v3. Reviewer: GK B (plan first). Also `services/finance/` for the runner; the plan goes to GK B before code.

Acceptance:
- [ ] A new profile version runs `run_investment_case` after the solve stage (built: at the head of the report stage) and charges it to the run's budget.
- [ ] The hand-off reads the case's KPIs; the report no longer lists the investment chapter as a gap when the case ran, and still does when it did not.
- [ ] A refused or failed case leaves the gap with its reason, never a missing chapter.
- [ ] Earlier versions are byte-equal (L8); the investment case's own results are unchanged (a pin).

### 2026-11-17: plan GO (GK B (60))

Built as `eh_site@4` (issue 33 had already shipped `eh_site@3`). The rulings:

- Q4: one PR and one version per issue, in the order 36 → `@4`, 34 → `@5`, 35 → `@6`.
- Q1: the case spends 0 solves (GK B (52) I2).
- B1: the case's wait ends at the run's wall time (the envelope's `expires_at`) when that comes first. A case still running then ends the step `failed` with the typed kind `ic_wall_time`, never completed.
- 36-K1: the hand-off reads only figures the stored case holds, through `ic_counterfactual.read_returns` (project post-tax NPV at WACC and IRR) and `read_pre_tax_irr`. Nothing is computed anew.
- 36-K2: a case that is stale for the network reads `not_current`, never its old number.
- Follow-up (a): the profile action carries `on_end: chapter_gap` (load accepts it on the solve stage's `run_investment_case` only). A case that ends refused, failed or aborted, or that runs past the wall time, keeps the step `failed` with its error. The step is `judged_by: report`, so the run goes on. The report's investment chapter is then the gap `Chapter not established: Investment case (<error>)`, even over a case the project stored before. An `uncertain` step still stops the run. `eh_ipp_hybrid@1` is unchanged.
- The hand-off states a case figure the case did not establish as `not_established` with the reader's reasons. `ho.kpis_read` accepts it when it equals the stored row.

### 2026-11-17: the case moved to the report stage (GK B (60) GO, M1–M3)

The build runs the case at the head of the report stage, not on the solve stage. The result gate's coupling loop re-solves after the solve stage, so a case run there was stale against the final network (`changed:dispatch`) and the hand-off read `not_current`. GK B (60) gave option 1 a GO on these conditions:
- M1: the stored case's inputs equal the final solved network's, and a case moved back to the solve stage goes red.
- M2: no stale read, and a report stage that is re-entered does not re-run a completed case.
- M3: A2's reservation and B1's deadline are tested at the report-stage position.
- F1: `_ic_build_case` reserves and checks under one lock; a refused reservation runs no LP; nothing is refunded below what ran; and a mutant that skips the call goes red.
Profile load accepts `on_end` on the report stage's case only (O1, adjusted from "solve stage" above).

### 2026-11-17: GK B (61) amends A5

The gap kinds (`ic_budget`, `ic_wall_time`, `ic_case_refused`, `ic_case_failed` and `ic_case_aborted`) are step errors, not tool errors. The manifest guard (`test_tool_error_kind_manifest`) is right to refuse them a tool-error-kinds.d fragment, so there is no fragment. In its place:
- (a) `run_ic` owns the set, pinned by exact equality; `run_envelope.IC_BUDGET_KIND` is `run_ic.BUDGET_KIND`.
- (b) Every chapter-gap row carries a kind from the set. A foreign exception maps to `ic_case_failed`, never raw text and never a 500.
- (c) No tool_error frame on the run path carries one of these kinds. Should one ever need to, it gets a fragment in that PR.
- (d) Each kind has a test of its own trigger.
