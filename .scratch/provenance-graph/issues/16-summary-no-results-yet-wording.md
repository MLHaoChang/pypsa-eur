# Summary says "No results yet" for a run that is out of date

Status: needs-triage
Model tier: sonnet.
Size: S.
Blocked by: 13 (PR #1960).
Gated: UX.
Likely files:
- `pypsa-gui/frontend/src/pages/results/SummaryTab.tsx`
Type: bug

After issue 13 the results readers withhold a stale run's figures and the Results tabs say "Out of date, re-run". UX review (29) found that the Summary tab's empty state still reads "No results yet" for a run that exists but is out of date. The wording should follow `ResultsOutOfDate` (per-project flag) wherever the Summary shows an empty state.

Done when: Summary shows the out-of-date wording while the flag stands and "No results yet" only when there was never a run; a vitest covers both.
