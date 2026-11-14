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

### GK B (56) on the S-B split: A (S-B1 pure module, S-B2 engine/card/budget/judging), plus C10
- C10 (S-B1): name the source of the cases and series. A project copy of cases.json is editable and S-A verifies only the template dir. S-B1 reads the project's cases.json and, before admission, verifies the template's price series (S-A's `verify`) and takes the hashes the cases reference from that verified meta.json; the PR body says which source is used and the digest binds that exact source.
- S-B1 tests also cover a malformed cases.json: not a dict, cases not a list, a change with no path, a duplicate id; each refuses with a typed code, never a 500 (#1390 F2).

### S-B1 (#1508) merged; GK B (57) OK @d44b0a22; S-B2 plan GO (GK B (57), re-confirmed by GK B (58)), conditions D1–D8
- S-B1 binds the project's own Library refs in the digest (C10, v2); survivors listed in #1508's body. C10b/C10c carry to S-B2.
- D1 (C10b, named series with no project ref): no run-time refusal. (a, recommended) mint/look up from the VERIFIED template file under the write's lock and check the ref's hash is put_series' hash of that content, or (b) `not_run` at admission with the reason (on the card, counted in total). Either is tested.
- D2 (C10c): before any solve the fork's patched commercial block validates through `CommercialConfig` like a user edit; failure reads `not_run` with the validator's reason, shown and counted.
- D3 (C4): the `old` compare and the write run in one critical section under the fork's write lock (`template_marker.write_lock`-style, held across the whole read-modify-write); test a second writer inside the read.
- D4 (C5): `require_current` before EVERY fork and again under the lock right before the solve. Tests: an edit of cases.json, a template series, the project's ref after approval each refuse `shipped_cases_changed`, no fork created, no solve counted.
- D5 (C6): settle the LP classification before coding: each of the 6 admissible cases' fields as enters-LP or finance-only, with the reader that proves it (`lp_bindings` / `binding.py`). Judge keys on the classification and the fingerprint together; a finance-only case reads not_evaluated even when the fingerprint differs for an unrelated reason. Open question found: contract price/share/tenor settle on the solved dispatch, but a dispatch-bound PPA's price enters the LP (`ic_ppa_price`, `lp_bindings.py:2343`), so the class depends on the contract's kind.
- D6 (C7): reserve each fork solve before it starts; a refused or failed fork releases or counts it, and the run record says which. 3 → 5 on the card and in the PR body. `test_harness_run_monitor`, route-disposition, ACL, write-lock, golden-coverage and the inventory/parity/matrix checks at the trial merge.
- D7: forks are cleaned up or listed; a run stopping mid-way leaves no fork the card does not name; test with a raising fork.
- D8: stay under PR-SIZE-1 (split card from engine past ~1,500 lines); mutants.py on the engine module and the fork helper, survivors listed.
- Fork helper (GK B (57)): a dedicated helper, not the `run_sensitivity_sweep` tool entry. Factor the shared internals (copy the project, apply one solver_config patch, bump the revision) so the sweep tool and the runner both call it. The runner passes only the admitted case id and the approved digest; the fork id comes from the harness (#875); a test shows the runner path's signature takes no project or changes argument from a model turn.
- Map (from the S-B2 survey): explore stage `harness/run_explore.py` (`due` :662, `advance` :1165, `steps_for` :911, engines NETWORK/STUDY, no explore card today :515); profile skip `services/run_profiles.py:935-938`, budget `:947`; fork internals `services/workflow_tools.py:613-701`; fingerprint `workflow_tools.py:501`.
