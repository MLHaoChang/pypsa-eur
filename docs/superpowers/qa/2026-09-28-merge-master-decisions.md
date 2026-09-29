# Merge of `origin/master` into `claude/epic-allen-k2t1c4` — decisions

**Date:** 2026-09-28. **Merge base:** `ec233027`. **Branch head before merge:** `65135991b` (P26 GO). **Master head merged:** `6e9fd4bb0`.

**Strategy.** A semantic merge. The branch's gated Energy Hub contract stays authoritative: the P11/P12 stage table and its order (decision 18), budget enforcement, honest `not_established`, the `certification` section shape and `pass`/`fail`/`inconclusive` verdict, frontier and fmea_top on private copies with no closing restore (Q4), live-network isolation, the P11 hub boundary with decision 6, DtC per-Load, the P17 import energy cap, templates, `eh_review` and Guided mode. Master's genuinely new capabilities are ported into that structure:

- PR #55: import sampling with the Link's hourly cap, zonal grid areas, grid storage and common-mode events.
- PR #53: LCOH, the class-A COPT screening, and the pack's `frontier_ladder`, `fmea_top_n` and `mc_*` fields.
- PR #54: fixed-cost reconciliation. This auto-merged; it touches no EH file.

Security work is merged from both sides.

Spec amendments that record the result are in `docs/superpowers/specs/2026-09-14-eh-reference-design.md` (§4, 2026-09-28 amendment; §9, 2026-09-28 amendment).

## 1. Conflicting files

### Backend

| File / hunk | Master behaviour | Branch behaviour | Resolution | Why |
|---|---|---|---|---|
| `models/energy_hub.py`: `DtcConfig` docstring and validator | Refuses `attribution != bus_aggregate_not_per_load` | P16 opt-in `per_load` (spec §10 amendment) | Branch | Master's refusal predates P6(b) and P16. The branch's per-Load attribution is gated and specified. |
| `models/energy_hub.py`: `ArchetypePack` fields | Adds `mc_draws`, `mc_seed`, `mc_cov_target`, `frontier_ladder` and `fmea_top_n`, with validators | Adds `frontier_default` | Union of both | Master's per-pack configurability is new capability. `frontier_default` is P12. |
| `models/energy_hub.py`: `DEFAULT_EH_MC_DRAWS` | 200 | The study default was `eh_study.DEFAULT_MC_DRAWS = 500` (P13) | 500; `S.DEFAULT_MC_DRAWS` aliases it | Keeps every branch MC result and verdict unchanged. Master's tests assert only `1 ≤ default ≤ MAX` and pack == constant. |
| `models/energy_hub.py`: `DEFAULT_EH_FMEA_TOP_N` | 10 | 5 (spec §9 P12 amendment "top-5") | 5 | The gated spec value. A pack can still ask for up to 50. |
| `models/energy_hub.py`: `CertificationVerdict` | `certified`/`failed`/`no_target`/`not_established` (point comparison) | `pass`/`fail`/`inconclusive` on the 95% CI, `null` without a target (P11 Q1) | Branch vocabulary | P11 is the specified contract, and it is stricter: a CI straddling the target is not certified. |
| `models/energy_hub.py`: `REPORT_SECTIONS` | `certification` right after `target` | `certification` appended at the end | Master's position, listed once | The auto-merge had listed it twice. No branch test pins the position, and master pins index = target + 1. |
| `services/adequacy/eh_study.py` (8 hunks) | Monolithic driver on the LIVE network. Frontier, certify and FMEA bodies live in `eh_stages` (closing restores, state published through `state_update`). | P11/P12 stage table on a private network and cfg copy | **Branch driver kept whole**, master capabilities ported in (§2) | Live-network isolation (`test_live_network_untouched`), Q4 private copies, budget share and honest `not_established` are gated. Master's driver would break all of them. |
| `services/chat_service.py`: `_sanitise_ui_value` | 8× work cap, then the linear fixpoint neutraliser `_neutralise_untrusted_delimiters` (c671f5e83) | P25 fixpoint `while` loop | **Both**: work cap → master's neutraliser → the branch's loop as defence in depth | The two are equally strict: both reach a fixpoint with no delimiter. Master's is linear, which fixes the DoS. The branch's loop now runs after it, is bounded by the work cap, and never iterates when the neutraliser is correct. Both test sets pass. |
| `tests/test_chat_sse.py`: `test_invalid_decision_returns_400_and_preserves_token` | Session records an owner (`seeded_identity`, 7a6edcc2f) | TTL raised to 60 s so the test does not race the 0.3 s TTL | Both | The fixes are orthogonal. |
| `tests/test_energy_hub_study.py` (2 hunks) | Asserts that the certification section is `not_established`, and that the frontier stage runs | Same, plus `certified is None`; frontier `ok` with the pack target swept | Union of assertions | Neither side's assertion contradicts the other. |

