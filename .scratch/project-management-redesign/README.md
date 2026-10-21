# GridSpine project management redesign — implementation-grounded brainstorm

Status: concept for owner discussion; refined by the feature audit and shared-assumptions proposal below. Not an approved implementation specification.
Reviewed: 21 October 2026, master `a68db6cfcf254df17a5b0d96da7a4c995f26423f`.
Initial method: read-only source review plus open-PR inventory. The follow-up audit adds targeted existing automated suites and three new HTTP restore probes; see VALIDATION.json for exact outcomes. No runtime implementation, deployment or live-browser usability study. Publication scope: one draft specifications PR for the entire project-management implementation including chatbot integration; no runtime features or fixes are implemented here.

**Start with the [concrete implementation plan](04-IMPLEMENTATION-PLAN.md) and [API/UI contracts](05-API-AND-UX-CONTRACTS.md), then the [chatbot integration](03-CHATBOT-INTEGRATION-SPEC.md).** The structured plan maps 15 delivery packages, 98 proposed acceptance cases and all 24 audited features.

**Read the [master feature audit](01-MASTER-FEATURE-AUDIT.md) and [shared-assumptions specification](02-SHARED-ASSUMPTIONS-SPEC.md) first.** They refine the initial brainstorm: safe capture/revert precedes visual redesign; existing section resume, queue and scenario services are reused; shared editing belongs in section 8 with explicit variant targets and guarded rollback.

## Recommendation

Make project management the place where a user understands the purpose of a study, its current design, what changed, what ran, which results are usable and what to do next. Reuse the existing project/scenario/version/job/access machinery. Preserve the approved project flow and one owning entry point per feature.

Keep the distinction between an example and a project: examples are starting configurations; a project is the user's working study with selected capabilities, horizon, sites, assets, variants, data and results. Do not make a separate project for every card, planning period or simulation run. Do not introduce a task-management system, arbitrary workflow editor or another assistant.

## Current foundation and observed gaps

| Area | Existing implementation | Redesign opportunity |
| --- | --- | --- |
| Projects home | Accessible root cards, Resume, blank/example/file/clone/decision-study creation, local-folder import when supported, legacy unclaimed-project import and missing-file states | Search, filters, list view, pinning, real activity timestamps and meaningful project summaries are not exposed by the current home. The large resume/create bands compete with the project list. |
| Scenarios | Nested parent-child tree, scenario descriptions/types, branching, solve selection, two-project compare, rename/delete | Show these as variants inside a project's management view. Keep the tree for lineage, table for scanning. Separate explicit baseline/preferred-design choices from scenario category labels. |
| Versions | Snapshot timeline, save/restore/delete and safety snapshot before restore; dispatch freshness metadata | Call these Saved versions in user-facing management to distinguish them from model time snapshots. Show what is captured and exact source identity. Snapshot category currently lives inside a message prefix, unlike scenario's typed category. |
| Runs | Solve queue, terminal states, logs, retained evidence and source-bound views exist in separate places | Give one project-level index linking a job to its inputs, exact results and exported artifacts. A solve job is not automatically a complete study across all requested capabilities. |
| Comparison | Rich saved-project A/B views for capacities, dispatch, prices, line loading, emissions, economics, curtailment, lost load and storage cycling | Preserve existing detail. Add input-basis differences and explicit comparability before highlighting a winner. Exact saved-version comparison needs its own supported read path. |
| Access | Organization/project registry, root-tree membership, edit locks, session-bound active project contexts | Show role/access/lock/solve protection separately. Per-variant private permissions and simultaneous same-project editing are not established by the current membership model. |
| Persistence | Stable DB project IDs, save/autosave, bundle import/export, rename/descendant protection, crash/missing-file handling | Add archive/recover lifecycle deliberately, without treating a saved snapshot as a complete immutable release of every project file. Keep desktop identity/migration behavior supported. |
| Workflow | Canonical ten-section project flow, server readiness, Guided/Expert context, owning editors | Surface a source-backed next action and selected-capability summary; do not create another editable settings dashboard. |

Concrete source findings:

