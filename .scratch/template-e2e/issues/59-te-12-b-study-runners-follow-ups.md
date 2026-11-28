<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Study runner follow-ups from te 12(b)

Status: needs-triage
Type: task
Review: GK A
Source: te 12(b) hand-off (#2091, #2102); te 12 session is live, so this is a new file (filed 2026-11-28 from the coordinator's follow-up lists)

## What is left

- A private solving context for the EH study and its front stage (own PR). A bare `ProjectContext(network=copy)` broke the `solve_target_context` readers.
- The other study runners (sizing, margin, frontier, coupling loops, mc, fmea sweep) are still under the shared mutation lock.
- UX: Guided Abort has no "Aborting…" state; a project switch or delete during a study gets 409 (`abortRunningSim` keys on `lock_held`).

## Comments
