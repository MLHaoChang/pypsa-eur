<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 15 — Harness profiles: grid-only templates (3bus, ieee14, belgium, ieee39)

Status: done
Type: task
Blocked by: 07
Gated: yes (GK A for the PF and N-1 rules; GK B for the harness parts)
Model tier: sonnet
Owner decisions: SH-PILOT-1
Design: [design.md](../design.md) §9.3 "Grid-only"

## What to build

1. One profile `grid_only@1` serving the four ids; `BLANK_PRESET` scope; no params.
2. Setup refuses `belgium`/`ieee39` with a reason when their source is missing (`_build.py:228, 247`).
3. Result rules: core plus PF convergence, voltage band and line loading ≤ 100 %, gated only on AC PF results the engine labels as PF, never on LOPF values (GK A, ACPF-BUG); N-1 `warn` when the solver config's option is on.
4. No tunables: L2 diagnoses and ends `needs_user`. Explore skipped by default.

## Acceptance criteria

- [x] `3bus` and `ieee14` runs reach hand-off with the LC-21 network chapters.
- [x] A seeded overload fails the loading rule and the run asks the user (no agent edit).
- [x] Without an AC PF result, the PF rules are `not_evaluated`; LOPF flows never satisfy them.

## Likely files

`pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/tests/test_run_profile_grid_only.py` (new)

## Comments

GK B (61), 2026-11-17, consolidating GK B (60)'s plan-GO conditions. The scope decision SH15-SCOPE is A: ship as is, with follow-up issue 51.

- (1) `lopf+ac_pf_*` is parsed so that an optimal LP stays optimal, and the PF outcome feeds only `grid.pf.converged`.
  - Done: `run_gates.lp_condition`.
  - Done: `calculations.solve_matches_inputs(after_pf=True)` accepts `optimal; ac_pf_failed: …` with no PF result, because the stage restores the LP frames.
- (2) `after_pf` needs both the revision and the condition, each with its own red case.
  - Done: revision-only and condition-only red cases.
  - A1: AC PF overwrites the slack's `generators_t`, so the run has no KPIs after a flow. Follow-up issue 50.
  - A2: `sub_network` is not an Input attr; the C-a pin passes with PF.
  - A3: the flow runs in place. It adds no undo entry or edit counter and leaves the branch carriers unchanged (test). Option A: no saved field other than results moves (`test_the_flow_moves_no_saved_field_but_the_results`); the PF-rewritten slack `generators_t.p` is not pinned either way (issue 50).
- (3) Test the capture check ordering. GK B (61) ruling:
  - Done: a revision bump after the flow's capture makes `after_pf` fail on the real 3bus run; the mutant that drops the revision compare in `calculations.solve_matches_inputs` is killed.
  - Done: an edit between the LP and the flow is caught by the input fingerprint, not the revision (the flow reads the edited counter). The run holds the network lock from before the LP to after `n.pf()` (`solver_service.run_simulation`'s `with lock:`; the revision is read under it at `ac_pf_service.py:363`), so that gap is closed on the run path.
- (4) The fingerprint change `''`→`'AC'` is accepted with:
  - C-a: before equals after solve, with and without PF, on 3bus, ieee14 and ieee39 (belgium skipped while unbuilt);
  - C-b (GK B (61) ruling): against master's code path (the fingerprint with `_canonical_branch_carriers` switched off):
    - (a) every built template with no `''` branch carrier, and a no-branch network, hash byte-identical to master (no shipped template has a non-empty branch carrier: the grid ones ship `''`, the others have no branch);
    - (b) a `''` carrier network, and each shipped grid template, equals master's fingerprint of the same network with `'AC'`;
    - (c) `'DC'`, `'HVDC'` and `'ac'` branch carriers, and `''` on Bus, Generator, Link and Store, hash as on master;
    - (d) the mutants "canonicalise every component's `''`" and "map any branch carrier to `'AC'`" are killed;
  - C-c: the shift is stated in the release note;
  - C-d (GK B (61) ruling): each fingerprint equality reader accepts a post-change record and reads a pre-change `''` record of a PF-on project stale, typed, never a 500 or fresh: `calculations.solve_matches_inputs` (False), `provenance_graph.runs` (`stale`, broken edge `input fingerprint`/`changed`), `retained_results.run_binding` (`inputs_changed_since_run: True`), `ic_counterfactual` via `solve_freshness` (`NOT_RUN`, `results_not_current`).
- #851's branch-overload reader stays off `after_pf`.
