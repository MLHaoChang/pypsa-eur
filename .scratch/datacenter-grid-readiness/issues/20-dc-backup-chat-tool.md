# Chat control of the data-centre backup

Status: needs-triage
Type: task
Blocked by: 03
Size: S

## What to build

The data-centre backup (issue 03) is set and assessed from the Backup section of the Grid study tab. Chat has no tool for it yet; `services/not_via_chat.py` entry `dc_backup` points to that section. The routes this issue holds:

- `PUT /api/campus-electrical/{name}/dc-backup`
- `POST /api/campus-electrical/{name}/dc-backup/assess`

Decide (owner) between a typed tool for each, or a UI pointer only (as AS19-POINTER-1 did for the intent comparison). A tool must keep 03's guarantees: the PUT leaves the network fingerprint, the investment-case digest and network.nc byte-equal; the assessment solves on a private copy and writes nothing.

## Done when

- Each route is served by a tool, or carries a `ui-pointer` declaration naming an owner decision, and the `held-by-spec` declarations in `pypsa-gui/backend/smoke/assistant_route_parity_declarations.d/claude-dc-03-battery-backup.json` are gone.

## Comments
