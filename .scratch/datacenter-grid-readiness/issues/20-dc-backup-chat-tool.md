# Chat control of the data-centre backup

Status: ready-for-agent
Type: task
Blocked by: 03
Size: S

## What to build

The data-centre backup (issue 03) is set and assessed from the Backup section of the Grid study tab. Chat has no tool for it yet; `services/not_via_chat.py` entry `dc_backup` points to that section. The routes this issue holds:

- `PUT /api/campus-electrical/{name}/dc-backup`
- `POST /api/campus-electrical/{name}/dc-backup/assess`
- `GET /api/results/reserves` (issue 06: reserve revenue of the backup storage, read-only)

Decide (owner) between a typed tool for each, or a UI pointer only (as AS19-POINTER-1 did for the intent comparison). A tool must keep 03's guarantees: the PUT leaves the network fingerprint, the investment-case digest and network.nc byte-equal; the assessment solves on a private copy and writes nothing.

## Done when

- Each route is served by a tool, or carries a `ui-pointer` declaration naming an owner decision, and the `held-by-spec` declarations in `pypsa-gui/backend/smoke/assistant_route_parity_declarations.d/claude-dc-03-battery-backup.json` and `claude-dc06-flex-revenue.json` are gone.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Not buildable until answered.
- Question (GK A): Owner: for the data-centre backup routes (`PUT /api/campus-electrical/{name}/dc-backup`, `POST .../dc-backup/assess`, `GET /api/results/reserves`), does chat get typed tools, or is it a pointer to the Backup section only (as AS19-POINTER-1 did)? Pointer = S, sonnet, drops the two held-by-spec declarations for ui-pointer ones; tools = M and must keep 03's no-write guarantees. The same answer decides DC 21.
- Blocked by: the answer.

### 2026-11-07: needs-info answered (coordinator)

Owner (card, DC20-POINTER-1): "Pointer only", as AS19-POINTER-1. Chat points to the Backup section; the two held-by-spec declarations become ui-pointer ones; no typed tools. S, Sonnet, GK B (chat surface) with GK A for DC-03's no-write guarantees. The same answer decides DC 21.
