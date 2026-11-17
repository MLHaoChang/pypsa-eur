<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Guided says Certified, Expert Results says not run yet

Status: needs-triage
Type: finding (keyed walkthrough audit, UX-NEXT-1)
Journey steps: J4 Run, J6 Read results (Guided and Expert)
Severity: high
Send to: Guided results legibility; next-step flow

## What happens

1. Create "Island Microgrid" from the template, open section 9, press Run study (12 s).
2. Section 10 reads "Certified: no shortfall expected, under the 3 h/yr goal." with a €3.47 M tile.
3. Press Expert. Results › Summary reads "Not run yet", "No results yet: run the model…". The header offers Run LOPF and the status bar shows "Obj: —".

Same for the data centre (€34.7 M tile in Guided). The two modes answer one question with two different runs and do not say so (EXPERT-GUIDED-1: Expert never shows less than Guided).

Shots: `shots/mg-after-run.small.png` (Guided), `shots/mg-expert.small.png` (Expert).

## Done when

After a Guided study, Expert Results shows the same verdict and cost as its headline, or says in words that the dispatch solve has not run and that the verdict came from the reliability study, with one link to run it.

## Journey fit

Touches J6. Hands off from J5 (the run ends on section 10) and to J7 (compare): the headline must be the same text in both modes.
