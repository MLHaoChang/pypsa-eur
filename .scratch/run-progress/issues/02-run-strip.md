<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Slice 2a: run strip (status line, ETA, read-only line)

Status: done

Done in #1627 (44387a69).
Type: task
Blocked by: 01 (stacked on PR #1614)

Spec: [../spec.md](../spec.md) §1, §2, §4; UX plan GO conditions (a), 3, 4, 8, 10.

## What

- `utils/runStatus.ts`: one wording model for every run of the project on
  screen (queue job or Guided goal study): plain run names, stage, steps,
  elapsed, ETA words ("about 4 min left", "under a minute left", "longer than
  usual (usually about 6 min)", "first runs: no estimate yet") and their basis.
- `hooks/useProjectRun.ts`, `hooks/useRunEstimate.ts`, `api/runEstimate.ts`.
- `components/runStatus/RunStrip.tsx` at the top of the tab (where the
  read-only banner sits): the status line plus, while the project is being
  solved, "Being solved: edits open again when the run ends." (RUN-READONLY-1).
  `LockBanner` yields for the solving reason, so it is one line, not two.
- Expert adds a "Log" link (opens the bottom panel's Log tab), dropped with the
  step count below 640 px. Blocked controls refer to the strip in the same
  words (`SOLVING_MUTATION_MESSAGE`). The strip keeps an empty `role="status"`
  region mounted so the first stage is announced; the clock is not.
- Header pill stays compact ("Running…", "Queued #2"); it now says "Running…"
  for the Guided goal study (walkthrough J4/J5: it read "Idle").

## Comments

2026-11-17: Header first, per UX (a). Shots at 1280/1024: in Expert the ETA was
cut at 1280 and the header wrapped at 1024, so per (a) the line moved to a strip
(not both). The strip shows every part at 1024 and 1280 in both modes; parts
drop below 768 px (ETA) and 640 px (steps). The Expert header's second row at
1024 px predates this (it wraps with the Abort button).

Slice 2b (next): the Runs indicator with its Guided popover (UX 1, 2), the
lasting "Open results" link after a solve (audit #25).
