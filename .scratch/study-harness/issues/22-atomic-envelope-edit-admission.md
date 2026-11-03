<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 22 — An envelope edit checks its expected input identity and writes inside one project lock

Status: needs-triage (waits for the LC-40b code PR)
Type: task
Blocked by: LC-40b code (`.scratch/live-copilot/issues/40b-revision-capture-helper.md`, session `session_01G7qCujWvzctR1i8DZ6y3a6`, in progress); 03 (landed)
Gated: yes (GK B plans it and reviews the PR; harness engine code)
Model tier: opus
Owner decisions: HARNESS-970-1, SH-FOLD-1, SH-ENVELOPE-1, SH-PILOT-1
Folded from: draft PR #970, HH-01 Phase A (`.scratch/harness-reliability-upgrade/issues/01-atomic-mutation-admission.md`) and HH-05 case E02
Design: [design.md](../design.md) §7 (the approval envelope), `pypsa-gui/backend/harness/README.md` "The approval envelope"

## Why

The README's "Known limit" (`harness/README.md`, the paragraph beginning "Known limit", ~L1320) still stands on master `a56cf0b06`: the runner reads `run_envelope.chain_head` under its locks, releases them at `tool_running`, and the handler then takes its own locks. A user edit that lands in that gap is taken into the freshness chain by `run_envelope.settle` (`runner.py:807`, a fresh fingerprint after the step), so the next unattended step runs on an input the user changed and the envelope never approved. Issue 03 shipped the envelope; this closes its one documented hole. #970 described it as a "new" P0; it is the same limit, not a newly reproduced exploit.

## What to build

1. **Compare and write in one lock.** For an envelope-admitted effectful step, take the project's existing mutation lock on the worker thread that runs the handler (then the solver-state lock, in today's order), compare the expected input identity (the chain's last link) with the current one, run the handler and capture the after-identity, all before releasing. A mismatch refuses before any write: the run pauses `envelope_drift` and the step waits for its card.
2. **Settle from the captured receipt.** `run_envelope.settle` takes the after-identity captured inside that interval, never a later ambient fingerprint. Remove the blanket `run_envelope.settle` call in `Runner.dispatch`'s `finally` for the covered steps. A user edit landing after the lock is released is drift on the next step.
3. **Reuse LC-40b, add no second capture helper.** Revision capture and hold go through `dirty_state.capture` / `dirty_state.hold` as LC-40b specifies (`record`, `ctx`, `lock=`). This issue adds a compare-and-write wrapper over them, nothing parallel. If LC-40b's signature changes, follow it.
4. **Cover eh_site@1's real edit shapes.** `run_profiles.py:186-206` lists the shapes: `update_component` on `Generator.p_nom_max`, `StorageUnit.p_nom_max`, `StorageUnit.max_hours`, and the `solve_queue_enqueue` run. #970's slice (`Generator` with `p_nom` / `marginal_cost`) is not eh_site's; do not build that. The solve enqueue is covered only to the extent of "the identity compared is the identity the job snapshot takes" (immutable snapshot admission stays in the `## Folded from #970` section of issue 05).
5. **Do not narrow the envelope.** SH-ENVELOPE-1 stays as granted: no Generator-only scope, no profile removed, no new grant after a restart. An edit shape the wrapper cannot cover yet keeps today's behaviour (still inside the envelope, with the known limit documented for that shape); it is never refused merely because #970's Phase A listed fewer shapes.
6. Update the README: delete "Known limit" for the covered shapes and name what remains.

## Acceptance criteria

- [ ] **Failing test first (HH-05 E02 / HH-01 A01, the barrier test).** Pause the real dispatch after `tool_running` releases the runner's locks and before the handler takes the mutation lock. A second correctly bound writer changes another input (say a `StorageUnit.p_nom_max` the envelope did not touch). Release the handler. Expect: the external edit is still in the network, the run pauses `envelope_drift` with no envelope write, and the chain head is unchanged. On master this test fails (the edit joins the chain).
- [ ] Same barrier with no external edit, for each of the three eh_site edit shapes: one native write, the expected value read back, one chain link, no card wait.
- [ ] Barrier after the write and the lock release, before `finish_step` / the runner's `finally`: an external edit to an unrelated Load is not in the stored chain link; the next unattended step pauses `envelope_drift` (HH-01 A04).
- [ ] A solver-config write through `routers.simulation.update_solver_config` cannot interleave inside the compare, write and capture (the fingerprint includes solver config); the same write before pickup refuses (HH-01 A18).
- [ ] Edit then undo (same input, bumped revision) refuses on the revision; the `exact=True` fingerprint catches a sub-precision input change (HH-01 A08).
- [ ] A write on another project's lock completes while this one is held (no process-global lock; HH-01 A05).
- [ ] ACL revoked, expiry, budget or grant change between preflight and pickup refuses with no write (HH-01 A09).
- [ ] The envelope profile and its grant for eh_site@1 are byte-identical before and after (a test pins the SH-ENVELOPE-1 envelope: nothing narrowed).
- [ ] `test_harness_envelope.py` and `test_harness_runner.py` pass unchanged apart from the new cases.

## Likely files

`pypsa-gui/backend/services/run_envelope.py`, `pypsa-gui/backend/harness/runner.py`, `pypsa-gui/backend/services/dirty_state.py` (only LC-40b's helpers, used), `pypsa-gui/backend/harness/README.md`, `pypsa-gui/backend/tests/test_harness_envelope.py`, `pypsa-gui/backend/tests/test_harness_runner.py`

## Not in scope

Phase B of #970's HH-01 (async snapshot admission) is issue 05's HH-10 section. Requirements (23), budgets (24). A different template's edit shapes: each profile adds its own to this wrapper's table.

## Corrections to the #970 text

#970 is pinned at `60d8332d6` / #944 and predates LC-40 (#1001: a solver-config write bumps `network_revision`), LC-40b (the capture helper), AS-06/AS-10 and LV-09; do not copy its source map. Its "supported shape" table, its new `execute_mutation` descriptor and its `InputVersion` type are proposals, not code: reuse `workflow_tools._network_input_fingerprint(exact=True)` and LC-40b before adding a type.

## Comments
