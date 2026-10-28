<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Harness run skeleton on eh_site: task v3, profile schema, typed gates

Status: ready-for-agent
Type: task
Blocked by: none
Gated: yes (GK B reviews the plan and the PR)
Model tier: opus
Owner decisions: SH-1, SH-PILOT-1, SH-FOLD-1, AI-567b, COP-1, TOOLSEL-1
Absorbs: LC-23 harness core (`.scratch/live-copilot/issues/23-study-copilot.md`; slices 1–2 merged, its brief and checkpoint stay)
Design: [design.md](../design.md) §3, §4, §5, §6.0, §6.6 (explicit context), §9.1, §9.2
Plan verdict: GK B GO, 28 Oct; B1–B4 settled in the design

## What to build

The tracer bullet: one eh_site harness run goes intake → setup → inputs → input gate → solve → result gate (solver status only) → hand-off, in **step-by-step mode** (COP-1 unchanged, every write and run carded), driven by the chat turn as today. No runner, no envelope, no loops yet.

1. `start_task(..., run={"profile", "mode"})`: version-3 task file with the `run` block (design §4). Versions 1 and 2 still read. No new catalogue tool.
2. `services/run_profiles.py`: the Profile schema (§9.1), the generic defaults (§9.2) and `eh_site@1` with stages 0–5 and 8. Card scope is read from `flow_scope.preset_for`, not copied.
3. `services/run_gates.py`: rule evaluation and the fact registry with the core input facts (`validate.*`, `readiness.*`, `diagnose.*`) and `solve.status`, `fresh`. Fail closed (`not_evaluated` = fail for `block`).
4. Stage steps materialised on stage entry (`<stage>.<n>`, ≤24 per stage). A stage is left only when its gate passes. The gate result is written by harness code only.
5. `get_task` returns the stage view (stage, gate results, next stage).

## Acceptance criteria

- [ ] An eh_site run in step-by-step mode reaches hand-off on the test fixture with every write and run carded (COP-1), and `get_task` shows each stage `passed` with its gate facts.
- [ ] A network with a validation error stops at stage 3 with `in.validate.errors` failed and its facts; no solve is enqueued.
- [ ] A fact reader that raises yields `not_evaluated`, and the `block` rule counts as failed.
- [ ] `resolve_task_step(completed, result=…)` cannot pass a gate (SC-12 extended to gates).
- [ ] A version-1/2 task file reads unchanged; LC-23 tests SC-01, SC-05–SC-07, SC-11, SC-12 stay green.
- [ ] Step-limit check: a stage with 25 steps is `invalid_task`; a run with 3 stages × 10 steps is accepted.
- [ ] The assistant inventory check passes with no new catalogue tool.
- [ ] B4: the fact registry is closed and versioned; a profile naming an unknown fact is refused at load (test); a fact reading `None` or NaN fails its rule.
- [ ] §6.0: a profile listing a never-tunable attr (a grid-code limit, `target_lole_h`, IT load) as `tunable` is refused at load (test).
- [ ] Each rule names the engine row id or function it reads; renaming a fixture row fails a test.
- [ ] Bound-context sweep (design §6.6 table): each tool in the eh_site stage subsets is shown, by a test, to resolve the project through the bound context; a tool that fails stays out of the subset.

## Likely files

`pypsa-gui/backend/services/assistant_tasks.py`, `pypsa-gui/backend/services/run_profiles.py` (new), `pypsa-gui/backend/services/run_gates.py` (new), `pypsa-gui/backend/harness/catalogue.py` (`start_task` schema only), `pypsa-gui/backend/tests/test_harness_run_skeleton.py` (new), `pypsa-gui/backend/harness/README.md` (a "Harness runs" section)

## Not in scope

The runner (02), envelope (03), loops (04, 05), LC-20 rules (05), report (07), UI (08).

## Comments
