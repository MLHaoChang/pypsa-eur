<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Job holds across sessions, tabs, replicas and restarts

Status: needs-triage
Type: task
Found by: AA-04 S5 (`04-action-and-job-outcomes.md`); GK B (30) N1.

## Problem

Issue 04 S5 holds a job call whose outcome is unknown on the in-memory
`ChatSession` (`harness/job_holds.py`). A new chat, another tab, a second
replica or a restart has no hold, so it can start the same operation again
(#1070: fail closed needs a cross-process record). Only `solve_queue_enqueue`
reads a persisted record (the solve-queue store's queued and running rows for
the project). Every other job tool (the `execution` and
`execution_long_running` tiers) has none: the solve thread is per process, and
studies, sweeps, reports and profile generation keep no store row keyed by
project and arguments.

## Wanted

A persisted record of an uncertain job call (project, tool, G3 fingerprint,
job id when known, opened at), written when the hold opens and cleared on the
same evidence, which P2 reads when the session has no hold; or, per tool, an
existing store row that identifies the operation. Test the new-session case
for each tool family.
