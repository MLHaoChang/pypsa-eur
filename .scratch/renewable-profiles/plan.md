<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Renewable profiles: order of work

Status: ready-for-agent once RP-Q1 is answered
Spec: [spec.md](spec.md). Tickets: [issues/](issues/).

## Step 0: land the existing stack (RP-Q1)

The stack #362 → #398 never ran CI below #362, because CI runs only on PRs based on master.
#362 is red on `test_hourly_assumption_audit` until #371's fix. The recommended landing
is in three units, each retargeted to master and green on its own head:

1. Phase 1: #380 (carries #362–#380). Port nothing, since #371's audit fix is
   inside. Merge master for the `catalogue.py` / `chat_tools.py` / `App.tsx`
   overlap. Gatekeeper: brief review of `assign_profile` (#371).
2. Phase C: #396 (carries #390, #393, #395), then #394 on top.
3. Phase E: #398 plus RP-01 (the 0/0 coordinates fix) in the same unit, so
   the silent wrong-site path never reaches master.

Each unit gets `scripts/coord/premerge.sh <pr>`, then "premerge ok <sha>" goes to the coordinator.

## Step 1 onwards: bounded PRs, tests first

| Order | Ticket | Why here |
| --- | --- | --- |
| 1 | RP-01 coordinates refusal | correctness bug; lands with Phase E |
| 2 | RP-02 readiness feed | every UI and chat item reads it |
| 3 | RP-09 batch apply | "Generate defaults for all" needs one undo step |
| 4 | RP-04 Time Series Manager integration | the owner's home for the feature (V1–V3) |
| 5 | RP-03 horizon from snapshots | after RP-Q3; gatekeeper plan first |
| 6 | RP-05 tuning panel | V6 |
| 7 | RP-07 compare | needed by the calibration loop |
| 8 | RP-06 shaping ops | V7 calibration engine; gatekeeper plan first |
| 9 | RP-08 chat default-first + tools | after the live-copilot LC-03/04/05 window for `catalogue.py` |
| 10 | RP-10 Guided offer | after RP-Q2 |
| 11 | RP-11 QA, live probe, docs | closes the package |

## Rules for every PR

- One claude/* branch per ticket, based on master, with a release-note fragment.
- The RED test first (tdd skill); mp-code-review before reporting.
- The synthetic source only in CI. ERA5 runs are manual, and credentials never appear in logs or files.
- UI journeys carry Playwright screenshots (Chromium at /opt/pw-browsers).
- Harness files (`catalogue.py`, `chat_tools.py`) get append-only edits, sequenced with
  the live-copilot build session (LC-03/04/05).
