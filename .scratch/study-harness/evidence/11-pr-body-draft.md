<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SH-11 PR body draft (hand-off, SH-11 (3))

Study-harness issue 11 (`.scratch/study-harness/issues/11-profile-h2-hub.md`): harness profiles `eh_h2_hub@1` and `eh_h2_hub_staged@1`.

**Stacked on #1307** (the hydrate fix, split out by GK B (38)'s option B). Until #1307 merges, this PR's diff against master includes it, so review the delta against #1307's head (`claude/hydrate-fresh-network`).

**Acceptance 1 is blocked by `.scratch/adequacy-loops/issues/01-loops-sample-link-outages.md`** (GK A (19) SH11-L5). A run on either H2 template cannot pass LOLE yet: the hubs' outages sit on Links (grid import, fuel cell), which the loops cannot sample. The coupling loop refuses with its "nothing to sample" error, `eh.adequacy.lole` stays `block` and reads the typed `loop_cannot_sample_link_outages`, and the run stops at the result gate. No template data was changed to get around this.

## Changes proposed in this Pull Request

- **Profiles** (`services/run_profiles.py`, `harness/workflows/profiles/eh_h2_hub*/`): EH study plus coupling loop (`eh.adequacy.lole`, block); H2 offtake served in every hour (`h2.offtake.met`, block); electrolyser utilisation in the [0.40, 0.95] sizing-screen band (warn); `ic.counterfactual` (warn, never blocks). L2 envelope: wind/PV/BESS candidate caps, the H2 store, and the electrolyser. The single-year hub sizes its built electrolyser by `p_nom`; the staged hub sizes only `electrolyser_new_*` by `p_nom_max` (GK B Q3 = A, confirmed by GK B (39)). Budget 13 solves (H7).
- **Readers** (`services/run_gates.py`): offtake shed hours and fleet utilisation, per investment period on generator weightings, read from the run's own solve (F6 guards). Utilisation reuses `asset_economics.link_capacity_factor` (H3) and fails closed when dispatch leaves [0, capacity]. The per-period worst-row code is now one helper, `_worst`, used by the three per-period readers (unserved MWh, shed hours, utilisation) instead of three copies.
- **LOLE on the hubs** (GK A (19) SH11-L1..L9):
  - `NOTHING_TO_SAMPLE` is defined once in `services/adequacy/coupling.py` and used at all four refusal sites: coupling, margin and MC loop runners, and `eh_stages`. The messages are byte-equal: each starts with the constant and keeps its own tail.
  - `run_gates._loop_converged` maps only that refusal to `loop_cannot_sample_link_outages`; every other refusal keeps its own outcome and reason.
  - On a refused engine start, `harness/run_loops._gate_on_refusal` reads the whole result gate into the stage, so the stopped run shows the typed row.
- **Queued-solve settle** (`harness/continuation.py`, A1–A3): the manifest objective is compared with `_compute_run_objective` on the saved network and the saved config, so a hub with existing-capacity costs settles `completed`. No saved config, no total, or inputs changed since the solve leaves it unverified. Ticket 39 (A4) holds the experiment-path finding.
- Issue 11 `## Comments` record GK B (38)'s plan verdicts, GK A (19)'s L1–L9 and GK B (39)'s Q3-A1/A2 verbatim.

- **Review findings (mp-code-review)** addressed:
  - H1: the H2 readers type every solve they cannot read. No run solve is `not_solved`; a solve that did not complete is `solve_<status>` (an infeasible LP: `solve_failed`); results older than the inputs are `stale_solve`. `_own_solve` takes `typed=True` only for them: `kpi.unserved_mwh` still reads None.
  - The FACTS sources of `h2.offtake.shed_hours` and `h2.electrolyser.utilisation` name their real readers (`run_gates._offtake_shed_hours`, `run_gates._electrolyser_utilisation`).
  - `_loop_converged`'s detail no longer names an internal path (CODING_STANDARDS UI rule 2), and imports `NOTHING_TO_SAMPLE` lazily like its neighbours.
  - A2 (c): `test_harness_run_eh_site_v2.py::test_pilot_envelope_mode_reaches_the_hand_off` settles a real eh_site queue solve through the unpatched `_queue_run` and asserts `completed`.
  - Harness README "Who writes the run block" names only `assistant_tasks._advance_run`. `run_loops._engine_stop` already writes the run block on an engine stop; `_gate_on_refusal` extends that same exception, writing the stage's gate through `runner._update` right after `_engine_stop`. The README is left as it is (it does not list `_engine_stop` either).

