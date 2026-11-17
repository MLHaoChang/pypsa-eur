<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Keyed walkthrough audit, 17 Nov 2026 (UX-NEXT-1)

Master c78a115e. Owner decisions: HOLISTIC-1, HOLISTIC-REC-1, UX-NEXT-1, EXPERT-GUIDED-1, RAW-NAMES-1.
Method: Playwright (Chromium, 1280×800) against the local API and Vite as the dev admin; every template created from the New project dialog, Guided walked through all 10 sections, Run study, Results, report download, then Expert.

**Not audited: the chat dock in use.** No `ANTHROPIC_API_KEY` is set in this environment (owner chose "walk now, chat later", 17 Nov). Only its "needs an API key" state was seen. A fresh session audits the dock cards once the key exists. Static read of the card components: see the section at the end.

## The journey (shared reference for all UX-NEXT-1 packages)

One end-to-end path, in Guided and Expert. Every spec and PR body carries a **Journey fit** line naming the steps it touches and its hand-off to the step before and after.

| # | Step | Guided home | Expert home | Hand-off today (checked) |
|---|---|---|---|---|
| J1 | Pick template | Home › New project › From template | same dialog | Some cards open an inline form (data centre) before Create. A duplicate name creates nothing. Lands on the project in Guided, on section 3 (not 1) for the microgrid. |
| J2 | Set up site and assets | Sections 1-7 | Sidebar groups 1-7 | Each section has "Next: <section> ›", but sections 2, 4, 5, 6 are a sentence and an "Open in Expert ›" link, which switches mode. |
| J3 | Check readiness | Section 9 | Issues | Guided says "Ready to run" even where Run study is disabled (IPP). Expert header shows "Run LOPF". |
| J4 | Run | Section 9 Run study (or Goal card) | Header Run LOPF | Two different runs: Guided runs the reliability study, Expert runs a dispatch solve. |
| J5 | Wait | Section 9 "Studying… 0:29 elapsed", Abort | header pill | Header pill still reads "Idle" while Guided studies. No ETA. Run took 33 s (data centre), 12 s (microgrid). |
| J6 | Read results | Section 10 verdict, cost tile, risks | Results tabs | Guided verdict is "Certified" while Expert Results says "not run yet" for the same project. |
| J7 | Decide / compare | "The whole picture" tiles, Open the detail | Results › Compare, Scenarios | See findings. |
| J8 | Report / export | Download report (.docx) | Results › Hand-off, Reports | The docx downloads in Guided. |

## Per-template walk

Guided through all 10 sections, Run study, section 10, Download report, then Expert. Chat off.

| Template | Run study | Verdict in section 10 | Report .docx | Note |
|---|---|---|---|---|
| Data Center Energy Hub | 33 s | Certified, 0.5 h/yr, €34.7 M | downloads | 409 in console; section 1 opens "to check" (no site location) |
| Industrial Hydrogen Hub | not walked | n/a | n/a | the copy used had no `eh_template.json`, see issue 08 |
| Island Microgrid | 12 s | Certified, 0.0 h/yr, €3.47 M | downloads | opens on section 3; Expert says "not run yet" afterwards (issue 01) |
| Data Center (staged growth) | 78 s | Certified, no shortfall | downloads | 3 × 409 in console |
| Industrial Hydrogen (staged growth) | 69 s | no goal set, no verdict ("step 3 (Goal)") | downloads | issue 05 |
| Green Ammonia (staged growth) | 253 s | no goal set, no verdict | downloads | issue 05, 03 |
| Mining Concentrator (staged growth) | > 25 min, still running | Running | not reached | 26 280 snapshots, elapsed counter only (issue 03) |
| Renewables IPP with battery | disabled | Not run yet (dead end) | not reached | issue 02; Expert Run LOPF solves in 14 s |
| Island Microgrid (staged growth) | 138 s | Certified, no shortfall | downloads | canvas opens fitted |
| Your own site | n/a | n/a | n/a | opens the Guided form (not walked further) |
| 3-Bus Tutorial, IEEE 14-Bus, Belgium Grid | n/a | n/a | n/a | 404 in a dev run (issue 08) |
| IEEE 39-Bus (New England) | not walked | | | planning-to-dynamics case, no Guided hub path |

## Findings (new, in this walk)

Severity: high = misleads or blocks the journey, medium = costs the user time, low = polish. Status "open" = reproduced on master c78a115e. Issue files are in `issues/`.

