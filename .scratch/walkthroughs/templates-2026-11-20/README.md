<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Walkthrough: three templates, 2026-11-20

Owner decision WALK-CADENCE-1 (daily template rotation). Templates: `eh_datacenter` (Data Center Energy Hub), `eh_h2_hub` (Industrial Hydrogen Hub), `eh_microgrid` (Island Microgrid). Judged against `.scratch/project-workflow/design.md`, `.scratch/template-e2e/` and the earlier walkthroughs.

How: master `1f6905e3`, API and Vite started from the pixi env (the boot script had not run), dev admin, Playwright at 1440×900, light theme. Each template was created through Projects › New project › From template, then walked in Guided (sections 1–10, Run, Results, investment case) and in Expert (panels, Run LOPF, Results tabs, Reports, export). Shots are downscaled to 800 px in `shots/`.

**Timing caveat.** My scripts polled every 5–10 s and waited a fixed 8 s after opening a project, so each time below is an upper bound within that granularity, not a measurement.

## Per template

| Step | Data Center | Hydrogen Hub | Island Microgrid |
|---|---|---|---|
| Create from template | setup form (IT load 36 MW, PUE 1.3, backup mode, architecture); toast, a few seconds | no form; toast, a few seconds | no form; toast, a few seconds |
| Open project (Guided, tour shown) | ≤ 8.5 s | ≤ 8.5 s | ≤ 8.5 s |
| Guided Run (Readiness and run › Run study) | ≤ 30 s | ≤ 30 s | ≤ 20 s |
| Guided verdict | Certified, 0.5 h/yr under the 3 h/yr goal; cost EUR 34.7 M | no goal set, so not certified; cost EUR 7.61 M | Certified, 0.0 h/yr; cost EUR 3.47 M |
| Switch to Expert | ≤ 5.4 s | same | same |
| Expert Run LOPF | ≤ 9 s; Summary EUR 34.7 M (agrees) | ≤ 9 s; Summary EUR 9.02 M (**Guided 7.61**) | ≤ 9 s; Summary EUR 3.47 M (agrees) |
| Evidence report (Expert › Reports) | 3 s; Export .docx and .pdf offered | same | same |
| Investment case | not runnable (needs "site connection", which is set) | runs (long; I did not time it, under 5 min); partial result | not walked (island) |

## What worked

- All three templates create, open, run and certify (or say why they did not) in under half a minute in Guided.
- Cost agrees between Guided, Expert Summary and the report for DC (34.7 M, report 34,700,616 €) and microgrid (3.47 M, 3,473,806 €).
- The verdict wording on H2 now names the right place: "section 8, Scenarios & optimisation". The earlier "step 3" mistake is fixed.
- A "Sized design" table now shows on all three results pages, with the built size and the allowed range (microgrid battery 7.52 MW · 30.08 MWh (4 h), which checks: 7.52 × 4 = 30.08). That confirms `asset-investment-2026-11-17` issue 03, which is already `done (#1677, #1734)`.
- The microgrid verdict adds a design option ("A 24 h battery also meets the cap at EUR 3.1 m a year (design: 4 h, EUR 3.5 m)").
- The report builds in about 3 s and states what a study did not establish.

## Findings, by severity

New (files in `issues/`):

| # | Finding | Impact |
|---|---|---|
| 02 | Report disclosures use engine words (`tea`, `COPT`, `single_period`, `VoLL`, "LP proxy") and a hydrogen note in non-hydrogen projects | medium |
| 03 | Grid-readiness counts differ between the Guided step and the report (13 vs 12 unknown on DC, 7 vs 6 on the microgrid) | medium |
| 07 | Guided Results LCOE card says "solve the project in Readiness and run" right after a run | medium |
| 06 | H2 investment case: raw `48,665,787.09` without unit; NPV and post-tax returns "not established" after a normal run | medium |
| 01 | "Next: Goal" names a step the stepper does not have and skips sections 4–7 | low |
| 04 | Results chart: axis end "3.6 h/yr" sits beside "Goal" (goal is 3) | low |
| 05 | Microgrid "Compare supply architectures" ends in "Needs local mode" | low |

Seen again, added to the existing issue (no duplicate):

- `template-e2e` 01: the template opens with "no location: no bus is placed" on all three.
- `template-e2e` 06: H2 "No goal yet"; DC and microgrid show an empty goal field beside an assumed 3 h/yr.
- `template-e2e` 18: H2 Guided EUR 7.61 M vs Expert Summary EUR 9.02 M.
- `template-e2e` 16: Expert Reports "No reports yet" and Results "Not run yet" after a certified Guided run.
- `template-e2e` 05: `grid_import`, "BESS new", "Fmea" in Guided.
- `investment-business-case-2026-11-17` 02: DC investment case "needs the site connection" while it is set. Guided "Investment case ›" lands on the same dead end.
- `asset-investment-2026-11-17` 02: "Certified 0.5 h/yr" next to "Site transformer about EUR 7.04 M per year" (DC) and "no shortfall" next to "Hospital feeder EUR 657 k" (microgrid); the EUR figure is not explained beside the verdict.

## Not walked, and what to trust

**Scope for the next rotation: Expert field edits were not walked on any of the three templates.** Edit a value in each Expert panel (Loads, Assets, Capacity Bounds, Economics, Grid connection), save, re-run and check Results and the report.

- Guided steps were read as text and screenshots; I did not edit inputs by hand in Expert (panels were opened and the Run, Results, Reports and investment-case paths were exercised, not field edits). Business-case "where offered" was walked on DC (dead end), H2 (run) only.
- Run times are bounds (see the caveat). The H2 investment case took up to my 5-minute poll; its real time is unknown.
- Dark theme, the assistant and the Grid study panel were not walked.
