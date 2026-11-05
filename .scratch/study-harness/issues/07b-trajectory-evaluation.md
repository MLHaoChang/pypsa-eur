<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07b — Trajectory evaluation (HH-05B)

Status: ready-for-agent
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
