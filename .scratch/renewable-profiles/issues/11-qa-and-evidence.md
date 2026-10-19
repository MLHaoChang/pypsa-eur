<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-11 — QA gate, live probe, docs

Status: ready-for-agent
Type: task
Baseline: master `5d5bf26b83aa18c9a2a0a3418d3beeecccc8d086`
Blocked by: RP-01…RP-10
Requirements: all in [the spec](../spec.md)

Hand-off item E4. Run the full backend `profile_gen` and harness suites and the full frontend suite. Run Playwright journeys with screenshots under `.scratch/renewable-profiles/evidence/`. Run a Tier B live probe (manual and capped) of OJ-47–OJ-54. Update `CONTEXT.md` (terms: readiness, shaping op, compare) and add a release-note fragment.
ERA5 is never exercised in CI. A manual ERA5 run is recorded without credentials in any log.
