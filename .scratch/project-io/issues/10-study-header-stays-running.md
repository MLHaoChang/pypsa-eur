<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10 After a Study run completes, the header still shows Abort and "Running · Solving…"

Status: needs-triage
Impact: medium
Blocked by: none
Source: follow-up from 08 (shot set 2, `.scratch/project-io/shots-08/06-completed-downloaded.png`).

## Problem

E2E run 2 of 08 (Study 336 h, k = 2, Expert): the queue job finished at 07:39:39 UTC (`/api/simulation/queue` reports `completed`). The panel showed every stage done. At 07:39:49, 10 s later, the header still showed the amber Abort and the "Running…" pill, and the footer said "Running · Solving… 5:53" and was still counting.

During the 6 min run, the backend log shows 117 `GET /api/simulation/log_stream` requests, about one every 3 s. The stream keeps closing and reopening, and the reopening went on after completion. `AppHeader.tsx` sets the run status back to idle only in the stream's `done` handler (`openLogStream`), so the stuck state matches a `done` that never arrives on a gridspine job. The previous session saw the same thing for ≥2 s once.

Not changed by 08: its header edit only hides Run LOPF on a Study (`(isStudy || kindPending) && !amber`).

## Acceptance criteria

- [ ] Within one queue poll of the job's completion, the header leaves Abort/Running and the footer stops counting, on a Study run.
- [ ] The log stream opens once per job, or the reconnect loop is explained and bounded.
- [ ] E2E: Study 336 h, k = 2; a shot taken 5 s after completion shows an idle header.

## Likely files

`pypsa-gui/frontend/src/layout/AppHeader.tsx` (`openLogStream`, `jobRunning || isRunning`), `pypsa-gui/frontend/src/api/simulation.ts` (`createLogStream`), the backend `log_stream` route and the gridspine job's log queue.
