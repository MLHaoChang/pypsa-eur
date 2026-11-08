<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07b — Trajectory evaluation (HH-05B)

Status: done (#1282)
Type: task
Blocked by: 07
Gated: yes (GK B)
Model tier: opus
Owner decisions: HARNESS-970-1
Folded from: #970 HH-05B, through issue 07's "Folded from #970" item 1 (coordinator decision under HARNESS-970-1: filed as its own issue)

## What to build

Issue 07's pilot acceptance run is one trajectory. Turn it, and later each
profile's run, into a reproducible suite:

- a closed case list with fixed fixtures;
- an independent native oracle per case;
- positive and refusal controls;
- a report that counts unauthorized effects, wrong-project results and false
  native completion as release blockers. Refusing everything must fail.

Build it as `harness/trajectory_evaluation.py` plus an offline CLI under
`tools/`, with the fake provider. No paid provider runs and no wall-clock
thresholds in CI.

HH-05A (#970's baseline characterization pack) is skipped; only its barrier
test lives on, as issue 22's failing test.

## Acceptance criteria

- [ ] The pilot run of issue 07 is the suite's first case, with its native oracle.
- [ ] Issue 22's barrier cases, issue 23's survive-a-restart case and issue 24's kill-between-call-and-charge case are cases.
- [ ] Each case has a positive and a refusal control; a run that refuses everything fails the suite.
- [ ] The report counts unauthorized effects, wrong-project results and false native completion, and any of them is a release blocker.
- [ ] The CLI runs offline with the fake provider; CI has no wall-clock threshold.

## Likely files

`pypsa-gui/backend/harness/trajectory_evaluation.py` (new), `tools/` (one CLI), `pypsa-gui/backend/tests/test_trajectory_evaluation.py` (new)

## Comments

### GK B (36): plan verdict, GO WITH CHANGES (P1–P7, verbatim in substance)

Answers: Q1 = A (the CLI's hang guard is off by default; `--timeout` opts in). Q2 = A (the pilot's refusal control is the drifted intake digest; a grant that lists run_eh_study can be a later case pair). Q3 = A (one PR).

- P1 (effects come from native state). Each driver snapshots native state before and after the case: the saved network file's hash and revision, the envelope chain, the run records and ledger, the report files, the project list. Every diffed change is an effect, matched to an authority (card id or envelope id) by reading the native chain or ledger, never the harness's return value. An unmatched change counts in unauthorized_effects.
- P2 (wrong-project reads are observable). Record every project id the authority / project-resolution seam resolves during the case (a recording patch in the driver). A read or write of a project that is not the bound one counts. For the decoy project, also diff its runs, reports and ledger. Test: a driver that reads the decoy counts 1.
- P3 (refusals for the right reason). A refusal control passes only when its native kind equals the case's expected kind (intake_stale, envelope_drift, the max_hours bound refusal, needs_user); a wrong kind is `wrong_refusal`, with a test and a mutant. A positive control passes only when the oracle passes too; a "completed" the oracle denies is false_native_completion.
- P4 (independent oracles). The pilot oracle computes unserved MWh by its own route (demand minus served dispatch from n.loads_t and the shedding units, × snapshot_weightings) and imports no harness, gate or metrics function the run's gate uses. Pin its helper with a non-unit snapshot_weightings test.
- P5 (kill_charge oracle). Asserts the hold counts at its bound and settled + open holds ≤ cap, from run.budgets on disk, and that the over-cap send left no new settled charge.
- P6 (fixed denominator). report() reads the case list from CASES and checks MANIFEST_SHA256; renaming a case id changes the sha (test). A driver that raises counts as that case failed, never skipped. No xfail or skip in the module.
- P7 (mutants). Also: wrong_refusal; the decoy read count (P2); the effect-to-authority match (an effect matched to any card counts as authorized). Each killed by a named test.