### Frontend

| File / hunk | Master behaviour | Branch behaviour | Resolution | Why |
|---|---|---|---|---|
| `frontend/src/api/simulation.ts`: report `tea` and `pipeline` | `EhTeaBlock` with an LCOH flag; `pipeline {aborted, solves_consumed}` | `pipeline` adds `budget_solves` and `stages[]` | Union | Both are additive. The verdict type now accepts both vocabularies, and `EhImportModel` gains `excluded`, `import_firmness` gains `not_counted` and `EhCertificationPayload` gains the P11 fields. |
| `EhReferenceDesignPanel.tsx`: imports and `COMPLETENESS_ORDER` | New types; certification after target | Readiness and template types; certification last | Union; master's order | Matches `REPORT_SECTIONS`. |
| `EhReferenceDesignPanel.tsx`: headline chips | `eh-report-mc-lole` and `eh-report-verdict` | `eh-report-lole` (h/yr + CI) and `eh-certification-verdict` (smoke-pinned) | **Branch chips only.** The duplicate master chips are removed. | One headline. `certificationHeadline` and `verdictTone` also read a master-shaped stored report (`certified` → `pass`, `mc_lole_h` basis). |
| `EhReferenceDesignPanel.tsx`: certification, frontier and FMEA blocks | Separate blocks: certification detail (import model, grid areas, common mode, scope note, warning), frontier table (`data-knee`, basis, shed h, warning, note, 8-column CSV), FMEA `top` table | Frontier table (`data-pack-target`, knee label), Class-B FMEA table (`eh-fmea-row-i`, unsolved line), not-established notes list | One block each: master's certification detail block, kept; the branch's frontier block carrying master's attributes, columns and lines; one FMEA table listing Class-B `rows` then the `class_a` rows (`eh-fmea-top-row-{rank}`, `data-class`), with master's COPT import, notes and ranking lines read from `class_a` | Both test sets render against the same DOM. The CSVs take master's richer columns (decision 3 fields; class and component, now that class-A rows sit beside Class-B). |
| `EhReferenceDesignPanel.test.tsx` | Suites: wired stages, import model, zonal open items, WP5 review | Suites: P18 pipeline table, templates, and more | Both suites kept | Adapted cases are listed in §3. |

## 2. Semantic decisions (ported capabilities)

