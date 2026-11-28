<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Slice 6: finish line in chat and voice; the estimate in the assistant's status tools

Status: needs-info; audit 2026-11-28: the PR blockers are met; one owner question left
Type: task
Blocked by: none by code: #1550 and #1556 are merged and slices 01 (estimate route), 02, 02b and 03 are done. The clause "live-voice issue 11 owner agrees" has no owner-decisions.md row.

Spec: [../spec.md](../spec.md) §5 and §6 slice 6.

- When a run ends, chat appends one neutral line with the toast's words (no
  model turn); an active voice session says it once when idle.
- The assistant reads the run estimate (`GET /api/simulation/queue/{job_id}/estimate`,
  `services/run_estimate.py`) through `wait_for_job` and the status tools, not
  a new route tool. Until then the route's chat disposition is held by this
  issue (route parity `held-by-spec`).
- GK B 62 terms (issue 01 comments, (b)): the estimate and its basis after the
  verified-outcome block (#927 ordering); unknown stated as unknown, never 0 or
  blank; the ETA never changes poll intervals, timeouts or job state; the
  spoken line bounded like the other voice echoes; tool-schema, inventory and
  route-parity checks run. Its own plan to GK B.

## Audit (2026-11-28)

- 2026-11-28 audit: `services/run_estimate.py` and `GET /api/simulation/queue/{job_id}/estimate` are on master; no chat line, voice line or `wait_for_job` estimate exists yet. Question for the owner: may this slice start while live-voice 11's residual (queue/GridSpine families, D-I2, D-C1) is open? (1) Yes, it touches only the finish line and the status tools (Recommended); (2) wait for live-voice 11 to close. On option 1 set the Status to `ready-for-agent`, model opus, plan to GK B first (the GK B 62 terms above).
