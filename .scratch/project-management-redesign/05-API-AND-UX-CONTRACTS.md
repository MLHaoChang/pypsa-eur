# Project management: proposed API and UI contracts

Status: concrete proposal for PM00–PM14, not existing endpoints or implemented screens. Baseline `a68db6cfcf254df17a5b0d96da7a4c995f26423f`. Reconcile names with current routers before building; actual authorization, typed refusals, context binding and existing route compatibility remain authoritative.

## Application structure

Global Projects opens the compact Resume/search/project list. Examples remains the existing creation/Builder entry; Library remains the existing source library. Opening a project resumes work directly when that is the explicit action; its management overview is also available through the project breadcrumb/menu. Do not force an overview detour on every switch.

Proposed management views share one project header, identity and context:

| View | Primary content | Primary action / exact destination |
| --- | --- | --- |
| Overview | Purpose/site/horizon, selected scope, section states, selected variant/result and unresolved next action | Continue setup opens the native owning flow section; View results opens exact result evidence. |
| Variants | Canonical section-8 variant table with tree toggle, declared differences and job/result state | Existing create/branch; Edit assumptions across selected opens the same drawer as section 8; Run selected previews exact targets. |
| Runs & versions | Distinct Runs and Saved versions subviews, with source links | View run is non-mutating; version restore/copy/run are explicit guarded actions, not implied by selecting a row. |
| Files & reports | Known input references, Library pins, reports and specialist outputs with source/freshness | Existing admitted read/export actions; no automatic raw-folder crawler or unsupported file interpreter. |
| Access & settings | Native membership inheritance, project-node lock and solve protection, metadata/lifecycle controls | Native authorized editor; archived/delete actions clearly distinguished; no new branch-private permission promises. |

Suggested route shape is `/projects/:projectId/manage/:view`, or an equivalent nested route within the current router. Existing `/projects` and workbench links remain compatible. Project/variant selection uses canonical authenticated IDs with a documented local-mode fallback, not a new identity store. Compare/version/run query references must be validated and remain distinct from the editable project.

A browser navigation event changes only intended view/context. Mutations, project switching, saving, copying, queuing and restoring have explicit native action contracts. Feature shortcuts open the canonical view/editor rather than add independently maintained controls.

## Visual and interaction blueprint

- Use current GridSpine brand/type tokens; cool neutral light/dark surfaces and bright coral accent. Consolidate warm glass/glow styles in this module; do not globally rewrite unrelated screens in the first slice.
- Desktop header: project breadcrumb/name, current variant, concise save/access state and one contextual primary action. Compact management tabs underneath; next-action card and current-evidence card first, variants/activity secondary.
- Project list has name/purpose, type/site, horizon, selected scope, family count, real modification/source status and row action. Cards show the same data, with optional existing site thumbnail/map/system preview. Missing images have a neutral fallback; no paid image generation or remote image requirement.
- Mobile tabs become an accessible view selector. Drawers occupy available width and allow row details to expand vertically; target names, old/new values, units and confirmation remain visible without hover.
- Single companion stays bottom-right without covering save/confirm/next controls. Context strip shows project/variant and discussed run/version; accessible Explain this shortcuts use the same dock. Draft changes open native preview cards/drawers.
- Keep existing server outcome semantics. Readiness, execution, evidence freshness, lifecycle and access are separate visible meanings; “complete” or “ready” is never certification.
- A saved version row uses Latest saved, captured source/time and included/excluded coverage. Current working copy is a separate object. Mark exact equality only if a native source comparison establishes it.
- A restored/changed/failed operation displays read-back or recovery status rather than optimistic success. Do not dismiss the only error on a short-lived toast; retain operation details and a safe next action.
- Respect existing theme/density and reduced-motion preferences. Key actions are keyboard/touch accessible, focus returns to origin, and status announcements are targeted rather than rereading an entire project list.

## Proposed service-facing API surface

The following routes are proposed v1 contracts, not claims of current availability. Reuse an existing endpoint instead when it already has these exact semantics. Register static resources before catch-all project-name routes, validate request models with unknown fields refused and enforce guards in native services as well as route dependencies so in-process tools cannot bypass them.

