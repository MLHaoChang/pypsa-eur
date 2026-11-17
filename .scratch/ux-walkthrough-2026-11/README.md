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
| J3 | Check readiness | Section 9 | Issues | Guided says "Ready to run" with a Run study button; Expert header shows "Run LOPF". |
| J4 | Run | Section 9 Run study (or Goal card) | Header Run LOPF | Two different runs: Guided runs the reliability study, Expert runs a dispatch solve. |
| J5 | Wait | Section 9 "Studying… 0:29 elapsed", Abort | header pill | Header pill still reads "Idle" while Guided studies. No ETA. Run took 33 s (data centre), 12 s (microgrid). |
| J6 | Read results | Section 10 verdict, cost tile, risks | Results tabs | Guided verdict is "Certified" while Expert Results says "not run yet" for the same project. |
| J7 | Decide / compare | "The whole picture" tiles, Open the detail | Results › Compare, Scenarios | See findings. |
| J8 | Report / export | Download report (.docx) | Results › Hand-off, Reports | The docx downloads in Guided. |

## Findings

Filled in below as the walk completes. Severity: high = blocks or misleads the journey, medium = costs the user time, low = polish. Status: open = still broken on master c78a115e, fixed = no longer reproduces.

| Template | Screen | Finding | Sev | Status | Shot |
|---|---|---|---|---|---|
| Island Microgrid | Expert Results after a Guided study | Guided says "Certified", Expert Results says "not run yet", Obj "—", header still offers Run LOPF (EXPERT-GUIDED-1) | high | open (new) | `mg-expert.small.png` |
| all hubs | Guide tour (hub-guide-button) | Opt-in only: a small dotted "Guide" button, never offered on first open. Titled "Design a hub in five steps", has 7 popovers, the stepper has 10 sections. Stays on the current section while describing others. The popover covers the control it explains. Says the assistant can do any step while no key is set. | medium | open (new) | `mg-tour-3.small.png` |
| Island Microgrid | Section 9 while running | Header pill reads "Idle" while Guided shows "Studying… 0:08 elapsed" | medium | open (new) | `mg-run-wait.small.png` |
| Island Microgrid | Section 10 | No chart; verdict sentence, one cost tile, ranked risk list. Same as ui-modern audit finding 1 | high | open (audit 1) | `mg-after-run.small.png` |
| all | New project › From template | A template whose name exists creates nothing: toast "already exists, so nothing was created" and an Open button. No rename or auto-suffix | low | open (new) | `t1-landing.small.png` |
| Island Microgrid | Expert and Guided | Raw tool names on screen: "Run LOPF", "Capacity Bounds", "Solver Settings" (RAW-NAMES-1) | medium | open (new) | `mg-expert.small.png` |
| Island Microgrid | Keyboard | No `main` landmark, no h1 (two h2), Tab order starts mid-stepper, some focus stops are unlabelled spans | medium | open (new) | none |
| Island Microgrid | Section 3 on first open | Project opens on section 3 "Connection & grid", not section 1 | low | open (new) | `mg-landing.small.png` |
