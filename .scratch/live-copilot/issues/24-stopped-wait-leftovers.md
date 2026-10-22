<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-24 — after Stop, a waiting turn leaves a stuck row and a false promise

Status: needs-triage
Type: task
Baseline: master `06fa29b422c456e39b5e44c0312275fc4c51345e`
Found by: live OJ-08 drive in the browser, 2026-10-22 00:48 CEST (hosted model, KEY-1)
Related: [LC-12](12-job-completion-continuation.md), #542, OJ-08

## Problem

In OJ-08 the user asked "Set L1 and L2 s_nom to 500 and relaunch the
simulation, then show me the load flow." The model started the solve and then
called `wait_for_job` inside the same turn. That locks the composer, so the
user pressed **Stop** to ask "What's happening?". Two things were then wrong:

1. **Stuck row.** The transcript row "Working: use wait for job…" stays in the
   Working state for the rest of the chat. The turn is over and the call was
   abandoned, but the row never settles.
2. **False promise.** The answer to "What's happening?" (from
   `get_simulation_status`, correctly: still running) ended with "I'll keep
   checking and let you know as soon as it's done". After a user Stop nothing
   continues (#542: never after a user abort), and that turn had no plan, so
   no continuation was armed. The words promise a follow-up the app will not
   deliver.

## Expected

- A tool row whose turn was stopped shows a settled state (for example the
  existing "the turn was stopped" wording), never Working.
- The words never promise a later update unless a continuation is armed for
  that job in this conversation. With none armed, they say how to check (ask
  again, or the run status), not that the assistant will report back.

## Not in scope

Finding 3 of the same drive (the composer is locked while the model sits in
`wait_for_job`) and the missing plan and continuation (findings 1–2) are
handled in the study copilot slice 2 plan (issue 23).

## Comments