### SH11-L9 (GK A)
- (a) The read after a refusal does **not** bump `stage["attempts"]`. It writes only `stage["gate"]`, or `stage["gate_error"]` when the read fails. So no attempts reader changes: not caps, not the needs_user wording, not continuation.
- (b) The readers it calls are exactly those the result gate's first pass already called through `assistant_tasks._gate_now` on the same Env, plus the loop-record reader:
  - eh_site@1/@2: `solve.status`, `fresh`, `kpi.unserved_mwh`, `eh.review.blockers`;
  - eh_datacenter@1: `solve.status`, `fresh`, `dc.gridcode.unchanged`, the `dc.readiness.*` scorecard (the stored readiness step's frozen scorecard, cached per gate);
  - the H2 hubs: `solve.status`, `fresh`, `kpi.unserved_mwh`, `h2.offtake.shed_hours`, `h2.electrolyser.utilisation`, `ic.counterfactual` (the stored case only);
  - all of them: `adequacy.loop.converged` (the task's steps only).
  Each reads stored state under the network guard; none starts a solve, job or review compute.
- (c) `test_the_gate_read_changes_nothing_but_the_stored_gate_rows` covers a 409 and a nothing-to-sample refusal on eh_site@1's shapes. With and without the read, it pins the same run/task status, stage status, reason and attempts, steps, ledger row (0 solves) and solves used.

### Tests
- `tests/test_run_profile_h2_hub.py` (new): H1–H8, A1–A3, L1–L3 and F9, Q3-A1/A2, and the H9 killers.
- `tests/test_harness_engine_loops.py`: L7 (409 / other 422 keep their own reason), the typed reason on the real message, L8 (a raising read leaves the stop as it is), L9 (c).
- The end-to-end L6 test is `test_a_run_on_each_template_stops_at_the_result_gate_on_the_loops_typed_refusal`. On both templates it drives the real runner → `start_engine` → coupling-loop route refusal → step error, with no fabricated error string.

### Mutants (H9; `scripts/coord/mutants.py`'s mutant set, run with a fast target in parallel worktrees)
- `run_gates.py`: 120. All killed except these equivalents:
  - `w = [float(v) …] → pass` ×2: a Series behaves the same.
  - `rows or None` and `not rows or isinstance(rows, NoEvidence)` variants: `rows` is None or a list; these readers' values never return NoEvidence.
  - `not links` dropped: zero capacity already gives None in `link_capacity_factor`.
  - `u is None or` dropped: the gate maps the reader's TypeError to not_evaluated, the same verdict.
  - `>`/`>=` and `<`/`<=` at exactly ±tol.
  - `bus in n.buses.index` dropped: a load on an undefined bus.
- `continuation.py`: 17. Equivalent survivors:
  - `not isinstance(objective, (int, float))` and `objective is None` dropped: a non-number or None never equals the computed total.
  - `total is None or` dropped: None never equals the objective.
  The freshness check's survivor is now killed (`inputs_changed`).
- `asset_economics.py`: 11. One equivalent: `> 1e-6` vs `>= 1e-6`.
- `run_profiles.py`: 1, killed. `run_loops.py`: 0 by the tool. Hand mutants are below.
- Hand mutants, all KILLED:
  - the mapping returns pass;
  - the mapping is removed;
  - every refusal maps (L7);
  - the H2 LOLE rule set to warn;
  - `_gate_on_refusal` dropped;
  - a read failure raises (L8);
  - the staged envelope drops a lever (Q3-A2);
  - a zero weight read as invalid;
  - an absolute dispatch tolerance.

### H10
- Every non-H2 profile (eh_site@1, eh_site@2, eh_datacenter@1) resolves byte-identical to master.
- Regression runs: REGRESSION_RESULTS
- `scripts/coord/prepush.sh`: PREPUSH_RESULT

### Size
2,188 changed lines against #1307: 464 code, 1,605 tests, 119 docs. That is over PR-SIZE-1's ~1,500 because of the test file. The code delta is small and is one reviewer area per GK (GK B harness/app; GK A for the LOLE and adequacy constant).

**Skipped / risk:**
- Acceptance 1 stays blocked by adequacy-loops 01.
- Ticket 39 (the experiment path's objective constant) and the owner's CBAM sensitivity and gas-vs-electric heat baseline are follow-ups.
- `eh_datacenter`'s missing stage `.md` files are untouched.

## Checklist

**Required:**
- [x] Changes are tested locally and behave as expected.
- [x] Code and workflow changes are documented.
- [x] A release note is added as a new file under `doc/release-notes.d/` (see its `README.md`); `doc/release_notes.md` is not edited directly.
- [x] The description is human-written and any AI-generated content is marked. (AI-generated by Claude Code.)

Session: session_01G7qCunuoVJGVRxSCrGWSvo

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01G7qCunuoVJGVRxSCrGWSvo
