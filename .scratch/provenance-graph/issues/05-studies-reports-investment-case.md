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