1. `ProjectsHomePage.tsx:167` calls `formatRelativeTime(project.created_at)` and renders it as Updated. `projectsHome.ts` also sorts recency by created_at. Creation, modification and personal last-open time need distinct meanings. A DB updated_at column exists but is not exposed in ProjectInfo; it must not be assumed to capture every model/sidecar write without an audit.
2. `ScenariosPanel.tsx` computes Best objective as the minimum finite objective across the supplied list. The row delta's comparability warning checks snapshot counts only. Equal snapshot counts do not establish common demand, periods, weighting, prices, units, objective basis, scope or source quality. A portfolio-wide cheapest project is not a meaningful design winner.
3. `SnapshotsPanel.tsx` passes an onCompare handler that opens Results and its compare rail without supplying the clicked snapshot ID. `CompareView.tsx` reads two saved projects. An exact saved-version comparison must either be implemented or labelled unavailable; opening a generic rail is insufficient.
4. `WorkspacePanel.tsx` still tells users there is one process-global active network and no full isolation. `services/active_project.py` and `PyPSAService` now resolve authenticated request contexts by session, with a resident registry. Refresh the copy against current behavior; this does not imply simultaneous collaborative editing or complete proof of isolation.
5. Home `countScenarios` counts direct children while the scenario model supports deeper descendants. Decide whether badges mean direct variants or the entire family and use that definition consistently.
6. The Workspace panel shows an email in its organization-identity area because the current authenticated user payload lacks an organization display name. Add a safe non-admin organization-summary projection rather than exposing internal UUIDs or depending on admin endpoints.

## Proposed information architecture

Global navigation: **Projects · Examples · Library**, plus account/organization controls. This is a consolidation proposal: relocate existing entry points rather than add duplicates to every sidebar. The single GridSpine companion stays bottom-right.

Projects landing:

- Compact Resume strip: last project + last section + next relevant action, based on actual persisted context.
- Search field and New project button on one line.
- Views: Recent, Pinned, All, Archived; a simple Cards/List toggle.
- Optional filters: template/site type, tag, project owner and needs attention. Respect real available metadata; introduce new fields through explicit migrations/API contracts.
- Each project shows name, short purpose/site, horizon, selected work scope, variant count, actual last update and a concise attention/result summary.
- Cards suit exploration; list/table suits users managing dozens of studies. Server-side bounded search/pagination is a later scale requirement, not loading every network on every render.
- New project opens a streamlined drawer with Choose an example or Start your own. Upload/duplicate/local import remain discoverable secondary options using the existing creation paths. Keep currently supported decision-study entry coherent; do not make its question-first intake the universal project entry.
- Examples prefill one integrated planning setup, with configurable horizon and phases; no duplication into single-period versus staged product cards.

Project management opens with one persistent header and up to five compact views:

1. **Overview**: purpose/site/horizon, current variant, selected capabilities, section-state summary, current result basis and next action. Continue setup returns to the actual section in Guided or Expert; View results opens the exact selected result. Do not force overview detours when a user explicitly resumes work.
2. **Variants**: existing scenario tree/table, branch from an explicit source, human-readable differences and pairwise comparison. A root baseline is not silently copied into children when edited later. Current editable branch, comparison baseline and preferred design are different selections.
3. **Runs & versions**: separate Runs and Saved versions subviews, linked by source revision. Preserve job terminal states, immutable retained evidence and reversible restore. Saving changes, saving a version and executing a run are different actions.
4. **Files & reports**: existing imports, pinned Library evidence, outputs, reports and specialist exports, each with its source and freshness. Start with known supported artifacts; do not imply a new general document management service or universal report archive already exists.
5. **Access & settings**: team access and inherited tree scope, lock state, metadata, export/archive actions, and a deliberately separated delete action. Advanced parameterization stays in its owning flow section.

The ten workflow sections remain the model-editing journey. Management views organize work around it; they do not replace it or mirror all its forms.

## Example overview