| Proposed route | Request / response essentials | Mutations / refusal boundary |
| --- | --- | --- |
| `GET /api/project-management/projects` | Bounded cursor/page size, authorized filters/sort; project IDs/parent refs, purpose/site/horizon refs, timestamp semantics, family counts, missing states, action capabilities and available source refs | Read only; no project activation/hydration, no inaccessible-name leak. |
| `GET /api/projects/{id}/management` | Bounded overview of canonical scope/section readiness, current selected source refs and permissible next targets | Read only; unavailable/error differs from empty or zero. |
| `PATCH /api/projects/{id}/management-metadata` | Expected metadata revision and admitted shared fields (description/tags/site label refs) | Validate/lock/recheck; no arbitrary model/config keys. Native sidecar/DB single source and portability policy required. |
| `PUT /api/project-management/preferences` | Expected preference revision; current user's pins/filter/default view/last context refs | Personal scope, not project bundle data or authorization; stale writes conflict. |
| `POST /api/projects/{id}/checkpoints/preview` | Intended capture source (live/saved), expected input revision, dirty/pending state and desired restore domain | Preparation only; authorized summaries, explicit unsaved resolution and capture limits. No implicit model save. |
| `POST /api/projects/{id}/checkpoints` | Bound preview digest, request ID and required native confirmation | Capture/read-back protected manifest checkpoint; existing saved-snapshot API retains documented compatibility rather than silently changing meaning. |
| `GET /api/projects/{id}/versions/{version_id}/view` | Exact authorized source and bounded supported section | No active-state mutation or current-result fallback; unsupported project/artifact refuses. |
| `POST /api/projects/{id}/restores/preview` | Exact version/capture-domain plus expected current input revision | Input/manifest differences and dirty protection requirements; preparation only. |
| `POST /api/projects/{id}/restores` | Preview digest, request ID and ordinary confirmation | Durable guarded restore/recovery receipt; not an implicit solve. |
| `POST /api/projects/{id}/versions/{version_id}/variants` | Reviewed isolated target name, exact source hash/version and request ID | New branch with source manifest, no overwrite of working design; running remains separate or an explicitly reviewed admitted composite. |
| `POST /api/projects/{id}/variant-changes/preview` | Explicit target IDs, canonical field/entity descriptors, typed values/unit/basis, expected revisions and common/overwrite policy | Read/isolated preparation only; include conflicts and preserved differences. |
| `POST /api/projects/{id}/variant-changes` | Immutable preview digest, idempotency key and ordinary confirmation | Native PM08 journal, per-target current authority/locks/revisions, checkpoint and read-back. No active-project switching loop. |
| `GET /api/projects/{id}/operations/{operation_id}` | Exact owner/family-authorized operation and bounded target receipt pages | Read only; historical observation never renewed authority. |
| `POST /api/projects/{id}/operations/{operation_id}/revert-preview` | Expected recorded post-state and current target revisions | Reverse-patch conflicts/preserved later work; no writes. |
| `POST /api/projects/{id}/operations/{operation_id}/revert` | Bound reverse preview, new idempotency key and approval | Guarded reverse operation with own receipt; not unconditional snapshot replacement. |
| `POST /api/projects/{id}/variant-runs/preview` and `POST .../variant-runs` | Exact selected IDs, supported requested kinds/source revisions, caps, preview digest and confirmation | Separate approved execution admission and group receipt; no jobs from preview or edits alone. |
| `GET /api/projects/{id}/comparisons` | Exact base/case source refs, metric/window/basis and bounded selection | Native A/B first; base+N only when S6 service admitted; incompatibilities explicit, no computation by model. |
| `POST /api/projects/{id}/lifecycle/preview` and `POST .../lifecycle` | Archive/unarchive family scope, expected revisions, active-work/dirty consequences and approval | Native PM13 service only; archive never disguised delete/cancel. Existing delete endpoint/confirmation remains authoritative. |

These surfaces represent native service operations, not one required chat tool per endpoint. Keep preview/status reads separate from effects and use existing confirmation policies. Avoid double approvals for duplicate representations of the same exact native effect; do not silently combine distinct edits, copies and solves into one vague approval.

## Request and receipt invariants

Every effect resolves the authenticated actor, organization and canonical target IDs independently. Exact expected input/policy/metadata revisions are compared at the moment the service requires, not merely when the dialog opened. Error bodies follow the repository's typed `{error_kind, message}` pattern and contain safe context without secret values or unauthorized target names.

Native pending-preview storage is actor/project scoped, bounded, expiring and content-digested. A preview token is not itself an approval; ordinary approval remains tied to the immutable native action. Retries with unchanged request IDs return the same receipt; changed payloads conflict. No route becomes a raw arbitrary filesystem or JSON-pointer writer.

The operation journal records stable ID, kind, origin, actor/family, requested target set, approved input digest, per-target pre/post/checkpoint refs, state, timestamps and safe failure/recovery references. Result metrics stay in native run evidence, not duplicated in the operation journal. UI cards, chat receipts, exports and Activity reference the same operation/result identities.

## Migration and portability policy

- Prefer additive nullable/defaulted DB columns or a versioned named sidecar with one clear owner; do not split the same metadata across DB/JSON without explicit reconciliation rules.
- Personal preferences/restrictions and approval credentials do not travel in project exports or scenario copies. Shared input/version/lineage receipts follow the declared portable domain, bounded and ownership-revalidated on import.
- Audit F10 before claiming sensitivity/operation/run history is complete in an exported bundle. Hashes/references to absent evidence must read unavailable, not fabricated ancestry.
- Preserve old snapshot/bundle import. Known absence can be restored exactly only when a trustworthy manifest records it; an old snapshot without that evidence gets a limited-policy path or refusal rather than guessed deletion.
- Projects without network.nc need an explicit kind adapter. Do not create an empty network to force compatibility with a network-only checkpoint route.

## Native action/assistant relationship

| User action | Native surface | Chat behavior |
| --- | --- | --- |
| Find or resume a study | Project list/resume | Authorized exact target and native navigation; no-key generic shortcuts retained. |
| Explain readiness or a result | Overview/result card | Same server reasons or native evidence; unsupported checked facts withheld/refused. |
| Change an assumption | Owning field / section-8 matrix | Prepare same native preview; ordinary confirmation and exact read-back. |
| Run selected variants | Reviewed run-group request | Same supported kinds/limits and exact target set; no implicit submission after discussion. |
| View old result | Exact run/version reader | Read only; no restore, project switch or current-data substitution hidden in explanation. |
| Revert operation / restore version | Native recovery/version surface | Clarify scope, protect dirty work and use native verified effect; unavailable safe path stays unavailable. |
| Close/interrupt chat | Companion controls | Stop speech/wait/assistant activity as supported; cancel-job is a distinct native action. |

The test mapping for these contracts is in [the implementation plan](04-IMPLEMENTATION-PLAN.md), with detailed shared-edit and companion cases in their specifications. API status names and frontend displays must map to current native contracts; proposed names above do not create a second global project-state machine.
