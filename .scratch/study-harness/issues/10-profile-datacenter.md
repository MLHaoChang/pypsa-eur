<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10 — Harness profile: eh_datacenter and eh_datacenter_staged

Status: done (10a: #1283 and its test follow-up; 10b: #1333)
Type: task
Blocked by: 07
Gated: yes (GK A for the engine gates and the grid-readiness actions; GK B for the harness parts)
Model tier: opus
Owner decisions: SH-PILOT-1, SAGC-1, D3-KEEP, DC13-SCOPE, TOOLSEL-1
Design: [design.md](../design.md) §5, §6.0, §9.3 "eh_datacenter"
Plan verdict: GK A GO, 28 Oct (screening rows, verdict gating, never-tunable list); sources: public SAGC (May 2026) and generic engineering only

## What to build

1. Profiles `eh_datacenter@1` and `eh_datacenter_staged@1` (per-period rules over 2027/30/35 from `TEMPLATE_META`).
2. Native actions for the runner: the campus/gridspine run that produces the SCR/ESCR, PQ, contingency and P–V/Q–V rows, and the grid-readiness read (`grid_readiness_for`). Same admission, ACL, lock and audit path; not offered to the model.
3. `dc.readiness.*` and `dc.gridcode.*` fact readers. Input stage waits (`needs_user`, reason "publish the grid-code profile") until a published profile exists; limits are never tunable or researched into the profile.
4. Only if chat must start these studies: one `grid_readiness_study(kind=scr|pq|contingency|pv_qv)` catalogue tool, `execution` tier, with the TOOLSEL-1 selection test.

## Acceptance criteria

- [ ] A DC fixture with a published blank-schema profile (SAGC fields filled per project) runs to hand-off; each scorecard row is a gate result by verdict (only `pass` passes), with its `assumed` flag; screening rows read "within the screening limit", and a pass on assumed inputs is never "verified".
- [ ] P–V/Q–V gates through the scorecard rows `dc.readiness.voltage_stability_pv` and `dc.readiness.voltage_stability_qv` as `warn` (a margin is screening; issue 18). dc.readiness.* reads through grid_readiness.readiness_fact, so a row excluded off-grid is not evaluated with its reason, never a pass.
- [ ] Grid-code publish is always the user's own card; the envelope never admits it.
- [ ] The profile has no `dc.readiness.*` wildcard: a table lists each row id or family with its severity (`block`/`warn`), a one-line basis and the run that produces its input (this also answers which run produces each SAGC study); a `warn` row that is `unknown` reads "not evaluated".
- [ ] On the shipped template (19 of 20 rows `unknown`), the run does not end `needs_user` on `warn` rows.
- [ ] Family rows (`energization_peak:*`, `dynamic:*`, `ride_through:*`, `motor_start:*`) evaluate all rows of the family.
- [ ] No published profile → the run waits at stage 2 and notifies; nothing is drafted or published by the run.
- [ ] A contingency failure: L2 changes only tunables; taps and shunts only in stage 2 after an outage (DC13-SCOPE); redispatch is not attempted.
- [ ] 60 Hz project: the frequency rule passes; a 50 Hz network under a SAGC profile fails `block`.
- [ ] Staged: rules evaluated per period; the investment case (NPV/IRR) appears in the result gate.
- [ ] Offered-tool count stays within the 128 limit with `validate_network` still selected (TOOLSEL-1 test).

## Likely files

`pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/services/run_actions.py` (new, native action registry), `pypsa-gui/backend/harness/workflows/profiles/eh_datacenter/`, `pypsa-gui/backend/tests/test_run_profile_datacenter.py` (new)

## Comments

### GK B (36): plan verdict, GO WITH CHANGES for 10a and 10b (D1–D8, verbatim in substance)

Slices (PR-SIZE-1): **10a** = eh_datacenter@1, the native-action registry (`services/run_actions.py`: `dc_campus_study`, `dc_grid_readiness`), the `dc.gridcode.*` facts, and the input and result rules with the scorecard row table. **10b** = eh_datacenter_staged@1 (per-period rules; IC NPV/IRR in the result gate), the contingency fact and L2's DC13-SCOPE restriction, and the TOOLSEL-1 count test. There is no 10c: no `grid_readiness_study` chat tool.

Answers:
- Q1 = A. The frequency rule compares the network's frequency with an intake param `grid.frequency_hz` (enum 50|60). The user gives it, it is never tunable, and it counts only because the card shows its value and unit (SH-07 B3b).
- Q2 = A. A missing campus file → needs_user "draft the campus". No new write path.
- Q3 = A, in 10b. A `dc.campus.contingency` fact reads the campus run's results; GK A confirms the source. Tests pin the taps/shunts exclusion and that redispatch is never enabled.
- Q4 = A. No chat tool. The TOOLSEL-1 count test (≤128 offered, `validate_network` still selected) goes in 10b.
- Q5 = A. LOLE works as in eh_site@2: `run_eh_study` in solve, the coupling-loop engine rule, and the goal param, through `reliability_goal.loop_goal(metrics.horizon_years(n))`. Solves = solves_used + probe_solves + the closing re-solve, on every path.

Conditions:
- D1 (which grid-code profile). The run's published grid-code id is fixed at the grant (or the stepwise card), from a profile the card names with its id, label and content digest. When more than one profile is published, the user picks on the card, never the runner (#1072). At `dc_campus_study` dispatch and at the result gate, the published profile's digest is compared with the frozen one. Republished or edited mid-run → needs_user; the run never reads the new profile. Test: a republish between the solve and the gate → the run does not pass.
- D2 (the scorecard is read once). The result gate's `_scorecard` reads with the frozen profile id and compares its digest with the one the `dc_grid_readiness` step stored. A mismatch gives NoEvidence on every scorecard row, never a fresh pass. Test it.
- D3 (an assumed input never passes a block row). A block row whose pass rests on a generic_assumed or code-default input (today, scr on generic_assumed) reads not_evaluated for the gate: it blocks or waits needs_user, never passes. It counts only when the card showed the assumed value with its unit and source. X/R follows AS11B-RVC-1: an assumed X/R passes only with the X/R-free bound. Test on the shipped template: scr passing on assumed data only → the gate does not pass on it.
- D4 (unknown and warn rows). A block row reading unknown fails closed (#1079). A warn row reading unknown reads "not evaluated", is never counted as a pass and never reads as accepted at hand-off. A test counts both on the shipped template.
- D5 (the solve budget is real). `dc_campus_study` settles from the job record's actual solve count. If the record carries no count, the step fails, or reserves and settles a stated upper bound. It never settles a fixed 1 when it ran more. Check and pin how many times N-1 sizing with invest=True solves.
- D6 (invest=True is a model write). If the campus run with invest=True changes the project network or sidecars, the card or grant lists that write. It is checked against the single-edit validators before the solve. Every solver-config or network write bumps the network revision. The PR names what the campus run writes, with file:line.
- D7 (the native-action registry stays closed). It is absent from the catalogue, every tool list, every allowlist and the provider offer, with one test each. `load()` refuses any args other than the frozen marker and any action outside the registry. A model-turn call of `dc_campus_study` is refused (as #1123 refused run_margin_loop): probe `_refusal` with the grant.
- D8 (the scorecard row table). The "every emitted row id is gated or listed as 'not gated: <reason>'" test runs on the shipped template AND on a project with sidecars (ride_through:, dynamic: rows), so a new engine row fails until it is classified. ride_through: and dynamic: never pass.
- Also: the wording is "within the screening limit". Run mutants.py on run_gates, run_actions and run_profiles, plus hand mutants of D1–D3.

### GK B (36): grid.frequency_hz in 10a, option A confirmed (2026-11-08), with conditions

- `_dc_gridcode_frequency` reads only the confirmed intake rows. It never falls back to campus_f_hz, a template default, the profile's own data or a parameter default (SH-07 B3b). Test: no intake row while the network and campus both say 60 → not_evaluated.
- With no row, the block rule is not_evaluated with "state the grid frequency (Hz)", and the run stops at the input gate. It never passes and never reads as a fail on the value. Test that state and its reason.
- 10b: the opened intake takes grid.frequency_hz as a user-given 50|60 enum, shown on the card with its unit, never tunable, and not a template creation param.

### Survey for 10a (session_01G7qCuicgzGCQ4BMEZ2uayd, master 5fd5977e)

- **Already on master (issue 05):**
  - `run_gates.fact_for` serves `dc.readiness.<row>` and `dc.readiness.<family>:` (`services/run_gates.py:526-590`) through `grid_readiness.readiness_fact` (`services/adequacy/grid_readiness.py:1305`). Excluded rows give NoEvidence; families need every row to pass; zero rows give not_evaluated; wildcards are refused in `check_rule`. Tests: `tests/test_harness_result_gate.py:45-177`.
  - `_scorecard` reads `grid_readiness_for(env.project)` with the campus default profile. D1/D2 change it to the frozen id plus a digest check.
- **Rows on the shipped `eh_datacenter`** (`grid_readiness.grid_readiness(dir, build_eh_datacenter(), "generic_assumed")`, no sidecars), 17 rows: scr pass (assumed), energization_peak:2030 fail, and 15 unknown: ramp_rate, oscillation, rapid_voltage_change, rapid_voltage_change_infrequent, harmonic_distortion, current_distortion, flicker, motor_start, voltage_unbalance, voltage_stability_pv/qv, reactive_range, frequency_support, grid_former (assumed=False), lole. A project with sidecars adds the ride_through:/dynamic: (and motor_start:<unit>) rows. Off-grid moves PV/QV to `excluded` (`grid_readiness.py:1745`).
- **Planned row table (D8):**
  - block: scr, energization_peak:, reactive_range.
  - warn: everything else (ride_through: and dynamic: never pass).
  - not gated: lole, which the loop rule judges.
- **Frequency:** `services/solver/island.campus_f_hz(project_dir)`. The grid-code schema has no frequency key (`gridspine/templates/grid_codes.py:185`).
- **Grid codes:**
  - `campus_grid_code_service.list_grid_codes(project)["published"]` and `publish` (`:884`, `:972`); `campus_electrical_service.profile_fingerprint(project, id)` (`:235`) is the D1 digest.
  - The chat tools `campus_publish_grid_code`, `campus_save/new/delete_grid_code_draft`, `campus_confirm_grid_code_limit`, `campus_extract_grid_code` and `campus_delete_published_grid_code` (`services/chat_tools.py:5053-9526`) join `run_envelope.NEVER_LISTED`.
- **PoC count:** `grid_readiness._governing_poc(n)` (`:1334`) returns (bus, count).
- **Campus run (the D5/D6 answer):**
  - `campus_electrical_service.enqueue_run` (`:1800`) queues a solve-queue job of kind `campus`; its body `run` (`:1688-1781`) reads the network under `_netcdf_lock` and runs no PyPSA optimisation (pandapower AC flows; greedy `invest_campus`; the MILP is a separate job, `start_milp_job`).
  - It writes `campus/run/` (published via `_publish_run`), `campus/run/used_library` and the cost basis when investing, and `campus/settings.json` (`:1773`). It does not write the network or the solver config.
  - It needs `campus/campus.yaml`, otherwise 422 (Q2).
  - Follow it like the queue kind (`harness/continuation.follow_queue_job`, `:279`), but with a campus-run evidence read: `run_job_state(project)` and the published run's `profile_used` sha (`:245`).
- **Analogue for frozen args, reservation, follower and recovery:** `harness/run_eh.py` (freeze `:100`, admit `:190`, release `:254`, settle/recover `:456`/`:577`); the runner's native dispatch is `harness/runner.py:798-805`.

### 10a build (session_01G7qCuiz5MQHhJ9WJuGKXjQ)

- `eh_datacenter@1` in `services/run_profiles.py`:
  - Stage 2 has `dc.gridcode.published` ("publish the grid-code profile").
  - The input gate has `dc.gridcode.frequency_match`, against the intake param `grid.frequency_hz`.
  - The solve stage runs solve_queue_enqueue, run_eh_study, `dc_campus_study` and `dc_grid_readiness`, with `dc.campus.study`.
  - The result gate has the core rules, `dc.gridcode.unchanged`, the `DC_SCORECARD` row table (`lole` listed as not gated) and the coupling-loop LOLE rule (Q5).
  - No envelope: every write and run is carded.
- The registry is `services/run_actions.py`. The runner dispatches and settles from `harness/runner.py`. The freeze happens on solve-stage entry (`assistant_tasks._advance_run`), and a stage entered again keeps the first freeze.
- D2–D4 and the never-pass families are in `services/run_gates.py` (`_frozen_scorecard`, `_scored_problem`, `NEVER_PASS`).
- What the campus run writes (D6): `services/campus_electrical_service.py` `run`. It writes `campus_electrical/run/` (via `_publish_run`, plus `used_library` and the cost basis when investing) and `campus_electrical/settings.json` (`(d / SETTINGS_FILE).write_text`). It runs no PyPSA optimisation. `test_the_campus_run_optimises_nothing_and_writes_only_its_run_and_settings` pins both.
- `grid.frequency_hz` while the chat intake serves eh_site only: the coordinator ruled option A (2026-11-08). 10a reads it only from the run's confirmed intake rows and fails closed at the input gate ("state the grid frequency (Hz)"). `run_intake.py` stays out of 10a. 10b opens the chat intake to eh_datacenter for this field, and keeps `grid.*` out of the template's creation params (`run_intake.params`). Sent to GK B (36) to confirm or redirect in its review.

### 10a done (session_01G7qCumiAjUAfL7g4XFgqek)

- PR #1283 merged with UX (14) OK and GK B (37) OK @1a40c2c0. GK B (37)'s findings F1–F4 are built (the campus study on its own Confirmation card bound to the frozen digest; `EH_DATACENTER["campus_study"]` wins over saved settings; no grant for a profile with no envelope; every frozen setting a card row).
- The follow-up PR adds the two `_dc_gridcode_frequency` tests the module mutants found missing (another param's confirmed row; a confirmed `grid.frequency_hz` row that carries `write`).
- Left for 10b: eh_datacenter_staged@1 (per-period rules; IC NPV/IRR in the result gate), `dc.campus.contingency` (Q3) and L2's DC13-SCOPE restriction, the TOOLSEL-1 count test (Q4), the chat intake for `grid.frequency_hz` (50|60 enum on the card, never tunable, not a template creation param), and step-card rendering in the frontend.
- Risk carried: no test drives the native card through the real `/confirm` route end to end.

### GK A (20): 10b plan verdict, GO WITH CONDITIONS (2026-11-10, verbatim in substance)

Q3 source confirmed: `campus_compliance_invested.csv` from the run's own campus job.
- CT-1 Column. Read `status_with_measures` from the invested CSV, never `status_as_is`. `not_rechecked`, `unknown`, `not_rated` or a missing status → NoEvidence, never pass. Test each.
- CT-2 No silent fallback. invest=True is frozen, so a missing invested CSV is NoEvidence. Fall back to `campus_compliance.csv` (its `status_as_is`) only if the job record positively says invest bought nothing; state which, test both.
- CT-3 Same run, three bindings, each tested with real files: (a) `campus_profile_used.json` profile == frozen id and sha256 == frozen fingerprint; (b) the published `campus_electrical/run/` is the one the run's own job published (an identity recorded at settle); (c) the network revision the campus job read == the revision at the result gate (the step records it; an edit after the job reads stale).
- CT-4 Rows present. Each named check (transformer_loading, pcc_voltage, campus_voltage) has its row, else NoEvidence; a missing transformer_loading row is "not applicable" only with the `NO_TRANSFORMER` detail on the voltage row.
- CT-5 D3 applies. A pass whose `source` is `assumed` or `extracted` never passes this block row. State what the shipped DC templates read, check by check, and pin it; bring it back before building if it blocks every shipped run.
- CT-6 Value. The failing list carries check, `worst_case`, `worst_period`, value and limit; the label says "intact and transformer N-1".
- ST-1 Periods. The `energization_peak:<year>` rules come from the network's own `investment_periods` at gate time, never TEMPLATE_META or NATIVE_PLANNING; an emitted period row with no rule is NoEvidence block. Test a staged project whose periods were edited.
- ST-2 LOLE per period. On staged the h/yr goal goes through `loop_goal(horizon_years(n))`, worst period, non-unit weightings in the test.
- ST-3 IC returns. `ic.returns` reads the stored case only if classify says it is current for this network; stale is NoEvidence; warn; never a hurdle.
- L2-1 NEVER_TUNABLE also gets the shunt and compensation attributes; a profile listing any is refused at load.
- L2-2 The redispatch exclusion is a test on the frozen settings and on `run_actions`' refusal, plus the grep pin.
- Mutants on run_actions too; hand mutants of CT-1, CT-3 (each binding dropped) and ST-1.
- Frequency intake: agrees with A (10c, a separate PR); GK B's call.

### 10b build notes (session_01G7qCukvjkKproA4DUNhn6F)

- CT-2: no fallback is built. A completed campus job with invest on always writes the invested table (`campus_electrical_service.run`), so the as-is table is never read and a missing invested table is NoEvidence.
- CT-5 probe (shipped templates solved, generic code published, frozen settings): on both `eh_datacenter` and `eh_datacenter_staged` the three checks read `pass` with measures, all `assumed`. The engine tags transformer_loading `assumed` by construction (`gridspine/static/campus_compliance.py:153`). Proposed to GK A: the transformer's own rating counts as an equipment limit (`run_actions.EQUIPMENT_LIMIT`); D3 holds the voltage checks, so the shipped template waits for the user's own PCC and campus bands.

### GK A (20): CT-5 amended (2026-11-10, verbatim in substance)

Option A, with conditions. transformer_loading's "assumed" is 100 % of the unit's own rating ("equipment rating (not a grid-code clause)"), not an assumed input.
- CT-5a The exemption is keyed narrowly: check == "transformer_loading" AND the clause starts with "equipment rating". A changed clause or tag fails closed to not_evaluated (a mutant that changes the clause reads not_evaluated).
- CT-5b campus_voltage passes only with the profile's own `campus_voltage` band; `assumed` → not_evaluated, needs_user "state the campus voltage band in the grid-code profile". `extracted` never passes, on pcc_voltage too.
- CT-5c Pinned both ways: the shipped template + generic code reads not_evaluated (needs_user), never block-fail or pass; a user profile with code/user-tagged PCC and campus bands can pass.
- CT-5d The pass shows its measures (#1172): a pass resting on a recommended resize carries that detail in the fact value, and the gate words read "passes with the campus study's measures: …", never a plain pass. State whether `ic.returns` includes the campus costs; if not, say the measures are not costed. Test the wording on the shipped template.
- Option B (warn) rejected: N-1 is the check DC13-SCOPE ties to a remedy, so it stays block. Option C not needed.

### GK B (41): 10b plan verdict, GO with E1–E7 (2026-11-10, verbatim in substance)

- Frequency intake: A, a separate **10c** PR after 10b, with UX. GK B (36)'s "there is no 10c" was about the chat tool; this 10c is the intake slice. Until it lands the DC run stops at the input gate with "state the grid frequency (Hz)", pinned on the staged profile too. 10c carries GK B (36)'s 2026-11-08 conditions (user-given 50|60 enum, on the card with its unit, never tunable, never a template param, no fallback).
- E1 `ic.returns` is bound to this run: the stored case counts only when its input fingerprint matches the run's network; stale or no fingerprint → NoEvidence, never the old numbers; test an edit after the case. Show the value with its currency; a warn reading NoEvidence shows "not evaluated" (D4). (Same as GK A ST-3: name the line where classify's "current" compares it, or add the check.)
- E2 = CT-3 (c): the campus job's input revision equals the network's at the gate.
- E3 Completeness: transformer_loading, pcc_voltage and campus_voltage present for the intact case and transformer N-1; missing or truncated → NoEvidence (#1079).
- E4 = CT-2: a missing invested CSV is NoEvidence; fall back only when the job record shows invest ran and by design wrote none.
- E5 Each period's `energization_peak:<year>` is gated; a missing period row fails closed; the D8 test runs on the staged template and a staged project with sidecars.
- E6 The shunt/compensation attributes join NEVER_TUNABLE with the tap attributes, each refused at load with its own test.
- E7 Mutants on run_gates, run_profiles and run_actions; hand mutants of the E1–E4 guards and the `irr_not_established` path, each with the test that kills it.

### 10b build notes, continued

- E3 finding: the engine solves a transformer N-1 case only for a group with a parallel unit (`gridspine/static/campus_flow.py` `solve_cases`). The shipped DC campus has none, so its `campus_lf_pcc.csv` holds intact cases only. 10b reads that as not_evaluated ("no transformer group has a parallel unit to lose"), never a pass.
- CT-5d: the investment case takes the campus study's purchases as extra owner assets (IC G3, `routers/results.py` "the campus study's equipment as extra owner assets"), and its report goes stale when the campus study changes. So on the staged profile a current `ic.returns` costs the measures.