```text
Projects / North campus                         [Continue setup ▸]
Data centre · 2027–2047 · Variant: Battery option B

Overview  Variants  Runs & versions  Files & reports  Access

NEXT ACTION
Confirm the 2030 connection limit                [Review field]
2 required inputs unresolved · 1 assumed input to acknowledge

STUDY SCOPE
Capacity  Grid  Reliability  Commercial  Control  Hand-off
Selected scope and availability come from the server contract.

CURRENT EVIDENCE
Selected result: run R… · completed · inputs changed since run
[View that result]    [Inspect changes]

VARIANTS                         RECENT ACTIVITY
Base design                      Connection limit changed
Battery option B                 Capacity run completed
Higher-growth case               Saved version created
```

All values in this wireframe are illustrative, not actual project observations. A completed run with edited inputs must remain visibly stale rather than being promoted to a current result.

## Status and result rules

Do not overload a single green/red project badge. Show separate meanings:

- User lifecycle: Active / Archived initially; any later In review / Delivered are human workflow labels, not engineering certification.
- Input readiness: existing server-authoritative section states and blockers.
- Execution: current job state, requested scope and any partial/interrupted work.
- Result validity: exact run/revision, current/stale/unavailable/failed, plus modeled scope and evidence quality.
- Access: viewing/editing, lock holder or solve protection and allowed next action.

Do not create competing backend enums when existing states suffice. A project with a good old result can still have incomplete current inputs; a completed capacity solve says nothing about unrequested grid compliance. Do not manufacture a completion percentage by counting fields. If a percentage is ever introduced, specify its scope/denominator and use the server's applicability states.

The latest modular-card design has **six** cards including Hand-off, each at most three sub-options. PR #496 is still open at inspection. Display the current server-supported scope, not a hardcoded claim that every planned option is available. This redesign must reconcile with that PR before implementation.

## Backend additions and reuse

### Mostly existing capability / initial changes

- Reuse project listing, scenario tree, snapshots, queue, retained results, bundle export and root membership endpoints.
- A bounded authorized project-summary DTO can combine canonical source IDs with summary metadata; do not load large NetCDFs for every row or activate a project simply to inspect its card.
- Correct creation/update labels before introducing new timestamps.
- Audit every write path before promising last_modified_at. Keep personal last_opened_at separate from a shared project change.
- Preserve stable project UUIDs in authenticated mode; avoid expanding name-based links. Local mode needs an explicit compatibility strategy.
- Expose safe organization display identity and server-calculated action capabilities rather than deriving permissions from a role badge.
- Route overview next actions through existing owning-editor/navigation behavior. Explanations can be assisted, but all necessary controls work without model calls.

### New product contracts

- Root project metadata: description, client/site label, tags, archive state and optional image. Location/site labels must reuse existing site records where present, not create a competing site registry.
- Per-user preferences: pinned projects, saved list filters and last section/mode; workspace-level settings must not silently become global browser state.
- Archive/unarchive semantics across a project family, active/queued work, membership, autosave, export and restore. Archive is not deletion or cancellation; define its read/write behavior before implementation. Trash/recovery is a separate later storage/retention feature.
- Exact relationship records between variant, saved source revision, job, result receipt and artifact; a management index references existing records rather than copying computed metrics.
- Compare contract for horizon, investment periods, time weights, demand, scenario changes, currencies/price year/cost basis, objective and scope. Different inputs may be intentionally compared, but their differences must be explicit. No generic better/worse badge across incompatible evidence.
- Explicit comparison baseline/preferred-design reference. A label called baseline or approved does not confer scientific validation or access permission.
- Activity summary from real durable events, bounded and authorized. Do not assume current logs constitute a complete immutable audit trail; index real events first and add missing mutation coverage deliberately.
- Typed version category if needed, replacing message-prefix encoding with a compatible migration.
- Per-version comparison only after a source-bound reader can resolve the selected snapshot without restoring/activating it.
- Per-project edit/viewer/reviewer roles or branch-private access would require real ACL/schema changes and are deferred; do not imply current tree membership supports them.

## Priorities

