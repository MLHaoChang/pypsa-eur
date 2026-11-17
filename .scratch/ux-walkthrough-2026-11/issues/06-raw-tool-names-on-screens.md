<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Raw tool and engine names on screens (RAW-NAMES-1)

Status: needs-triage
Type: finding (keyed walkthrough audit, UX-NEXT-1)
Journey steps: J4 Run, J2 Set up, chat dock
Severity: medium
Send to: next-step flow; expert-catalogue

## Seen on screen

- Expert header and Results: "Run LOPF" (also in the palette), "Capacity Bounds", "Solver Settings", "Grid study" (`shots/mg-expert.small.png`).
- Sidebar rows "Add demand", "Time Series" use PyPSA words with no Guided counterpart name.
- Static read of the chat cards (chat dock not audited live): AssistantResultCard shows `run_lopf (waiting)`, raw job ids and `diff.attribute` such as `p_nom` in Expert (`components/AssistantResultCard.tsx:63,173,209`), the prompts "Use get_task / resume_task / cancel_task with task_id …" (:183-188), and `next.error` verbatim (:218); ChangeSetCard shows "Source: {item.source}" and the backend `row.label` (`ChangeSetCard.tsx:115,207`).

## Done when

Expert labels use the same words as the Guided sections for the same feature; engine names appear only in a Source column or a tooltip.

## Journey fit

J4 (the run button name) and J2 (panel names).