| Template | Screen | Finding | Sev | Status | Shot / issue |
|---|---|---|---|---|---|
| Island Microgrid, data centre | Expert Results after a Guided study | Guided "Certified", Expert "not run yet" (EXPERT-GUIDED-1) | high | open | 01, `mg-expert` |
| Renewables IPP | Sections 9, 10 | Run study disabled, results say "Go to Goal and run the study"; dead end | high | open | 02, `ipp-Read` |
| all hubs | Section 9 wait | Header pill "Idle" while "Studying…"; no ETA; Mining > 25 min; 409s in console | high | open | 03, `mg-run-wait` |
| all hubs | Guide tour | Opt-in dotted button, "five steps" vs 7 popovers vs 10 sections, never moves section, popover covers its target | medium | open | 04, `mg-tour-3` |
| staged H2 and NH3 hubs | Section 10 | "set … in step 3 (Goal)": Goal is section 8; no verdict | medium | open | 05 |
| all hubs | Section 1 and 9 | Every template opens amber: "The site has no location: no bus is placed" | medium | open | 05 |
| Island Microgrid | First open | Lands on section 3, not 1 | low | open | 05, `mg-landing` |
| all | Expert header, sidebar, chat cards | Run LOPF, Capacity Bounds, Solver Settings, tool ids in chat cards (RAW-NAMES-1) | medium | open | 06 |
| all | Keyboard | No main, no h1, odd first Tab stop, chat cards take no focus | medium | open | 07 |
| 3-Bus, IEEE 14, Belgium | New project | 404 toast names `project_templates/_build.py`; duplicate name creates nothing | medium | open | 08, `tpl-404` |

## Re-check of the open findings

### `.scratch/ui-modern/audit.md` (9 Nov, 11 findings), re-checked in the running app

| # | Finding | Status now | Evidence |
|---|---|---|---|
| 1 | Guided Results has no chart | still-broken | `mg-after-run`: verdict, one tile, risk list |
| 2 | Network canvas opens un-fitted | fixed | `chk-canvas`: fitted, legend open, nodes readable at 1280 px (labels still small) |
| 3 | Section 7 raw form | fixed | `chk-guided-s07`: styled tariff and contracts cards |
| 4 | Walls of "assumed" rows with raw keys | partly fixed | rows are plain sentences now ("Discount rate: 7 % nominal (assumed)"); every row still has an amber dot and "Edit ›"; "Keep all defaults" is the one bulk action |
| 5 | Charts do not read as one system | not re-checked | Expert Operation not walked |
| 6 | Stepper clipped at 1280 px | still-broken | every section shot cuts "5 A…" or the first steps |
| 7 | Four sections are a sentence and a link | still-broken | sections 2, 4, 5, 6 list rows with "Edit ›" and "Open … in Expert ›" |
| 8 | Always-on chrome | still-broken | "Every step below works by hand…" banner on every section; "Chat here" button on every screen |
| 9 | Home ignores the theme | still-broken | set light inside a project, Home stays dark (`rgb(21,17,18)`) |
| 10 | Loading and empty states | still-broken | Summary "No results yet" plain; "Idle" pill during a run; section 3 shows tooltips only |
| 11 | Wording rules | still-broken | em dashes in section 3 text; "(s)" plurals not seen this time |

### `.scratch/project-workflow/issues/UX-flow-expert-audit.md` (26 rows), re-checked in code

Fixed: 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 23, 24, 25, 26 (26 opens the first section, not the first incomplete one).
Still broken: **1** (the reliability goal is still typed in the EH pack, `EhReferenceDesignPanel.tsx:83-88`, and Solver settings, `SolverSettings.tsx:1533`; the loop panels now read the goal) and **22** (`paletteData.ts:15-17,55-57` still puts Bus, Line, Transformer and demand in the Assets palette).
Unclear: 17 (commercial "Library" renamed "Commercial library"; campus rename and double mount not checked).

### `docs/superpowers/research/2026-09-28-ux-audit-novice-path.md` (top 15)

Fixed: 1 (question-first decision study card), 2 (NPV, IRR and break-even exist), 10 (Results opens on Summary after a solve; report in Guided), 13 (four of the five copy defects), and "no tour" (GuidedTour exists, see issue 04).
Still broken: 11 (template 404 names a developer path in dev runs, issue 08), "no glossary" (only a Guided `Term` component), the raw-name part of 9 (issue 06).
Unclear, needs a deeper walk: 3, 4, 5, 6, 7, 8, 9 (Expert jargon), 12, 14, 15.

## Chat dock

**Not audited live (no API key).** Static read of the card components, as the owner chose (17 Nov): see issues 06 and 07 for the raw-name and focus items. Also from that read, not yet seen on screen:
- `AssistantResultCard.tsx:194` "Project change preview · 1 values"; `ChatLaunchGreeting.tsx:197` "1 buses · 1 snapshots" (no singular).
- Em dashes joining clauses in `ChatLaunchGreeting.tsx:79,80,90,101,206`.
- Silent failures: greeting meta, status and hub-study queries show nothing on error (`ChatLaunchGreeting.tsx:116-137,181,195`); "Checking…" reverts after 15 s with no text (`AssistantResultCard.tsx:116-121`); options and Send are disabled while streaming with no visible reason (`ChatPanel.tsx:603-605,636`).
Cards, choice cards and result cards in use need a fresh session once `ANTHROPIC_API_KEY` is set.

## Not covered

IEEE 39-Bus, the "Your own site" form beyond its first screen, Expert Operation and Compare tabs, light-theme Guided, and the template-e2e decision inventory (not on master when this ran). The Mining run was still going at the end of the walk.
