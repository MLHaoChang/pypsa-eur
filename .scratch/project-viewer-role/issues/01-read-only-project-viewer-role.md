<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A read-only project viewer role

Status: ready-for-agent
Type: task
Source: owner decisions LC48C-ROLE-1 (2026-11-14), VIEWER-ROLE-1 (2026-11-17, build it now), from LC-48c (`.scratch/live-copilot/issues/48c-undo-rebind-viewer-rows.md`)

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

## Plan (2026-11-17)

Gated: GK B (backend, harness); UX for PR 2. Two PRs.

**Role.** `ProjectMembership` gains `role` (`editor` default, `viewer`) with an Alembic
migration; existing rows stay editors. The role lives on the tree-root membership, like
access today. Org admins and anyone who created a project in the lineage are always
editors. `project_acl.project_role(db, user, project) -> 'edit' | 'view'` is the one place
that answers it. `PUT /{name}/members` takes an optional per-user role (the old body
still means "editors"). Only someone who may manage members can set a role.

**One refusal kind.** `project_view_only`, HTTP 403,
`{"error_kind": "project_view_only", "message": "You can view this project but not change it. Ask its owner for edit access."}`.
A 403 is fine here: a viewer can already see that the project exists, so this tells them nothing new. Declared in
`pypsa-gui/tool-error-kinds.d/claude-viewer-role-backend.json`. The same kind comes back from
HTTP routes, undo, snapshot restore and chat write tools.

