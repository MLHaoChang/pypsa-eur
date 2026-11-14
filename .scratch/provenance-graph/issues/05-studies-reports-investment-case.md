# Studies, reports and the investment case on the graph

Status: ready-for-agent (blocked by 04; Opus; GK B, UX for UI parts)
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: opus.
Gated: GK B; UI parts need the UX reviewer's OK.
Blocked by: 04.

## What to build

Study forks and investment-case reports as `run` nodes. Move the checks in the spec survey's "Studies, reports and
the investment case" table onto `staleness`: `study/report.stale_reasons`, `routers/studies._report_stale`,
`finance/investment_case_runner.staleness`, `routers/results._ic_campus_digest`, `participants._stale_hub`. The
client stops OR-ing three `stale` flags (`financeModel.studyStale`, `hubDesign/chapters.ts`, `ResultsCard.tsx`) and
stops parsing reason text with a regex (`ReportStep.tsx:26`): the verdict says which edge broke.

## Acceptance

- Each report and the investment case show one verdict, with the edges that broke as reasons.
- One PR within PR-SIZE-1, or the slices the plan GO names.

## Comments

### 2026-11-11: triage

Checked on master: `provenance_graph` has no study or report `run` nodes yet. Verdict: ready-for-agent, waiting for 04.

- Blocked by: 04.
- Likely files: `study/report.py`, `routers/studies.py`, `finance/investment_case_runner.py`, `routers/results.py`, `participants.py`; client `financeModel`, `hubDesign/chapters.ts`, `ResultsCard.tsx`, `ReportStep.tsx`. Reviewers: GK B plan first, UX for the client part.

The acceptance in the body stands. Add: [ ] `ReportStep.tsx` no longer parses reason text with a regex; a test feeds it a verdict with edges.

### From issue 08 (GK B (45), 2026-11-11)

A cost node built here that reads a tariff or PPA cell gets a `derived_from` edge to that cell's node,
`input:commercial/<target>:<entity>/<path>` (`provenance_graph.commercial.cell_id`), so the cost reads the cell's
verdict. Until then a cost whose commercial input has no such edge lists it as `uncompared`, never fresh (ruling (b)
in issue 01).

### 2026-11-14: GK B (56) plan GO; sliced 05a (backend, GK B) and 05b (client, UX)

Q1: `participants._stale_hub` is in scope for 05a. Q2: `needs_run` goes on the wire.

- V1 (one answer): on every report, study and IC fixture, and on each per-code case, `verdict.state == "stale"` exactly when the existing `stale` bool is True, pinned with a test. A row that moves (fresh today, stale on the graph) is listed in the PR body, `stale` moves with it, and a test pins the change. Nothing moves toward fresh.
- V2 (compared nothing): a verdict whose pins compared nothing (no recorded hash, the campus key, an unreadable current value) never reads fresh (#1170). `_ic_campus_digest` does not count as a compared pin.
- V3 (exact pins): the `stale_reasons` order, the IC staleness dict minus `verdict`, and the `_stale_hub` bool are compared with `==` over the existing fixtures. Each pin test goes red under a mutant that drops or reorders one mapped code.
- V4 (each pin killed): ledger, intake, each fork's `network.nc`, the engine digest, the study_record reasons, every IC assumption part, `group_links` and `fixed_shares` each have their own killing case. That one input breaks exactly that edge.
- V5 (needs_run): True exactly on edges into a run node, one test per edge kind. The client regex goes only in 05b, after 05a's field is on master.
- V6 (reads): a raising current-hash read is stale with its reason, never a 500 (#1390 F2). A recorded None never matches a computed None.
