<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-41b — The coupling-loop panel shows no stale state

Status: done
Type: bug

Follow-up to LC-39/LC-41 (owner RESUME-1). `GET /api/results/coupling_loop` carries no
`edited_since_study` and `LoopPanel.tsx` shows no banner, so it keeps pre-edit numbers unflagged.

## Acceptance

- [x] The coupling-loop runner captures the network revision as `margin_loop_runner.py` does (LC40-COUNTER-1; its own `restore="final"` hold re-captures).
- [x] The route returns `edited_since_study` (true after an edit, null while running or without a captured revision).
- [x] `LoopPanel` shows `EditedSinceStudyBanner` (`loop-edited`).
- [x] `LC-D13.stale_source_panels` lists the panel.

Out of scope: a shared revision-capture helper (LC-40 skipped it).