**HTTP enforcement (PR 1).** One app-level dependency, `require_project_write` in a new
`services/project_write_gate.py`, added to `FastAPI(dependencies=[...])`. It runs on
every POST/PUT/PATCH/DELETE. The route is resolved from `request.scope["route"]`. The
target project is the `{name}`/`{project_id}`/`{base}` path parameter if there is one,
otherwise the active project. If the caller's role on that project is `view`, it raises
`project_view_only`. **Default-deny:** a table classifies each of the 306 mutating routes
(today's inventory) as exactly one of:
- `project_write` (refused for a viewer). This covers all of `/api/network/*` (incl.
  `POST /undo`, `/reset`, `/cluster`, every component add/edit/delete, timeseries,
  snapshots and `_bulk`), `/api/campus-electrical/*`, `/api/gridspine/{name}/*`,
  `/api/simulation/*` (run, run_ac_pf, finance, solver_config, value_flows,
  document_values/apply, force_reset, abort), the `/api/results/*` study runs and aborts,
  `/api/io/import/*` into the active project, and every `/api/projects/{name}/*` write:
  save, rename, delete, scenario, settings, layout, map_layout, sites, scene, profiles,
  weather, worksheet, uploads, snapshots (create, delete, **restore**), studies, ledger,
  reports (create, generate, regenerate, template, roundtrip, delete), report-charts,
  flow ack/scope, stress_scenarios, asset_health, intent-comparison, members, and lock
  acquire/heartbeat.
- `viewer_allowed`, each with a written reason: `POST /{project_id}/activate` (opening
  a project; the save-on-switch skips a project the caller only views), `DELETE /lock`
  (release only), the 8 declared `non_mutating_routes` previews/preflight, chat turns
  and run controls (writes are refused inside the chat, below), and the answers to the
  owner questions (comments, exports).
- `not_project` (account, org and personal state, unchanged): auth, admin, chat and LLM
  settings, local-settings, memory, MCP, dictation, changelog, library, and creating a
  new project (from template, import, import-folder, unclaimed).
Routes that name their project in the body or the job (`POST /api/simulation/queue`,
`queue/{job_id}/requeue`, `/{base}/scenarios`) get an explicit `project_role` check in the
handler next to the lock check that is already there. The middleware is not used for
this, since it can only see the active project.
A route added later with no row fails `test_viewer_route_table_is_total`.

**Chat enforcement (PR 1, backend).** `harness/restrictions.capture` puts the caller's
project role into the snapshot. `require_allowed` refuses every tier except `read` with
`project_view_only` when the role is `view`, whatever rule the caller has saved (a viewer
cannot loosen it through `PUT /api/chat/restrictions`). Both dispatch seams already call it
(`harness/loop.py:3409`, `harness/change_set.py:112`), so `loop.py` and `chat_tools.py`
stay untouched. Write tools are the tools whose `catalogue.safety_tier_for` is `write`,
`destructive`, `execution` or `execution_long_running`. Voice turns use the same dispatch.

**Tests (PR 1).** `tests/test_viewer_role_gate.py` is parametrised over **every**
`project_write` route from `app_routes` (not a sample). For each route, a viewer gets 403
`project_view_only` and `lc43_gates.state` is unchanged; an editor is not refused by the
gate. The suite also covers: undo, snapshot restore, queue/requeue/scenarios body
routes, the members role round-trip and who may set it, admin and creator overrides,
cross-org still 404, and every non-read tool tier refused through `require_allowed`
with the state unchanged. Mutants run on `project_write_gate.py`, `project_acl.py` and
`restrictions.py`.

**Frontend learns the role (PR 2).** The project list, `GET` project and `activate`
responses carry `my_access: "edit" | "view"`, and `useProjectAccess()` reads it. Guided
and Expert hide or disable every edit control with the reason "View only — ask the
project owner for edit access" (EXPERT-GUIDED-1). The members dialog gains a role picker.
The chat panel stays visible: a viewer is offered only read tools, and the system prompt
says the project is view-only. No raw tool names appear in that text (RAW-NAMES-1).

**Last.** After #1566 merges, `LC-D22.viewer_role` in `smoke/family_manifest.py` returns as a
behavioural fixture: a viewer's edit, undo and restore are refused with
`project_view_only` and the state is unchanged. The `r3-uncovered:LC-D22` blocker is updated.

Open owner questions (sent to the coordinator): per-project vs. workspace scope; may a
viewer run a study; comment on reports; export/build deliveries.

## Comments

### 2026-11-17: GK B plan GO for PR 1, with conditions

GK B (session_01G7qCunbvxN6JVyrzVTsisA) reviews this package (the brief names it; GK B2 inactive).

- R1. Each project_write row declares its target resolver (path param, active project, handler check); id-keyed writes (snapshot, report, job, upload, study, site) resolve the id's owning project. Tests per resolver kind: viewer on X / editor on active Y and the reverse.
- R2. A non-member or other-org caller never sees `project_view_only`; the handler's 404 stands (status and body equal a nonexistent project's).
- R3. Role re-read at use: chat per tool call; queued/requeued jobs, run-profile and approval executors, Guided flows, scheduled triggers, sweep/experiment child writes at execution. A test downgrades editor → viewer between enqueue/approval and execution.
- R4. List every WebSocket route and every GET that writes project files; gate each or show it read-only, with a test. Name the tier source for undo, restore and every writing tool; a writing tool tiered `read` fails a test.
- R5. Members PUT: an absent role keeps the stored role (editor default for new members only); nobody changes their own role; admins and lineage creators read editor whatever the row says; mutant of `can_manage_membership` at the role write.
- R6. A malformed stored role reads `view` (fail closed); migration up/down on SQLite; existing rows read editor.
- R7. Totality test fails on an unclassified route and on a stale row; viewer_allowed rows carry a reason; a test pins the exact viewer_allowed set.
- R8. A mutant per guard: gate view check, each resolver, `require_allowed` role branch (with a loosening saved rule), members role authority, save-on-switch skip.
- R9. At the trial merge: test_route_chat_disposition, test_project_route_acl_policy, test_write_surface_lock_policy, test_golden_coverage, tool_error_kind_manifest, ChatPanel.manifest, assistant_inventory / route_parity / control_matrix --check.
- R10. Choice made: **role-setting stays out of PR 1.** Members PUT is unchanged in PR 1 (roles are set only by tests), so no viewer can exist before PR 2 ships the UI, the generic `project_view_only` message and the role picker. R5 lands in PR 2.
