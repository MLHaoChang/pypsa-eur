# Chat reading of the plan-vs-operation gap

Status: needs-triage
Type: task
Blocked by: 07
Size: S

## What to build

The plan-vs-operation gap of the operational run (issue 07, S3 and S4) is shown on Results › Summary, in the Plan vs operation card. Chat has no tool for it; `services/not_via_chat.py` entry `dc_operational_gap` points to that card. The route this issue holds:

- `GET /api/results/operational_gap` (read-only; it never solves)

Decide (owner) between a typed read tool and a UI pointer only (as DC20-POINTER-1 did for the DC backup and architecture). A read tool must keep S3's guarantees: it never solves, it answers 409 while a solve runs, and a missing or out-of-date gap reads None with its flag, never 0.

## Done when

- The route is served by a tool, or carries a `ui-pointer` declaration naming an owner decision, and the `held-by-spec` declaration in `pypsa-gui/backend/smoke/assistant_route_parity_declarations.d/claude-dc07-s4-gap-table.json` is gone.
