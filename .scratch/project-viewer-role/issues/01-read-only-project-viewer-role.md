<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A read-only project viewer role

Status: needs-triage
Type: task
Source: owner decision LC48C-ROLE-1 (2026-11-14), from LC-48c (`.scratch/live-copilot/issues/48c-undo-rebind-viewer-rows.md`)

A product idea, not yet a plan. Project access today has two roles, admin and member
(`pypsa-gui/backend/services/tenancy_service.py`, `services/project_acl.py`); both may
edit. A person who should only look at a project (a reviewer, a client, a manager) has
to be given edit rights or nothing.

The idea: a viewer role that may open a project, read its network, runs, reports and
deliveries, and is refused every write, undo, snapshot restore and copilot write tool
with one clear kind.

The live-copilot gate row `LC-D22.viewer_role` was dropped because no such role exists
(`pypsa-gui/backend/smoke/family_manifest.py`); if this is built, that row comes back
as a behavioural fixture (a viewer's edit, undo and restore refused, state unchanged).

Open questions for triage:

- Is the role per project, per workspace, or both?
- Does a viewer see the chat panel, and may the copilot run read tools for them?
- Do deliveries and downloads stay open to a viewer?

## Comments