| # | Topic | Master (#53/#55) | Branch (P11–P26) | Resolution | Why |
|---|---|---|---|---|---|
| S1 | Where the stage bodies live | `eh_stages.run_frontier_stage`, `run_mc_certify_stage`, `run_fmea_top_stage`, `frontier_targets_for` and `certification_verdict` | `eh_study._stage_*` handlers in `_STAGE_HANDLERS` | Branch handlers. The five master runners are removed from `eh_stages`, which keeps the fleet machinery: `FleetScope`, `hub_fleet_scope`, `freeze_fixed_plan`, `_grid_areas`, `_screen`, `_rank_import_links_once` and `_flatten_mode`. | Two implementations of each stage would drift. Master's runners also restore on the live network, which the branch's Q4 guard tests forbid. |
| S2 | Hub boundary and sides | `hub_fleet_scope`: `bus0` is the grid unless flipped by `eh_critical`. The carrier rule or a non-separating Link falls back to `whole_network`. | `archetypes.hub_boundary_copy` (P11 B1/R1). Carrier-only, ambiguous, inverted or loadless boundaries and non-separating Links are refused → `not_established`. | Branch boundary is authoritative. `hub_fleet_scope(…, boundary=info)` takes its sides (`removed_buses`) and skips its own fallbacks. | Master's fallback certifies a copper plate that counts the grid as local capacity. That is exactly the P11 gate probe the branch refuses. |
| S3 | A Link without its own outage data | Counted as a firm block at the planning cap (`planning_limit_only`) | Excluded from the MC fleet (spec §4 P11, decision 6) | Branch: new model `excluded`, firmness `not_counted`. It is never a firm block. The same applies to a Link with a rate but no finite MTTR, and to an energy-limited import (P17). | Decision 6: import is firm only when its outages are modelled. The P11 amendment pins this. Master's default is unchanged when no boundary is given, so master's unit tests still hold. |
| S4 | A counted Link's capacity | Two-state unit, UP = the hourly cap series | Two-state unit at the horizon-mean cap (synthetic generator `eh_import_unit_*`) | **Master (hourly)** | Strictly more capable: exact for a time-varying `p_max_pu`, and identical for a constant one. `hub_boundary_copy` still records the boundary's decision (`fleet_boundary`). |
| S5 | The grid behind a counted Link | Zonal two-area MC (`mc_zonal`): one area per grid reached, grid storage grid-first, common-mode events | Far side removed | **Master**, for counted Links only | New capability (PR #55). Areas are built only from counted Links: an excluded Link reaches no area. |
| S6 | Common-mode event on an excluded Link | Event-only path: a firm block plus a sampled event (`common_mode_sampled`) | n/a | Disclosed as `applied: false`, with reason "import Link not counted in the MC fleet" | This follows from S3. It is a question for the product owner (§5 Q1). |
| S7 | Certification payload | `mc_lole_h` (per horizon), `ens_met`, `fleet_scope`, `import_model`, `import_firmness`, `engine`, `fidelity`, `draws_requested`, `certification_metric` | P11 keys: `lole_h_per_horizon`, `lole_h_per_year`, `target_lole_h_per_horizon`, `horizon_years`, `verdict`, `confident`, `met_on_mean`, `fleet_boundary`, `dsr_note` | Union. `mc_lole_h` in the payload is **per year**, the same number as `report.mc_lole_h`. | The P11 per-year headline (spec §4) wins on units. Master's disclosures are additive. The verdict rule is extracted as `eh_study.certification_verdict`. |
| S8 | Refusals before the MC | Empty fleet → `not_established` | Also: bad transition probabilities, `horizon_years ≤ 0`, horizon < the largest MTTR (Q2) | Branch checks, extended to grid-area units and common-mode MTTRs | A zonal area repairs on its own clock too. |
| S9 | MC draws, seed and CoV | Pack fields | Request `mc` options (P13) | Request wins, else the pack. Both defaults are the P13 values (500, 0, 0.05). | Both capabilities stay, and no existing result changes. |
| S10 | fmea_top | Merged A+B top-N (`top`), class B with a closing restore on the live network, class-A-only fallback reported `ok` | Class-B Link ranking on a private copy, no restore, no partial sweep (`rows`) | Branch `rows` decide the section status. Master's class-A COPT screening is added as `payload.class_a`, with zero solves, the same hub-side fleet, the import Link ranked once, `copt_metrics.import_exact`, and `fleet_scope`. `class_a` is also attached when the Class-B ranking is `not_established`. | This keeps decision 14 and the P12 amendment. The class-A capability and all its #55 screening work are not lost. Whether to merge A and B into one ranking is a product-owner question (§5 Q2). |
| S11 | Frontier targets | Ladder × cap (4, 2, 1, ½, ¼). At least 3 points plus a closing restore. `skipped` on budget. | Cap plus the nearest `DEFAULT_TARGETS_PERMYRIAD`. At most 40 % of the budget, at least 2 points, private copy, no restore. `not_established` on budget. | Master's ladder supplies the candidates; the branch's budget rule selects from them, keeping the cap first, then the nearest in log distance. | The pack field must drive the stage, or it would be silently ignored configuration. The gated budget and restore rules are kept. Points are also tagged `excludes_shed_cost: true`, and `n_ok`, `ladder`, `voll_eur_per_mwh` and `engine` are added (master). |
| S12 | LCOH | `compute_tea(network=, cfg=)` | LCOE only | Master, computed on the study's private solved copy | New capability (decision 9). |
| S13 | `ens_met` | In the payload | — | Adopted. On a `fail` verdict with ENS met, the note cites decision 2. | Master's disclosure. The verdict rule is unchanged. |
| S14 | Chat tool copy and CHATBOT.md (`run_eh_study`) | "verdict certified/failed", "firm block at the planning cap otherwise", "Class B + Class A" | P11/P12 wording | Rewritten to the merged semantics: `excluded`, the P11 verdict, the ladder, `class_a` | The copy must describe what the tool does. |
| S15 | Readiness frontier estimate | — | `S.frontier_targets(cap, n)` | Passes `ladder=pack.frontier_ladder` | Keeps the estimate equal to the stage. |
| S16 | QA driver `tests/qa_eh_reference_design.py` (master) | Asserts master's shapes | — | Rewritten to the merged contract. The weak_flexible run requests the frontier (it is not a default stage for weak_flexible, P12). | 110/110 steps pass. |

## 3. Tests adapted: the "other side deliberately changed it" exception

No test was deleted. Each adaptation below keeps the test's intent and re-points it at the behaviour the merge chose.

| Test | What it pinned | Why it changed |
|---|---|---|
| `test_energy_hub_certify.py::test_mc_certify_is_implemented_in_the_driver` | `eh_stages.run_mc_certify_stage` exists | Retargeted to `S._STAGE_HANDLERS["mc_certify"]`, `freeze_fixed_plan` and `S.certification_verdict` (S1). |
| `…::test_certification_verdict_rule` (5 cases) | Point comparison: certified, failed, no_target, not_established | Re-parametrised on the P11 CI rule, now 6 cases including straddle and resolution floor (S7). The P11 Q1 rule deliberately replaces the point comparison. |
| `…::test_off_grid_pack_certification_fails_on_lole_even_when_ens_met`, `…weak_flexible_default…`, `test_energy_hub_certify_scope.py::test_off_grid_certification_ignores…`, `test_energy_hub_import_outages.py::test_off_grid_islanded_hub_certification_is_unchanged` | `failed`/`certified` | Now `fail`/`pass` (vocabulary), and each also asserts `certified`. |
| `test_energy_hub_certify_scope.py::test_weak_flexible_counts_import…` | Class-A names in `fmea_top.top` | Now read from `fmea_top.class_a.rows` and `class_a.fleet_scope` (S10). |
| `test_energy_hub_fmea_top.py` (5 tests) | `top`, `classes_included`, `class_b.base_restored`, charge base + 1 + restore, a class-A-only fallback reported `ok`, `FMEA_TOP_N == 10` | Now Class-B `rows` plus `class_a`. Charges are base + 1 (Q4 no restore). The fallback is `not_established` with class A still reported (P12: no partial ranking). N = 5 (P12 spec). |
| `test_energy_hub_import_outages.py::test_fmea_top_ranks_the_import_link_once…`, `…keeps_the_class_a_link_row…` | `top`, `import_link_ranking` at the top level | Read from `rows + class_a.rows` and `class_a.import_link_ranking`. The "cannot run" case also asserts `not_established` (P12). |
| `test_energy_hub_zonal_review.py::test_fmea_top_ranks_the_link_when_the_hub_has_no_sampled_unit` | "COPT screening skipped" because the grid area is MC-only | The Link has a rate but no MTTR, so it is not counted (S3), no area is built, and `class_a` is `not_established` with an "occurrence" reason. The Class-B row assertion is unchanged. |
| `test_energy_hub_frontier_stage.py` (8 tests) | `frontier_targets_for`: 3-point minimum, `+1` restore charge, `base_restored`, `skipped` on budget | Retargeted to `S.frontier_targets` and `S.frontier_point_count`: 2-point minimum, no restore charge, `restore_skipped_on_private_copy`, `not_established` at budget 2 (S11, Q4). |
| `test_energy_hub_frontier_fmea.py::test_frontier_targets_keep_the_pack_cap_and_its_neighbours` (branch) | Neighbours drawn from `DEFAULT_TARGETS_PERMYRIAD` | The same assertion now passes those targets as a ladder, plus a new assertion for the default ladder (S11). |
| `test_energy_hub_review.py::test_get_eh_template_and_put_stress_scenarios_via_chat` (branch) | The project double had no `uuid` | Master's sidecar lock gate (68e5f62c3) reads `project.uuid`. The double now carries `uuid="u-1"`, as master's own handler tests do. No lock is bypassed. |
| `EhReferenceDesignPanel.test.tsx`: "flattens frontier points and FMEA rows" (branch) | 4-column and 6-column CSVs | These are now master's 8-column and 9-column CSVs. |
| `EhReferenceDesignPanel.test.tsx`: "renders MC LOLE, the verdict…", "shows a failed verdict…", two `queryByTestId('eh-report-mc-lole')` negatives (master) | Master's headline testids | Now target the single headline: `eh-report-lole` and `eh-certification-verdict`, with the verdict normalised to `pass`/`fail`. |

New tests:

- `tests/test_energy_hub_merge_master.py` (12 tests) covers S3, S4, S5, the energy-limited exclusion, P11 sides, zonal e2e, class-A beside Class-B, top-N, the ladder, MC option precedence, the P13 default and the LCOH flag.
- Five new vitest cases cover normalised verdicts, the `excluded` label, B-then-A ordering, the class-A reason, and the per-year CI.

## 4. Regenerated artifacts

- `tests/fixtures/route_inventory_phase0.txt`: regenerated with `tools/openapi_diff.py --phase0-fixture`. It gains master's `POST /api/gridspine/{name}/dispatch-source/external`.
- `tests/fixtures/eh_archetypes/mvp_a_report_skeleton.json`: no generator script exists. `test_mvp_a_report_fixture_round_trips` validates it against `ReferenceDesignReport(archetype="strong_grid", pack_hash="fixture-pack", assumptions_hash="fixture-assumptions", ens_cap_permyriad=10.0, sections=empty_section_map())`. It was rewritten from exactly that model with `model_dump_json(indent=2)`. The auto-merge had left `certification` in it twice. The rewrite is equal as data to the merged file and lists `certification` once, after `target`.
- `pypsa-gui/pypsa-gui.spec` gains the `gridspine.drivers.year_study` hidden import. Master's `gridspine_service` imports it inside the gridspine guard (increment 7), but master's spec never named it, so master's own guard test `test_packaging_requirements.py::test_the_spec_names_every_gridspine_module_the_backend_guard_imports` fails on master too. The merge's full suite caught it.
- The pack fixtures (`*_pack.json`) are unchanged. The new pack fields default, and the round-trip test compares against the factories.

## 5. Verdicts (P26 smoke, per template)

| Template | Before merge (P26 gate) | After merge | Headline after merge |
|---|---|---|---|
| `eh_datacenter` (weak_flexible) | `fail`, about 12 h/yr vs 3 | `fail` | "Not certified: about 12 h/yr of shortfall vs a 3 h/yr goal — driven by site_transformer" |
| `eh_h2_hub` (strong_grid) | none (no goal) | none | "No reliability goal is set…" |
| `eh_microgrid` (off_grid) | `inconclusive`, 3–6 h/yr | `inconclusive` | "Not decided: the shortfall estimate (3–6 h/yr) straddles the 3 h/yr goal…" |

No verdict changed, and no smoke expectation was edited. The templates' grid supply carries no occurrence data, so no zonal area is built. The data-center PoC cap is constant, so the hourly cap equals the old mean.

## 6. Verification

- Backend, full suite (`-m "not slow"`): 6563 passed, 31 skipped, 1 failed. The one failure was the packaging guard above; after the spec fix its file passes (10/10). No code changed after the suite except `pypsa-gui.spec` and docs.
- EH subset after the adaptations: 147 passed.
- QA driver `tests/qa_eh_reference_design.py`: 110/110.
- Frontend: `tsc --noEmit` clean; vitest 234 files, 2610 tests passed.
- Guided smokes P26, P25 and P22.9 all PASS. P22.9 verdict: `fail`.

## 7. Questions for the product owner

1. **Common-mode event on a Link without its own outage data (S6).** Master counted such a Link as a firm block and sampled the event. Under decision 6 the Link is not counted, so the event is disclosed as not applied. Should a common-mode event alone qualify an import as "outages modelled"?
2. **One FMEA ranking or two (S10).** Master merges class A (COPT) and class B (LP re-solve) into one top-N. The branch spec says top-5 Class-B Link modes. The merge keeps them separate: Class-B `rows` decide the section, and `class_a` sits beside them. The panel lists both in one table, B first. Should the two engines' criticalities be ranked together?
3. **Frontier point floor.** Master required 3 points; P12 requires 2. The merge keeps 2 (gated). Confirm.

## Product owner decisions on the merger's open questions (2026-09-29)

| # | Question | Decision |
|---|---|---|
| Q1 | A common-mode event on a grid-import Link that has no outage data of its own | **Exclude and disclose.** The Link is not counted, and the report states that the event was not applied. The merged behaviour stands. |
| Q2 | One FMEA ranking or two (class A COPT screen vs class B LP re-solve) | **Two lists, kept separate.** Class B decides the section, and class A sits beside it as `class_a`. The merged behaviour stands. |
| Q3 | Frontier minimum points | **A curve needs at least 2 points; the knee needs 3.** The frontier is reported from 2 points. Below 3 points the knee is `not_established` (never reported). This must be verified in code before the merge into master, and fixed test-first if it is not already so. |

## Follow-up to the independent review (2026-09-29, `2026-09-29-merge-master-review.md`)

The review returned NO-GO on two blockers. Both are fixed test-first, as new commits on top of the merge commit `f44ae8f33`.

| Item | Finding | Fix | Tests |
|---|---|---|---|
| **B1** | The chat tool `put_stress_scenarios` (branch P22) called master's lock-gated sidecar handler (68e5f62c3) bare. `db` and `user` arrived as `Depends` sentinels, and every real project crashed in server mode. The adapted unit double `uuid="u-1"` skipped the lock branch and hid this. | The call goes through `_route`. `_route` also injects `actor`. | `tests/test_chat_tools_handler_dependencies.py`: the holder writes through chat; a non-holder gets 409 `project_locked`, both on a real project uuid. The `u-1` case stays as the no-lock-row control. |
| **N6** | `update_solver_config` (pre-existing on master) called the handler without `db` or `actor`, so the user-code admin gate crashed. | It goes through `_route` when an acting user is bound. With no acting user it passes `db=None`, `actor=None`, and the gate then refuses user code (fail closed). | An admin may set `extra_functionality_code` through chat; a member gets 403 `user_code_forbidden`; with no acting user it is 403; an ordinary knob still works. |
| **Scan** | Every chat tool that calls a router handler directly. | An AST guard with a self-test, as a test. It covers both `from routers… import` and `import routers… as m`. It found exactly two, `put_stress_scenarios` and `update_solver_config`, and both are fixed. Every other handler call already goes through `_route`, which raises on any dependency it cannot satisfy. | `test_no_chat_tool_leaves_a_handler_dependency_unsupplied` and `test_the_guard_sees_a_direct_call_that_misses_a_dependency`. |
| **B2** | Owner's Q3 rule: a curve needs 2 solved points, a knee needs 3. The merge reported a knee from 2, and a panel test pinned that. | Added `eh_stages.MIN_EH_FRONTIER_KNEE_POINTS = 3`. The stage writes `knee_index`, `knee_status` and `knee_note`; below 3 OK points the knee is null and `not_established`, with the reason. `eh_review` emits no `frontier_knee` below 3 OK points, even for a report stored earlier. The panel marks a knee only from 3 OK points and otherwise shows `eh-frontier-knee-note`. `EhFrontierPayload` gains the new fields. | Stage with 2 points gives no knee; the 3-point positive control gives a knee; review with 2 vs 3 points; two vitest cases; a QA driver step. **Owner-driven test change:** `EhReferenceDesignPanel.test.tsx` "renders the frontier and FMEA top-N tables" asserted a knee from its 2-OK-point fixture. It now asserts no knee plus the knee note. |
| **N1** | The panel and the CSV numbered class-A rows after class B, which reads as one joint ranking. | Each class is ranked within itself. A mixed table shows `B1…` / `A1…` (`data-rank`) and a note that the two criticalities come from different engines. Row testids are positional. A stored master report with a joint `top` keeps its own ranks. | The vitest merge case asserts `[1,'B'],[1,'A']` in the modes and the CSV, `data-rank` `B1` / `A1`, and the engines note. |
| **N2** | `class_a` was dropped when the Class-B sweep failed or was aborted. | Both paths keep `{"rows": [], "top_n", "class_a"}`. | `test_class_a_survives_a_failed_class_b_sweep` and `…_an_aborted_class_b_sweep`. |
| **N3** | Test changes the first pass did not document (listed below). | Documented here. The dropped verdict case is restored. | `test_no_mc_lole_gives_no_verdict`. |
| **N8** | `routers/projects.py` loads `project_templates/eh_templates.py` by file path (`parents[1]` of the router), so it must sit at the bundle root. | Added to `smoke/check_bundle.ROOTED`. | The rooted-bundle test now shows it is flagged when missing and accepted when present. |
| **N5** | Master's own issue: `POST /api/results/eh_study/abort` is missing from `_FOREIGN_LOCK_GATE_EXEMPT_EXACT` (`main.py`). A foreign lock taken during an EH study traps the abort. The "exactly ten write routes" comment also undercounts: there are twelve. | **Not changed here.** Recorded as a finding for master. | none |

Test changes the first pass did not list (N3):

- **`test_energy_hub_frontier_stage.py::test_a_custom_ladder_is_used_and_needs_three_factors`** was renamed `…_needs_two_points`. Its second assertion is inverted: a 2-factor ladder is now accepted, because the curve floor is 2 (Q3).
- **`test_ladder_needs_a_positive_cap`** asserted that master's `frontier_targets_for` returns no targets for a cap of `None` or `0`. That function is removed (S1). The test now asserts that the model refuses `ens_cap_permyriad=0`. The `None` cap is covered end-to-end by `test_energy_hub_frontier_fmea::test_frontier_without_a_pack_ens_target_is_not_established`.
- **`test_ladder_skips_below_minimum_points`** was parametrised over `[0, 1, 2, 3]` remaining solves and asserted a "budget" reason. It now uses `[0, 1]` and asserts that `frontier_point_count` falls below the 2-point floor. With 2 or 3 remaining solves the P12 rule does sweep 2 points. The "budget" note is asserted end-to-end in `test_frontier_skipped_when_budget_cannot_afford_two_points`.
- **`test_frontier_skipped_when_budget_cannot_afford_three_points`** was renamed `…_two_points`. It tests budget 2.
- **`test_certification_verdict_rule`**: master's `(None, 3.0) → not_established` case was dropped in the first pass. It is restored as `test_no_mc_lole_gives_no_verdict`. `certification_verdict` now returns no verdict for a missing or non-finite CI.

Left open, as non-blocking review notes:

- **N4:** a pack ladder of `(1.0,)` passes the validator and sweeps a single point.
- **N7:** the certification block shows EUE per horizon without a basis label.
