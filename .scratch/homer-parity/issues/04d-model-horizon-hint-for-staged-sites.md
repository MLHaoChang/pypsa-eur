# 04d — Model Horizon says when a site's stages already carry its load growth

Status: needs-triage

Follow-up from homer-parity 04a2 (UX 26's placement GO, 2026-11-19).

## Problem

The generic site template bakes each stage's peak into the network's per-period load (`eh_site.stage_network`, `load_scale`). If the user later sets a Model Horizon load growth rate (04a) on such a project, the derived multipliers scale the already-grown stage load again, so the growth counts twice. A typed load multiplier does the same today.

## Ask

Model Horizon's Load growth row says, for a project created from stages, that the stage peaks already set the load per period and that a rate here multiplies them. The wording comes from the UX reviewer. There's no engine change.

## Notes

- UI change, so it needs a UX review. Out of 04a2 by UX 26's GO.
