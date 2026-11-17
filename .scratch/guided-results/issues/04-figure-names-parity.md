# One name and unit for LOLE, NPV and LCOE across Guided, Expert, reports and chat

Status: needs-triage
Type: task
Spec: `.scratch/guided-results/spec.md`; GK B (62) condition G5, UX (23) condition 9.

Filed from slice 01's survey (master `c78a115e3`). Guided's headline reuses the existing words, so it adds no new
name; these differences already existed. The expert catalogue (EXPERT-CAT-1, its spec §3.6: one name per thing)
owns the names, so it decides which word wins.

## Differences

- **LOLE.** "LOLE" in Expert Adequacy (`McPanel.tsx:624`) and reports (`services/reports/evidence.py:432`, "MC LOLE (h/yr)");
  "Expected shortfall" in Guided (`chapters.ts:139`, `plainWords.ts:15`); "Allowed shortfall" for the goal input.
  Units: "h/yr", but Expert prints "h / N h horizon" for a non-annual run (`adequacy.tsx:331`) and the COPT harness
  metric is "h" (`harness/copt_calculations.py:38`). Guided's tile prints the raw number (`chapters.ts:166`
  `String(r.lole)`), which the shared Kpi tile then rounds.
- **NPV.** Four names: "Unlevered NPV at WACC" (Expert Investment, `financeModel.ts:176`, bare 2 dp, no currency);
  "What the design is worth" with basis "Net present value at the WACC, after tax" (Guided, `chapters.ts:76`);
  "NPV at the WACC, after tax" (report, `services/reports/chapters.py:110`); "Project NPV (post tax, at the WACC)"
  (harness, `harness/investment_calculations.py:43`). Check that "unlevered" and "after tax" name the same figure
  (`npv_at_wacc`). "Option NPV" / "Battery NPV" in the study report are a different figure.
- **LCOE.** "LCOE (real)" on Expert's Summary strip and in Guided (`headlineWords.ts:32`); "LCOE (finance-consistent,
  nominal)" and "LCOE (real)" on the Investment tab (`financeModel.ts:192-194`, "per MWh", no currency); "LCOE" in
  reports (1 dp) and Compare (2 dp). "System LCOE / LCOS" on Asset economics (`Economics.tsx:763`) is a cash-cost
  figure with the same unit string.
- **Chat.** Tool outputs carry keys (`project_post_tax_npv_at_wacc`, `lcoe_real_per_mwh`, `chat_tools.py:2972-2977`)
  with no label; the answer's words come from the model.

## Comments

Expert-catalogue design lead (2026-11-17): not in the catalogue's scope (inputs only). Recommends one
HEADLINE_WORDS-style source read by the strip, Investment, Guided, reports, Compare and the harness, each figure with a
plain name, unit and basis line, and a mode-parity test. Unlevered vs after-tax NPV needs a finance ruling (GK B)
before any name is chosen. Left needs-triage for the coordinator.
