<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10 — Harness profile: eh_datacenter and eh_datacenter_staged

Status: ready-for-agent
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
- Open: where a DC run gets `grid.frequency_hz` while the chat intake serves eh_site only. Asked of the coordinator; built as option A (fail closed until an intake row exists).
