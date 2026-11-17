<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A user without project access reads as restriction_unavailable

Status: ready-for-agent
Blocked by: none (live-voice 11b merged, #1548)
Model tier: sonnet
Size: S
Gated: GK B
Likely files: `pypsa-gui/backend/harness/restrictions.py` (`capture`, `require_allowed`), `pypsa-gui/tool-error-kinds.d/<branch>.json`, `pypsa-gui/backend/tests/test_lv11b_cancel_stop.py`
Type: task
Found by: live-voice 11b (#1548), GK B (59) N2.

When the acting user has no access to the open project, `dispatch_tool_call` refuses
every tool at `harness/restrictions.capture`. That refusal is 503
`restriction_unavailable` ("Saved chat restrictions are unavailable"). The user is told
the restrictions store is down, when the real cause is that they cannot act on this
project. The tool's own ACL refusal (for example `cancel_job` → 404 `job_not_found`)
never runs.

It predates 11b. `tests/test_lv11b_cancel_stop.py::test_cancel_job_through_dispatch_runs_each_acl_as_the_acting_user[foreign_actor-...]`
pins today's behaviour and changes with this fix.

## Scope

- `harness/restrictions.capture` / `require_allowed`: tell "no access to this project"
  (a 403/404 with its own kind) apart from "the store is unreadable" (503, fail closed).
- The manifest fragment for any new kind, and the frontend copy if it is a banner.

## Comments

**2026-11-17 triage (SCALE-1).** Still needed on master. A new error kind goes in a `tool-error-kinds.d` fragment; the frontend copy changes only if the kind is a banner.
