<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Slice 6: finish line in chat and voice; the estimate in the assistant's status tools

Status: ready-for-agent
Type: task
Blocked by: PRs #1550 and #1556 merged; live-voice issue 11 owner agrees

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
