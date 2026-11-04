# Issue 09 evidence: the research step in the run monitor

Made with `pypsa-gui/frontend/scripts/run-monitor-shots.mjs` against the real API
and Vite, signed in as the dev admin; only `/api/chat/runs/*` and
`/api/chat/activity` are route-mocked from `runFixtures.ts` (the #1048 pattern).
Each state in Guided and Expert:

- `*-research_confirm.png`: a sourced value beside its page link and verbatim
  quote, "1 other source for this value", and what "Use this value" attaches it to.
- `*-research_unverified.png`: a waiting row the server could not verify:
  "Unverified source: not used", no link or quote, Reject only.
- `*-research_cap.png`: the stop line at the research cost cap, and the
  budgets line with "Web research €2.00 of €2.00".
