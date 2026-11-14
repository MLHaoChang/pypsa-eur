<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 14 — Harness profile: eh_ipp_hybrid

Status: ready-for-agent
Type: task
Blocked by: 07
Gated: yes (GK B, investment/finance)
Model tier: sonnet
Owner decisions: SH-PILOT-1, AI-558
Design: [design.md](../design.md) §9.3 "eh_ipp_hybrid"

## What to build

1. Profile `eh_ipp_hybrid@1`: scope `_IPP`; inputs fetch the market series; solve then `run_investment_case`.
2. Result rules: export ≤ 70 MW, `ic.min_dscr` ≥ profile value (`warn`), `ic.project_pre_tax_irr` present.
3. Explore runs the shipped `cases.json` (≤4 cases) and `solve_ppa_price` for a target IRR.

## Acceptance criteria

- [ ] A run reaches hand-off with the investment chapter; the shipped cases run as explore cases (capped at 4).
- [ ] `solve_ppa_price` result is a KPI read back at hand-off.
- [ ] Grid, reliability and control stages that the preset turns off are `skipped` with the reason.

## Likely files

`pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/harness/workflows/profiles/eh_ipp_hybrid/`, `pypsa-gui/backend/tests/test_run_profile_ipp.py` (new)

## Comments

### GK B (52) plan GO (Q1=A, Q2=A, Q3=A)
Slices: PR-1 = S1–S4 + S6 (explore skipped with its reason, issue not done); PR-2 = the `shipped` explore kind + S5 + S7 (Status done).
- I1: run_investment_case and solve_ppa_price join BOUND_CONTEXT_TOOLS; each tested to read the run's own project and refuse another `*project*` argument (#875).
- I2: the IC step settles through the harness's wait/judge, no model polling (LC-45); IC-triggered solves count against the solves budget (tested).
- I3: `ipp.export.max_mw` reads the POI link with an explicit sign; both flow directions tested; max over investment periods; unreadable sidecar limit = not_evaluated, no hard-coded 70.
- I4: `ic.min_dscr` = min over years with debt service; no debt/NaN/inf = not_evaluated; a failed warn stays covered (#1409).
- I5: `ic.project_pre_tax_irr`: no sign change/no IRR = not_evaluated; stale report and changed assumptions_hash tested.
- I6: shipped cases: approval digest binds each case's full content; card shows every change (#1379 F2); cases.json edit after approval refuses; `commercial.*` allow-list; applied to the fork's solver_config only; write bumps the fork's network revision.
- I7: solve_ppa_price card shows target IRR, year, source path, contract; missing/non-finite target = not_evaluated.
- I8: every committed series file checked against meta.json; missing/extra/mismatched hash blocks; absent meta.json refuses.
- I9: skip reasons come from the preset; the no-LOLE test asserts the rule set exactly.
- I10: trial merge runs test_harness_run_monitor, inventory, route-parity, control-matrix, test_tool_error_kind_manifest if a kind is added; PR body names the test killing each reader's mutant.

### GK B (53) review of PR-1 (#1454): NOT OK @dcf28fe8, fixed in the next push
- B1: the case re-solves the baseline LP whenever the owner keeps an existing asset, uncounted. The step is now refused (`baseline_solve`) when `baseline_plan(...).needed` or the plan cannot be read; it solves no LP otherwise.
- B2: `run_investment_case` is admitted only as the runner's own step (`session.run_step_call == call_id`), else `own_step_only`.
- A1: the DSCR 1.2 floor is labelled assumed on the rule and the card. A2: DSCR tests run the real `assemble_finance_sections`. A3: `eh_poc` read with the shared `flag_column`.
- For PR-2: `solve_ppa_price` also reaches `solve_baseline` through `_ic_build_case` (`chat_tools.py:3242`), is a read-tier tool and never hits `admit`. It needs a solve rule (count, headroom, refusal when the baseline is needed) before any stage lists it.

### GK B (56) plan GO for PR-2 (Q1=A, Q2=A, Q3=A), conditions C1–C9
Corrections to my plan (master 837cb6a7): cases.json has 8 cases plus `version`, `note`, `base`; two fall outside the allow-list (`tight_connection`: `Link.grid_export.p_nom`; `faster_degradation`: `finance.degradation_by_asset.*`); `price_series/meta.json` carries no hashes today.
- C1 (PR-SIZE-1): one PR per slice, S-A then S-B then S-C; S-D rides with the last.
- C2 (S-A): hashes go into meta.json through the generator (`eh_ipp.py`), never by hand; native byte-identity (`write_sidecars` per TEMPLATE_META id, then `filecmp` against `project_templates/<id>/`). A meta.json without hashes is never verified: the harness refuses to admit it; the human path names it, never a 500. A killing test each for: missing file, extra file, hash mismatch, missing meta.json, an entry with no hash.
- C3 (S-B, no silent cap): all 8 cases on the card with all their changes (#1379 F2); a case outside the allow-list shows "not run" with its reason, never dropped, never counted; if more are admissible than the budget, the user picks on the card (#1072) or admission refuses, never the first N; printed counts use the true total (#1353).
- C4 (allow-list): `commercial.*` and the bess field set checked against the built template (no never-lever matches), a refusal test per instance (#1429 B1); an `old` that differs from the fork's current value refuses, compared at write time under the write's lock.
- C5 (digest): covers the whole cases.json (version, base, every case incl. not-run) plus the price-series hashes the cases reference; recomputed at run time, any mismatch refuses; test an edit after approval.
- C6 (judging): a case passes only if the fork's `_network_input_fingerprint` differs from the base's; finance-only cases read not_evaluated ("investment case per case not run"), never pass on base dispatch (#1409 F1); check which price/share fields enter the LP; test both kinds.
- C7 (solves): each fork solve and the main solve are counted; the 3 → 5 budget change is on the card and in the PR body; `test_harness_run_monitor` at the trial merge; fork ids made by the harness only, no model-supplied project argument (#875).
- C8 (S-C): `solve_ppa_price` on the runner's own step only and refused on a model turn (probe `_refusal` with the grant, #1123); `baseline_solve` refuses when the plan keeps an asset; the target comes from the template and shows value, unit and source path on the card (SH-07 B3b); a missing or non-finite target or result is not_evaluated.
- C9 (Q3=A): `run.ppa_price` binds the run id, the IC record digest and the network revision/fingerprint; `read_kpis` re-checks them, a mismatch or malformed record reads stale/not_evaluated, never falls back to retained_results, never a 500; test an edit after the step.
Send each slice's PR to GK B (56).
