# Energy Hub Reference Design panel (frontend)

**Date:** 2026-09-15  
**Parent plan:** `docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md` (P5 leftover)  
**Backend:** `POST/GET /results/eh_study`, abort, `GET /results/eh_reference_design` (P1.5 HTTP GO)

## Goal

Let a user run an archetype pack study from Results → Adequacy and read the
`ReferenceDesignReport` summary without leaving the Adequacy tab.

## Non-goals (this slice)

- Chat tools for `eh_study`
- Deep redundancy / levers / DtC table UIs (GETs exist; follow-up)
- New Adequacy IA or marketing layout
- P2 / P6–P9 modelling

## Approach (chosen)

**One collapsible panel** matching `FrontierPanel` / `LoopPanel`:

1. Archetype select: `strong_grid` | `weak_flexible` | `off_grid`
2. Run → `POST /results/eh_study` `{ archetype }`
3. Poll `GET /results/eh_study` every 2s while `status === 'running'`
4. Abort → `POST /results/eh_study/abort` (visible only while running)
5. On done: show report headlines (from study `report` and/or
   `GET /results/eh_reference_design`): ENS target/achieved, cost@target,
   TEA LCOE if present, completeness map as status chips
6. 409/422 via existing `blockerMessage`

Mount **last** on `AdequacyTab` (after margin loop): packaging study over the
stack the user already read.

## Files

- `api/simulation.ts` — EH client methods + types
- `pages/results/EhReferenceDesignPanel.tsx` (+ `.test.tsx`)
- `AdequacyTab.tsx` / `AdequacyTab.test.tsx` — mount + order invariant

## Acceptance

- Panel mounts with no early return (204 / empty session)
- Run requires archetype; Abort only while running
- Completeness statuses visible for a done report
- AdequacyTab panel order includes `eh-reference-design-panel` last
