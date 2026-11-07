# Chat control of the data-centre power-architecture comparison

Status: ready-for-agent
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

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `services/not_via_chat.py` (entry `dc_architecture`), `pypsa-gui/backend/smoke/assistant_route_parity_declarations.d/claude-dc-05-architecture-compare.json`, a harness tool or ui-pointer declaration.
- Blocked by: owner answer to the DC 20 question (does not hold the pointer variant).
- Model tier: sonnet.
- Gated: GK A.
- Size: S.
- Remove the `not_via_chat` row `dc_architecture` and the held-by-spec parity declaration (replace with the tool or a `ui-pointer` declaration). Tool or pointer follows the owner's answer to DC 20; default pointer per AS19-POINTER-1. DC 05 (#1199) is merged.

### 2026-11-07: needs-info answered (coordinator)

Owner (card, DC20-POINTER-1): "Pointer only" applies here too: chat points to the Power architecture section; remove the `dc_architecture` not_via_chat row and its held-by-spec parity declaration in favour of a ui-pointer one. S, Sonnet.
