<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Raw tool and engine names on screens (RAW-NAMES-1)

Status: ready-for-agent; audit 2026-11-25: #1827, #1820, #1821, #1897, #1738 merged (several RAW-NAMES-1 surfaces); left: chat-card rows (AssistantResultCard.tsx:153-189 still shows task_id and tool names) and the rest
Model tier: sonnet
Size: M
Blocked by: #1634 (expert catalogue core) merged
Likely files: `AssistantResultCard.tsx`, Expert header and Results labels; the catalogue raw-name guard
Type: finding (keyed walkthrough audit, UX-NEXT-1)
Journey steps: J4 Run, J2 Set up, chat dock
Severity: medium
Send to: next-step flow; expert-catalogue. Duplicate check: template-e2e gap 05 and the catalogue raw-name guard cover part of this; chat-card rows are to confirm live

## Seen on screen

- Expert header and Results: "Run LOPF" (also in the palette), "Capacity Bounds", "Solver Settings", "Grid study" (`shots/mg-expert.small.png`).
- Sidebar rows "Add demand", "Time Series" use PyPSA words with no Guided counterpart name.
- Static read of the chat cards (chat dock not audited live): AssistantResultCard shows `run_lopf (waiting)`, raw job ids and `diff.attribute` such as `p_nom` in Expert (`components/AssistantResultCard.tsx:63,173,209`), the prompts "Use get_task / resume_task / cancel_task with task_id …" (:183-188), and `next.error` verbatim (:218); ChangeSetCard shows "Source: {item.source}" and the backend `row.label` (`ChangeSetCard.tsx:115,207`).

## Done when

Expert labels use the same words as the Guided sections for the same feature; engine names appear only in a Source column or a tooltip.

## Journey fit

J4 (the run button name) and J2 (panel names).

## Comments

**2026-11-17 triage (SCALE-1).** Owned by RAW-NAMES-1 and the expert-catalogue track (#1597, #1634); template-e2e 05 covers part of the screens. Build after the catalogue core so labels come from it. The chat-card rows (`run_lopf (waiting)`, job ids, `diff.attribute`) are to confirm live first; no new package until the catalogue session says they are outside its scope.

- 2026-11-23 status sweep: landed in #1827 (46b5a40f4f); #1820 (afd3992a33); #1821 (1cd3c835a1); #1897 (d76e562211); #1738 (6c32315cbb): several RAW-NAMES-1 surfaces. Remains: chat-card rows and the rest, unverified.

## Audit (2026-11-25)

- 2026-11-25 audit: five PRs in the origin/master log; AssistantResultCard.tsx still builds raw get_task/resume_task/cancel_task prompts with task_id.
