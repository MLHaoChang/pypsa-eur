<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 27 — IPP: Guided has no run path and the results hand-off is a dead end

Status: ready-for-agent
Source: decide-report-journeys.md (walk 2026-11-17, master 0ab07339)
Severity: blocks decision
Journey fit: run → results for a generation-only site.

## What

Guided sections 8 and 9 say 'Not offered for this site: Nothing to make adequate', Run study is disabled. After Expert Run LOPF (about 12 s solve) Guided Results still says 'Not run yet … Go to Goal and run the study'; Improve is disabled and Guided has no Download report. The Goal panel asks for an allowed shortfall and a price of undelivered energy for a site with no load. Total system cost shows -EUR 4.55 M (net revenue, not labelled). The Expert-only report says six times 'no ReferenceDesignReport … run the study first (Goal step)'. See 11 (no-verdict) and 14.

## Acceptance

- Guided offers an IPP run (or a clear Expert hand-off) and its results show the LOPF outcome, NPV and IRR.
- No load-only goal inputs on a generation-only site; net revenue is labelled; the report omits the reliability text.

## Triage (2026-11-20, master b7e5dee3)

Checked by code reading only; the app was not re-run. Overlaps issue 11 (TE2E-11-PLAIN-1: IPP gets 'no reliability verdict for this site type'). Still true: `flow_scope.py` still says 'Nothing to make adequate'. Question for the coordinator: what does Guided do for the generation-only IPP template? (1) Recommended: Guided shows the LOPF outcome, NPV and IRR as its result and offers a Run that does the LOPF, no load-only goal inputs, net revenue labelled, no reliability text in the report. (2) A clear Expert hand-off from Guided ('run it in Expert') and show the Expert result in Guided Results once run. (3) Both: hand-off now, Guided run as a later issue.

Decision (owner, 2026-11-20, TE2E-27-RUN-1): Guided shows the LOPF outcome, NPV and IRR as its result and offers a Run that does the LOPF; no load-only goal inputs, net revenue labelled, no reliability text in the report. Cross-reference: issue 11 (TE2E-11-PLAIN-1) covers the 'no reliability verdict' statement for IPP; this issue covers the Guided run and results. Acceptance as above. Slice if over 1,500 lines (PR-SIZE-1). Gated: GK A and UX. Blocked by: none.

## Comments

- Seen again 2026-11-21 (template walk, master 6b421b06): IPP (`eh_ipp_hybrid`): Guided section 9 now has a "Run the optimisation" button and says "The optimisation finished. Open the results ›" (about 10 s, upper bound), so the run part is fixed. Section 10 still says "Not run yet — The study writes its verdict, cost and risks here. Go to Goal and run the study", and Investment and Reliability say "Not run yet. Set a goal and run it". The result (EUR -4.55 M, "revenue exceeds cost") shows only in the status bar and Expert Results.
