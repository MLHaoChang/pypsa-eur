<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Generation-only template (Renewables IPP) cannot run from Guided

Status: needs-triage
Type: finding (keyed walkthrough audit, UX-NEXT-1)
Journey steps: J3 Readiness, J4 Run, J6 Results
Severity: high
Send to: next-step flow; Guided results legibility

## What happens

"Renewables IPP with battery": section 9 says "Ready to run." but Run study is disabled, with the reason only in a tooltip and a line: "Not offered for this site: Nothing to make adequate: this site only generates and has no load." Section 10 then says "Not run yet… Go to Goal and run the study" and "Go to Goal" (reliability tile), a dead end for a site with no goal to set. Expert Run LOPF works (13.7 s, solved), yet Guided section 10 still reads "Not run yet" with only the Operation tile "Solved"; the Investment tile says "The investment case has not run".

Shots: `shots/ipp-Read.small.png`, `shots/ipp-guided-after-lopf.small.png`.

## Done when

A generation-only site has a runnable Guided path (or Guided says plainly that its run is the dispatch solve and offers it), and section 10 shows its result in the same place as the other templates.

## Journey fit

J3 to J6. The Next button on section 9 must lead to a run that ends on a result the site can have.
