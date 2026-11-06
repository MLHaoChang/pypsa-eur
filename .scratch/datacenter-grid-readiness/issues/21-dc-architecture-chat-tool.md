# Chat control of the data-centre power-architecture comparison

Status: needs-triage
Type: task
Blocked by: 05
Size: S

## What to build

The power-architecture comparison (issue 05) runs from the Power architecture section of the Grid study tab. Chat has no tool for it yet; `services/not_via_chat.py` entry `dc_architecture` points to that section. The route this issue holds:

- `POST /api/campus-electrical/{name}/dc-architecture/compare`

Decide (owner) between a typed tool or a UI pointer only (as AS19-POINTER-1 did for the intent comparison). A tool must keep 05's guarantee: the screen reads the saved network and writes nothing.

## Done when

- The route is served by a tool, or carries a `ui-pointer` declaration naming an owner decision, and the `held-by-spec` declaration in `pypsa-gui/backend/smoke/assistant_route_parity_declarations.d/claude-dc-05-architecture-compare.json` is gone.

## Comments
