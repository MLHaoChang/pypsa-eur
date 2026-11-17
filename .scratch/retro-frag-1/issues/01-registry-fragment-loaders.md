Status: in progress (#1566 merged, loaders; #1610 docs; migration pending a coordinator slot)

# RETRO-FRAG-1: registry fragments

Owner decision RETRO-COST-1 pattern; retro brief 2026-11-17 ("improve code efficiency and implementation speed to enable a lot of sessions run in parallel").
Plan GO: GK B (60), Q1 a, Q2 a, conditions F1–F7.

1. #1566 (merged): `.d/` fragment loaders for the lock policy, chat disposition, not_via_chat, family manifest and golden ROUTE_SURFACES; no row moved.
2. #1610: steward (live and template), LEARNINGS, harness README say "add a fragment".
3. Migration: move the existing Python rows into each JSON base, in a slot the coordinator picks; rerun the F7 digest before/after (equal for all ten tables).

## Comments

Follow-up left out by GK B Q2 (a), see `02-tool-registry-fragments.md`.
