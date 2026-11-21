<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Two admission refusals still read as restriction_unavailable

Status: ready-for-agent
Type: task
Found by: live-voice 17 (`17-no-access-actor-reads-restriction-unavailable.md`).

Issue 17 gave "no access to this project" its own kind (`project_access_denied`).
`harness/restrictions.capture` raises two more refusals with a plain-string detail,
and `loop.dispatch_tool_call` maps every non-dict detail to `restriction_unavailable`
("Saved chat restrictions are unavailable"):

- 403 "Chat ownership changed." (the chat's owner is not the acting user);
- 409 "Project binding changed. Refresh access before saving restrictions." from
  `current_project`, when the active project is no longer the one resolved.

Neither is about the rule store. The user is told the store is down.

## Scope

- `harness/restrictions.capture`: a typed kind for each, in a `tool-error-kinds.d`
  fragment. `current_project` keeps its string detail for the restrictions save route.
- Tests through `dispatch_tool_call` for both.

## Comments

## Triage (2026-11-20, master 87cf52a7)

Checked by code reading only. Still true: `harness/restrictions.py` still raises the two plain-string refusals (lines 39 and 82). Acceptance: each gets its own typed kind like `project_access_denied` (issue 17) with a plain message, `dispatch_tool_call` maps them to it instead of `restriction_unavailable`, a test per refusal, and a new error-kind fragment under `pypsa-gui/tool-error-kinds.d/`. Gated: GK B. Blocked by: none.
