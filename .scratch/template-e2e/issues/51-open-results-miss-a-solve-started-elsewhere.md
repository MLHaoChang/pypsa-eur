<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 51 — Open Results pages miss a solve started elsewhere; "No results yet" after a backend restart

Status: needs-triage
Impact: medium
Templates: DC staged (seen there; likely all)
Found by: te 18 shot run and its diagnosis, 2026-11-28 (#2140)

## What
- A solve started through the API (not from the open page) is not noticed by an open Expert Results page. Its figures stay as they were until the page remounts.
- After an API solve or a backend restart, Expert Results showed "No results yet" with no action, while the header said "Run study (1 out of date)". This was seen once in the shot run and has **not** been reproduced.

## Not this issue
The Summary's "No cost breakdown / No capacities for this solve" next to an "Out of date" pill is fixed in #2140. That PR moves the statistics query under the results root, so a solve's end refreshes it, and makes an empty part say "Out of date" while the results are out of date.

## Next
Reproduce the restart case first (diagnosing-bugs skill): restart the API with a solved project open on Expert Results, then read `/simulation/status`, the flow's results freshness and the results routes. A solve started through the API needs the page to learn of jobs it did not start, e.g. by polling the job queue.