| Priority | Slice | Why first / acceptance |
| --- | --- | --- |
| P0 | Correct misleading time/compare/state copy and project/scenario/version terminology | Updated is sourced correctly or labelled Created; snapshot Compare resolves exact source or explains unsupported; no cross-project objective winner. Refresh stale isolation copy without making an unsupported guarantee. |
| P1 | Unified landing plus project Overview, search/list, explicit Resume/next action | User finds the intended study, knows current variant/result and continues in place; existing features are relocated coherently, not duplicated. |
| P1 | Variants and Runs & versions connected to existing comparison/evidence | Source identity, staleness and requested scope survive navigation and exports; static card reads do not load/activate networks. |
| P2 | Tags/pins/archive, safe metadata and linked Files & reports | Organization features become useful without adding a generic PM system. Archive cannot disrupt active jobs, ownership or recovery. |
| P2 | Access clarity and durable activity summaries | Existing permissions/locks reflected accurately; inherited access explicit; human lifecycle labels remain separate from engineering evidence. |
| Later | Rich review workflow, multi-project portfolio rollups, simultaneous editing | Only after a clear need and complete permission/comparability/persistence design. Portfolio totals require common units/time/value basis and scope. |

The first visible redesign should combine the P0 corrections with P1 landing/overview in small coherent slices. It should not require new solvers, a new co-simulation engine or a separate project backend. Appropriate migrations and source-bound readers are still real backend work.

## Validation to require before shipping

- Find and resume the intended root or nested variant with 0/1/50+ projects; search/filter/keyboard/mobile and clear error/empty states.
- Rename a root with descendants and preserve stable links, member inheritance, pins, compare selections and queued work.
- Source-correct creation/modification/last-open semantics; edits to real sidecars invalidate relevant summaries/results, passive reads do not.
- Switch organization/user/project while dialogs, requests or assistant work are pending; no cross-target mutation or tenant leak.
- Read-only lock, solve protection, revoked membership and local unauthenticated mode remain distinct and usable.
- Snapshot compare selects exact source or refuses without restoring. Restore protects current work and marks incompatible results appropriately; saving a version is not equivalent to a run.
- Compare equal snapshot counts with different demand, weighting, periods, currency or objective basis; show differences and reject unsupported rankings.
- Failed/cancelled/interrupted/stale/partial run, corrupt evidence and missing local folder remain explicit; no fallback to a different current result.
- Archive/unarchive/delete with nested variants, active jobs, autosave and owned study forks; no data loss, accidental cascade or resurrection of deleted content.
- Selected/deselected capability scope and unsupported options match the actual server contract, including planned #496 integration.
- Project overview deep links preserve mode, project, variant, exact result and return position; one feature has one owning entry point.
- Large project list has bounded authorized queries and no per-card heavy network hydration.
- No assistant API key is required to create, organize, compare, recover or inspect a project.

## Principal code references

- [Projects home](../../pypsa-gui/frontend/src/pages/ProjectsHomePage.tsx), [home helpers](../../pypsa-gui/frontend/src/pages/projectsHome.ts).
- [Workspace panel](../../pypsa-gui/frontend/src/pages/WorkspacePanel.tsx), [project menu/save actions](../../pypsa-gui/frontend/src/layout/Sidebar.tsx).
- [Scenarios](../../pypsa-gui/frontend/src/pages/ScenariosPanel.tsx), [comparison](../../pypsa-gui/frontend/src/pages/CompareView.tsx), [snapshots](../../pypsa-gui/frontend/src/pages/SnapshotsPanel.tsx).
- [Project API](../../pypsa-gui/frontend/src/api/projects.ts), [ProjectInfo](../../pypsa-gui/backend/models/schemas.py), [database models](../../pypsa-gui/backend/db/models.py).
- [Projects router](../../pypsa-gui/backend/routers/projects.py), [snapshot router](../../pypsa-gui/backend/routers/snapshots.py), [session project resolution](../../pypsa-gui/backend/services/active_project.py).
- [Approved project flow](../project-workflow/design.md), [modular cards](../project-workflow/modular-cards.md), [owner decisions](../../docs/agents/owner-decisions.md).

Continuation: use the audit baseline and proposed safe-revert/shared-assumption slices to refine implementation scope before a UI prototype. The original priority table is superseded by the audit sequence: state safety, connected existing features, shared assumptions, wider comparison, organization conveniences. This brainstorm does not authorize contact with reviewers, engine changes or deployment.
