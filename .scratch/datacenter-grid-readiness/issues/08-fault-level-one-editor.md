# Fault level: edit the value and its source in one place

Status: ready-for-agent
Type: task
Blocked by: 02 (slice 6 UI, PR #596)
Gated: no (frontend)
Model tier: sonnet
Size: S

UX reviewer follow-up on #596. Today the fault level's value is edited on the PoC bus (`eh_sk_mva`, Properties "Short-circuit level"; Guided GenericSiteForm `fault_level_mva`) and its source in Connection limits (`components/gridReadiness/ConnectionLimits.tsx`), which shows the value and an "Edit the fault level ›" route.

## What to build

- Make the value editable on the Connection limits "Fault level" line, writing the same `eh_sk_mva` on the PoC bus, next to its Source select.
- Turn bus Properties' "Short-circuit level" (`layout/properties/cardKit.tsx`) and GenericSiteForm `fault_level_mva` read-only, each with "Edit in Connection & grid ›" through `openFlowTarget` (Expert) / `openFlowTargetInPlace` (Guided).
- The write goes through the existing bus update path (gatekeeper to confirm); one editor per value.

## Done when

- One place edits the value and source; the other two show it read-only with the route.
- Tests cover the line, both read-only spots, and the routes.
