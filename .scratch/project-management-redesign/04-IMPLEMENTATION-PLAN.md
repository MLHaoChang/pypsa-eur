# Concrete implementation plan and required tests

Status: proposed; source baseline `a68db6cfcf254df17a5b0d96da7a4c995f26423f`. This PR specifies work and publishes audit evidence; it changes no application runtime. **15 bounded delivery packages, 98 required acceptance cases and 9 domain contexts.** These are design/test requirements, not a claim that 98 new tests already exist or have passed.

## Scope and fixed product decisions

Keep one project model and ten flow sections, with the supported modular-card scope (six including Hand-off per the current owner design). The management views are Overview, Variants, Runs & versions, Files & reports, Access & settings; they organize the same model rather than duplicate its parameter forms. Section 8 owns the variant/shared-assumption surface; shortcuts and companion actions open the same editor.

Keep cool neutral light/dark surfaces with coral-red accents, compact Resume, searchable Cards/List projects, useful overview/next action and one bottom-right GridSpine companion. Reuse current mode/section resume and native actions. Capture/restore integrity is repaired before offering safe one-action revert; read-only overview/chat work can proceed alongside that repair.

Existing copies stay independent; no implicit live inheritance. Initial shared edit: **Generator.marginal_cost**, scalar-only, admitted only when the canonical field/bus/carrier/unit/money-basis contracts match and a time-series override does not contradict the intended effect. It must use existing validators/writers; this is not an economic-engine redesign. Additional cost, tariff, finance, process and profile fields require their own declared adapter and tests. A missing/unsupported target is a scoped refusal, not guessed mapping.

One planned matrix may select up to eight existing variants; that does not raise the current four-new-case sweep cap. Actual run groups must obey their own admitted limits/readiness and ordinary approvals. No hidden autosolve, active-project switch loop, raw JSON path writer or model-computed comparison.

## Contract/data changes to implement

| Contract | Concrete content / authority |
| --- | --- |
| Checkpoint manifest | Version, canonical project/kind/source revision, declared capture domain, known-present and known-absent members, member hashes/types/sizes, excluded/licensed state and protected-operation references. Legacy missing manifest means unknown capture, not absence. |
| Restore journal | Exact source/target IDs and digests, protected pre-state, staged validated members, publish/recovery phase and terminal receipt. Multi-file filesystem publish is not made atomic by a database transaction. Block unsafe reads/writes while recovery is unresolved. |
| Management summary | Bounded authorized metadata, stable IDs/parent links, distinct timestamps and source semantics, family count, current native readiness/scope, selected job/result refs, missing/corrupt states and current action capabilities. No network activation/hydration per card. |
| Shared-change preview | Explicit target IDs, exact owning field/entity mapping, current effective values, typed proposed values/unit/basis, expected revisions, policy, preserved differences/conflicts and immutable preview digest. |
| Shared-change journal/receipt | Idempotent actor-scoped request, per-target pre/post digests/checkpoints, native read-back, affected result staleness, applied/failed/uncertain records and guarded reverse patch. No blanket success after partial writes. |
| Variant run group | Reviewed exact target set, requested supported kinds/resource caps, source revisions, job IDs and per-job outcomes. Durable at-most-once submission and eligible origin-bound completion; failure does not invent missing metrics. |
| Comparison | Exact source IDs/window, periods/weights/demand/objective/money/scope basis, declared input differences, native available values/deltas and incompatibility reasons. A/B readers reused; base+N requires an admitted native extension. |
| Companion context | Actor/org/project/variant plus separate editable, discussed-run, version and comparison references; untrusted per-turn UI hints re-resolved natively. Access summaries/history do not confer approval. |

API paths/type/class names for new contracts are proposed by each slice and frozen before implementation; the proposed file names below identify ownership, not existing callable endpoints. Migrations need additive defaults and explicit local/authenticated compatibility. Services own behavior; routes and harness adapters enforce the same native contract.

## Delivery packages and code/test targets

Each package becomes one isolated implementation issue/PR, splitting further if it cannot remain reviewable. Existing seams are reuse candidates, not permission to rewrite whole modules. New paths are explicitly proposed. Tests listed are **to add**, not files asserted to exist.

### PM00 — Freeze current contracts and coexistence with incoming work

**Depends on:** none.

**Existing seams:** `.scratch/project-workflow/design.md`, `.scratch/project-workflow/integration-sensitivities.md`, `docs/agents/owner-decisions.md`.

**Acceptance IDs:** .

**Done when:** Refresh master/source hashes, resolve overlaps with W2/W7/S5/S6 and #496/#428/#368, confirm restore-domain and action ownership; write scoped issue before production edits.

### PM01 — Safe dirty-state capture and protected checkpoints

**Depends on:** PM00.

**Existing seams:** `pypsa-gui/backend/routers/snapshots.py`, `pypsa-gui/backend/services/dirty_state.py`, `pypsa-gui/frontend/src/pages/SnapshotsPanel.tsx`, `pypsa-gui/frontend/src/utils/projectActions.ts`.

**Proposed files:** `pypsa-gui/backend/services/project_checkpoints.py`.

**Tests to add:** `pypsa-gui/backend/tests/test_project_checkpoint_capture.py`, `pypsa-gui/frontend/src/pages/SnapshotsPanel.capture.test.tsx`.

**Acceptance IDs:** SR01, SR02, SR03, SR04.

**Done when:** Reproduce F01; capture exact intended live/saved state with explicit dirty handling, pending-save flush and protected quota semantics. No unsaved edit lost behind a successful restore message.

### PM02 — Exact declared-domain restore and restart recovery

**Depends on:** PM01.

**Existing seams:** `pypsa-gui/backend/routers/snapshots.py`, `pypsa-gui/backend/routers/projects.py`, `pypsa-gui/backend/services/atomic_io.py`, `pypsa-gui/backend/services/project_registry.py`.

**Proposed files:** `pypsa-gui/backend/services/project_restore.py`.

**Tests to add:** `pypsa-gui/backend/tests/test_project_restore_manifest.py`, `pypsa-gui/backend/tests/test_project_restore_recovery.py`.

**Acceptance IDs:** SR05, SR06, SR07, SR08, SR09, SR10, SR11, SR12, SR13, SR14.

**Done when:** Reproduce F02/F03; versioned presence/hash manifest, legacy policy, scoped removal, protected rollback and durable recoverable publish. Corrupt/partial restore cannot report fully restored.

### PM03 — Authorized lightweight project-management summary

**Depends on:** PM00.

**Existing seams:** `pypsa-gui/backend/routers/projects.py`, `pypsa-gui/backend/models/schemas.py`, `pypsa-gui/backend/db/models.py`, `pypsa-gui/backend/services/project_registry.py`, `pypsa-gui/frontend/src/api/projects.ts`.

**Proposed files:** `pypsa-gui/backend/services/project_management.py`.

**Tests to add:** `pypsa-gui/backend/tests/test_project_management_summary.py`.

**Acceptance IDs:** PD01, PD02, PD03, PD04, PD05, PD06, PD07, PD08, PD09, PD10.

**Done when:** Document bounded DTO with canonical IDs, real timestamp/source semantics, family count, readiness/result refs and action capabilities; no heavy network hydration or accidental activation. Metadata migration only for admitted fields.

### PM04 — Redesigned project landing and overview

**Depends on:** PM03.

**Existing seams:** `pypsa-gui/frontend/src/pages/ProjectsHomePage.tsx`, `pypsa-gui/frontend/src/pages/projectsHome.ts`, `pypsa-gui/frontend/src/store/activeSection.ts`, `pypsa-gui/frontend/src/layout/flowNavigation.ts`.

**Proposed files:** `pypsa-gui/frontend/src/pages/projects/ProjectOverview.tsx`, `pypsa-gui/frontend/src/pages/projects/ProjectList.tsx`.

**Tests to add:** `pypsa-gui/frontend/src/pages/projects/ProjectOverview.test.tsx`, `pypsa-gui/frontend/src/pages/ProjectsHomePage.management.test.tsx`, `pypsa-gui/frontend/scripts/smoke-project-management.mjs`.

**Acceptance IDs:** UI01, UI02, UI03, UI04, UI05, UI06, UI07, UI08, UI13, UI14.

**Done when:** Compact Resume; search/list/cards; exact next action; neutral/coral themes; keyboard/mobile/reduced-motion. Use existing create/resume/edit entry points and authoritative state.

### PM05 — Connect variants, runs, versions, files and access views

**Depends on:** PM03, PM04.

**Existing seams:** `pypsa-gui/frontend/src/pages/ScenariosPanel.tsx`, `pypsa-gui/frontend/src/pages/SnapshotsPanel.tsx`, `pypsa-gui/frontend/src/pages/SolveQueuePanel.tsx`, `pypsa-gui/frontend/src/pages/WorkspacePanel.tsx`, `pypsa-gui/frontend/src/pages/CompareView.tsx`.

**Proposed files:** `pypsa-gui/frontend/src/pages/projects/ProjectManagement.tsx`.

**Tests to add:** `pypsa-gui/frontend/src/pages/projects/ProjectManagement.test.tsx`.

**Acceptance IDs:** UI09, UI10, UI11, UI12, UI13, UI14.

**Done when:** One canonical entry per action, same section-8 variant table, linked source/run/version identities and accurate access text. Remove global Best objective and current/newest ambiguity; no duplicated editor state.

### PM06 — Exact version inspection, isolated version runs and comparison basis

**Depends on:** PM02, PM05.

**Existing seams:** `pypsa-gui/backend/routers/snapshots.py`, `pypsa-gui/backend/routers/compare.py`, `pypsa-gui/backend/services/run_evidence.py`, `pypsa-gui/frontend/src/pages/CompareView.tsx`.

**Proposed files:** `pypsa-gui/backend/services/project_version_views.py`.

**Tests to add:** `pypsa-gui/backend/tests/test_project_version_views.py`, `pypsa-gui/backend/tests/test_project_comparison_basis.py`, `pypsa-gui/frontend/src/pages/SnapshotsPanel.compare.test.tsx`.

**Acceptance IDs:** VM01, VM02, VM03, VM04, VM05, VM07, VM08.

**Done when:** Read exact authorized version without mutation; supported version-copy plus ordinary queue run; richer comparison basis. Source absence refuses; no restore hidden behind View/Run.

### PM07 — Read-only common-assumption matrix and previews

**Depends on:** PM03, PM05.

**Existing seams:** `pypsa-gui/backend/services/workflow_tools.py`, `pypsa-gui/backend/services/project_acl.py`, `pypsa-gui/backend/services/project_locks.py`, `pypsa-gui/frontend/src/pages/ScenariosPanel.tsx`.

**Proposed files:** `pypsa-gui/backend/services/variant_changes.py`, `pypsa-gui/backend/routers/variant_changes.py`, `pypsa-gui/frontend/src/pages/projects/VariantAssumptionsDrawer.tsx`.

**Tests to add:** `pypsa-gui/backend/tests/test_variant_change_preview.py`, `pypsa-gui/frontend/src/pages/projects/VariantAssumptionsDrawer.test.tsx`.

**Acceptance IDs:** BV01, BV02, BV03, BV04, BV05, BV06, BV07, BV08, BV18.

**Done when:** Explicit bounded targets, canonical field/unit/effective-value mapping and preview digest; common-value vs explicit overwrite; unresolved differences stop Apply. No writes/solves in preview.

### PM08 — One admitted shared scalar edit, read-back and guarded revert

**Depends on:** PM02, PM07.

**Existing seams:** `pypsa-gui/backend/services/workflow_tools.py`, `pypsa-gui/backend/services/project_registry.py`, `pypsa-gui/backend/services/dirty_state.py`.

**Proposed files:** `pypsa-gui/backend/services/variant_change_journal.py`.

**Tests to add:** `pypsa-gui/backend/tests/test_variant_change_apply.py`, `pypsa-gui/backend/tests/test_variant_change_recovery.py`, `pypsa-gui/backend/tests/test_variant_change_revert.py`.

**Acceptance IDs:** BV01, BV02, BV03, BV04, BV05, BV06, BV07, BV08, BV09, BV10, BV11, BV12, BV13, BV16, BV18, BV20.

**Done when:** First field Generator.marginal_cost, scalar-only within matching declared bus/carrier/unit/money basis and no conflicting series override. Canonical validators/writers, expected revisions, deterministic locks, capture/journal/read-back and safe reverse patch; no new LP semantics or ad hoc unit conversion.

### PM09 — Exact-target runs and native base-plus-N comparison

**Depends on:** PM06, PM08.

**Existing seams:** `pypsa-gui/backend/routers/solve_queue.py`, `pypsa-gui/backend/services/solve_queue.py`, `pypsa-gui/backend/routers/compare.py`, `pypsa-gui/frontend/src/pages/CompareView.tsx`.

**Proposed files:** `pypsa-gui/backend/services/variant_run_groups.py`.

**Tests to add:** `pypsa-gui/backend/tests/test_variant_run_groups.py`, `pypsa-gui/backend/tests/test_variant_comparison_table.py`.

**Acceptance IDs:** BV14, BV15, BV19, VM04, VM05, VM06, VM08.

**Done when:** Admitted exact-target job group, per-case outcomes, caps and no duplicate replay; S6 native bounded base-plus-N table/exports computed by existing numerical readers. Eight-target editor does not raise four-case sweep cap.

### PM10 — Companion management context, summaries and navigation

**Depends on:** PM03, PM04, PM05.

**Existing seams:** `pypsa-gui/frontend/src/components/AssistantDock.tsx`, `pypsa-gui/frontend/src/components/ChatPanel.tsx`, `pypsa-gui/backend/harness/navigation.py`, `pypsa-gui/backend/harness/catalogue.py`.

**Proposed files:** `pypsa-gui/frontend/src/components/ProjectChatContext.tsx`.

**Tests to add:** `pypsa-gui/backend/tests/test_project_management_chat_context.py`, `pypsa-gui/frontend/src/components/ProjectChatContext.test.tsx`.

**Acceptance IDs:** AI01, AI02, AI03, AI04, AI05, AI18, AI23, AI25.

**Done when:** One visible project/variant/source context; preserve no-key generic shortcuts, exact resolution and owning-field navigation. Read only; no new authority from summaries or mount-only acknowledgements.

### PM11 — Companion shared-edit and safe-version action adapters

**Depends on:** PM06, PM08, PM10.

**Existing seams:** `pypsa-gui/backend/harness/edit_card.py`, `pypsa-gui/backend/harness/effects.py`, `pypsa-gui/backend/harness/catalogue.py`, `pypsa-gui/backend/services/chat_tools.py`, `pypsa-gui/frontend/src/components/EditCard.tsx`.

**Proposed files:** `pypsa-gui/backend/harness/project_management_adapters.py`.

**Tests to add:** `pypsa-gui/backend/tests/test_project_management_chat_actions.py`, `pypsa-gui/frontend/src/components/EditCard.variantChanges.test.tsx`.

**Acceptance IDs:** BV17, AI06, AI07, AI08, AI09, AI10, AI11, AI12, AI19, AI20, AI21, AI22, AI23, AI26.

**Done when:** Thin adapters to native operation services, full previews/read-back and per-target restrictions; deny/uncertain stops dependent runs. Strict unmapped tools refuse until a separate validated adapter is admitted.

### PM12 — Companion group completion, evidence comparison and input-mode parity

**Depends on:** PM09, PM10, PM11.

**Existing seams:** `pypsa-gui/backend/harness/continuation.py`, `pypsa-gui/backend/services/assistant_tasks.py`, `pypsa-gui/backend/harness/activity_history.py`, `pypsa-gui/frontend/src/components/AssistantActivity.tsx`, `pypsa-gui/frontend/src/components/AssistantDock.tsx`.

**Tests to add:** `pypsa-gui/backend/tests/test_project_management_chat_continuation.py`, `pypsa-gui/backend/tests/test_project_management_provider_parity.py`, `pypsa-gui/frontend/src/components/AssistantDock.projectManagement.test.tsx`, `pypsa-gui/frontend/scripts/smoke-project-management-chat.mjs`.

**Acceptance IDs:** AI13, AI14, AI15, AI16, AI17, AI18, AI22, AI24, AI25, AI26.

**Done when:** At-most-once eligible origin-bound completion without new approvals, native numerical evidence, explicit checked gaps and no active-project hijack. Type/dictate/live share the native gate; speech mocks do not claim hosted-voice validation.

### PM13 — Pins, tags, archive and bounded activity

**Depends on:** PM03, PM05, PM02.

**Existing seams:** `pypsa-gui/backend/db/models.py`, `pypsa-gui/backend/services/project_settings.py`, `pypsa-gui/backend/routers/projects.py`, `pypsa-gui/frontend/src/pages/ProjectsHomePage.tsx`.

**Proposed files:** `pypsa-gui/backend/services/project_lifecycle.py`.

**Tests to add:** `pypsa-gui/backend/tests/test_project_management_metadata.py`, `pypsa-gui/backend/tests/test_project_archive_lifecycle.py`, `pypsa-gui/frontend/src/pages/ProjectsHomePage.archive.test.tsx`.

**Acceptance IDs:** LF01, LF02, LF03, LF04, LF05, LF06.

**Done when:** Personal vs shared metadata migrations, explicit family archive policy and busy/dirty checks; permanent delete stays guarded, no invented Trash/full audit guarantee. Chat writes admitted only after native services exist.

### PM14 — Integrated release gate and implementation handoff

**Depends on:** PM01, PM02, PM03, PM04, PM05, PM06, PM07, PM08, PM09, PM10, PM11, PM12, PM13.

**Existing seams:** `pypsa-gui/TESTING.md`, `scripts/coord/premerge.sh`.

**Acceptance IDs:** all 98 cases and all supported domain journeys.

**Done when:** Every delivered requirement has passing independent native/fixture/browser evidence on the current source; failures, unsupported families, external review and real-use limits explicitly reported. Full applicable CI/premerge and exact-head UX/engine review under then-current rules; no production activation in this package.

## New acceptance cases beyond the shared-edit/companion matrices

BV01–BV20 remain in [the shared-assumptions specification](02-SHARED-ASSUMPTIONS-SPEC.md); AI01–AI26 remain in [the chatbot integration specification](03-CHATBOT-INTEGRATION-SPEC.md). The structured plan contains all 98 cases and maps each to its implementation owner.

| ID | Scenario | Required independent evidence |
| --- | --- | --- |
| SR01 | Unsaved model change before restore | Preserve the exact dirty live input state in a protected checkpoint or refuse before changing files; reproduce F01 through authenticated HTTP. |
| SR02 | Pending frontend/debounced field edits | Flush or explicitly resolve pending edits before preview/capture; no apparently saved checkpoint of an older server value. |
| SR03 | Checkpoint creation from dirty project | Explicit Save and capture or last-saved-only choice; source identity and included/excluded state are clear. |
| SR04 | Create checkpoint at quota / ID collision | Protected operation rollback points and chosen source survive pruning; collision has a typed bounded outcome. |
| SR05 | Sidecar absent at capture then created | Known-absent stress/asset/control settings revert to absence within declared domain; reproduce F02. |
| SR06 | Directory absent at capture then created | Known-absent uploads/reports/studies/sites domains follow manifest policy; reproduce F03 without deleting outside the domain. |
| SR07 | Sidecar/directory present in saved version | Restore exact captured content and required metadata; read back through fresh HTTP and registered context. |
| SR08 | Legacy snapshot with unknown presence | Migrate or offer explicit legacy limited restore; never infer known absence or promise exact full restore. |
| SR09 | Copy/read/write failure partway through capture/restore | Fault-inject each stage; do not publish complete success; keep old state/recoverable protection and explicit recovery status. |
| SR10 | Process restart during restore publish | Durable journal identifies pre/post/uncertain state, blocks unsafe normal reads/writes and supports idempotent recovery. |
| SR11 | Concurrent autosave, edit, queued/running work or lock takeover | Recheck source/target/lock/revision before commit; no interleaved mixed checkpoint or overwritten newer edit. |
| SR12 | Corrupt manifest/member/hash, traversal or symlink | Refuse before mutation; authorized containment, finite size and correct member type checks. |
| SR13 | Restore networkless specialized study | Either declared supported capture/restore adapter or clear unsupported result without partial writes or fallback network. |
| SR14 | Complete restore source/current-state reconciliation | Next authenticated request sees restored context; section/result freshness and provenance are correct; historical approvals/jobs do not revive. |
| PD01 | Authorized project summary listing | Only accessible roots/variants, stable identifiers and bounded pagination; no cross-org metadata/name disclosure. |
| PD02 | Creation vs modification vs personal last-open | Distinct source semantics; passive reads do not count as model edits; real relevant writes update/derive correct revision. |
| PD03 | Nested family count and cycles | Consistent whole-family or explicitly direct count; missing/cyclic parents bounded, no duplicate rows. |
| PD04 | Summary performance with large networks | No per-row NetCDF hydration or project activation; bounded metadata/evidence reads and explicit pagination. |
| PD05 | Missing folder, orphan temporary file and corrupt metadata | Different empty/error/recovery states; missing data is not zero or an empty valid project. |
| PD06 | Current result and readiness projection | Same canonical scope/readiness/evidence contract; objective presence does not establish all selected modules complete. |
| PD07 | Rename stable-link compatibility | Root/descendants, last section, compare targets, pending references and selected project resolve coherently. |
| PD08 | Organization identity and action capabilities | Safe non-admin display DTO; server-calculated allowed operations respect locks/read-only/busy state. |
| PD09 | New metadata migration and legacy/local compatibility | Old bundles/rows load with explicit defaults; no invented modification history or server-only requirement for local support. |
| PD10 | Family portability/source relationships | Export/import/copy retain declared version/batch/base receipts or explicitly mark unavailable; no fabricated lineage. |
| UI01 | Search/filter/cards/list | Find intended root or nested variant with 0/1/50+ accessible projects; persisted filters scoped correctly. |
| UI02 | Compact Resume | Actual existing last project/mode/section reused; missing/deleted/revoked target is not opened or replaced silently. |
| UI03 | Overview next action | Server reason plus owning editor; same project/mode/return position; no duplicate parameter form. |
| UI04 | Independent status meanings | Lifecycle, readiness, job state, result freshness and access remain distinguishable, including stale completed results. |
| UI05 | Project creation/examples boundary | Reuse wizard/Builder; integrated horizon; current supported template/scope choices only; no duplicate staged examples. |
| UI06 | Visual language and theme | Cool neutral light/dark surfaces, coral primary/selection styling, semantic errors, readable typography and consistent spacing. |
| UI07 | Keyboard/touch/reduced motion | Visible focus, semantic controls, accessible dialogs/drawers, touch actions and reduced motion; no hover-only primary actions. |
| UI08 | Responsive layout | Native desktop and mobile journey; no clipped target previews, destructive buttons or inaccessible horizontal-only actions. |
| UI09 | One canonical variant table/tree | Management and section-8 entries share data/actions; nested lineage and active/base/preferred distinctions preserved. |
| UI10 | Runs vs saved versions | Separate identity/action labels; Latest saved is not labelled current without equality; viewing retained results never restores inputs. |
| UI11 | Files/reports and access navigation | Known supported artifacts display source/coverage; inherited membership and per-node lock explained accurately. |
| UI12 | Single companion integration | One context-aware dock across management and workbench; native help/actions remain usable without model key. |
| UI13 | Empty/error/loading/busy states | Server failures and pending state explicit; navigation does not hide stale data under success badges. |
| UI14 | One owning entry per feature | No duplicate editors/buttons across Guided/Expert/management; shortcuts open the same canonical surface and return. |
| VM01 | Snapshot-row exact comparison | Clicked version ID selects a non-mutating reader or explicitly refuses; general project A/B rail is not a substitute. |
| VM02 | Read-only old-version inspection | No active-project restore/activation; source/member scope and unknown artifact coverage explicit. |
| VM03 | Run exact old version in isolated copy | Parent version/input hashes pinned, current design unchanged, ordinary readiness/queue approval and job receipts. |
| VM04 | Comparison with equal counts but different basis | Periods, weighting, demand, objective, currency/year/units and scope differences exposed; no unsupported like-for-like delta. |
| VM05 | Zero baseline, stale/failed/missing result | Native signed deltas only where valid; unavailable not zero; no best-project ranking across unrelated studies. |
| VM06 | Base plus N comparison | Admitted native service computes deltas/rank/exports; caps enforced, no LLM arithmetic or implicit solver execution. |
| VM07 | Version selection after rename/deletion/revocation | Stable authorized reference or source-unavailable, never newest/current fallback. |
| VM08 | Compare/export reproducibility | Native table, chart and download share exact source/window/basis; changed source invalidates incompatible views. |
| LF01 | Personal pins and saved filters | Scoped to current actor/org; no credential/approval inheritance or shared-browser leakage. |
| LF02 | Tags/root description/site metadata | Canonical metadata with validation; reuse site records; same values through all views and bundle policy. |
| LF03 | Archive/unarchive with descendants and active work | Preview family scope; deterministic queued/active/dirty handling; archive is not hidden delete/cancel. |
| LF04 | Archived restore/export and lookup | Explicit read/write rules; no background autosave resurrection or fallback to wrong active project. |
| LF05 | Permanent delete and recovery boundaries | Existing confirmation/descendant/queue/permission guards retained; no false Trash/Undo promise. |
| LF06 | Activity lineage and authorization | Only actual durable events/receipts shown with source; no complete-audit certification from partial logs. |

## Domain coverage

| Domain | Availability at baseline | Required journey / limit |
| --- | --- | --- |
| C&I / own site | Existing eh_site path | Create/resume, scalar common-input preview, variants/queue/result freshness; irrelevant Grid/Control options remain outside scope. |
| Data centre | Existing integrated horizon/growth path | Shared cost assumption, dirty checkpoint, weak-connection result and source-bound summary; no inferred transient/compliance result. |
| Hydrogen hub | Existing H2 example | Carrier/process units and demand unchanged by management edit; same-unit scalar edits only, missing Generator target refuses rather than guessing a Link. |
| Ammonia | Existing staged process example in reviewed source | Preserve production units/recipe and period mapping; no energy-sum or monetary-basis substitution; native module support drives available actions. |
| Island microgrid | Existing microgrid example | Joint backup/control assumptions remain native; failed/partial jobs show correct evidence; no annual reliability claim from short dynamics screen. |
| IPP / generation-only | Existing generation project where supported | No critical-load/control requirement invented; comparison exposes contract/time/currency basis; market-series references preserved. |
| Blank / raw-network / full bundle import | Existing creation/import paths | Network-only versus declared full bundle clear; meaningful empty states, variant mapping and no missing-equals-zero. |
| Planning→dynamics / networkless study | Existing specialized project kind | Summary/navigation/access supported; checkpoint/batch actions use explicit adapter or refuse unsupported kind without mutation. |
| Mining / future templates | No new template implementation in this plan | Generic management contracts apply when admitted; do not advertise an unavailable template, process/control engine or runnable combination. |

These are parameterized integration contexts, not nine new solvers. Native support/readiness decides which actions are enabled. No new mining template, detailed electrical engine, hosted speech system or external feed is implemented here.

## Test layers and execution evidence required

1. **Pure/isolated service tests:** manifests/presence and digest contracts, target mapping, typed values/units, source compatibility, operation journals and independent numerical oracles. Do not mirror production calculations or weaken existing assertions.
2. **Authenticated native HTTP/ordinary harness dispatch:** real project fixtures, DB/ACL/lock/session binding, pending-state changes, fresh-request read-back, exact target effects and all three F01–F03 regressions. Assert both expected changes and absence of unintended writes/jobs.
3. **Fault injection and restart:** corrupt/stale/missing source, crash after each durable phase, cancellation, full disk/copy failure and concurrent edit/autosave. Recovery must never imply atomicity that is not established. Protect rollback points against pruning.
4. **Frontend component/contract:** same server summaries and IDs, query invalidation, cards/drawers, scope/read-only/error states and no-key functionality. Identify mocked HTTP fixtures as such.
5. **Native browser journeys:** isolated authenticated backend plus actual frontend; desktop/mobile, keyboard/touch/reduced motion, Guided/Expert continuity, find/resume, branch/edit, batch preview/apply/revert, queue/failure/results and exact-version view/run. Proposed `smoke-project-management*.mjs` scripts reuse the existing smoke approach; use fake provider transports for deterministic chat. Native endpoints must not be replaced by fabricated success mocks in this layer.
6. **Provider-neutral harness parity:** both ordinary provider wires with fixture transports, no paid API calls; exact cards, policy/approval rechecks, source/target invalidation, group completion/no replay, strict unsupported refusal and numerical evidence from native readers. Voice event fixtures establish gate parity only, not hosted voice quality.
7. **Regression and release:** relevant existing suites plus all new cases for the slice; backend tests, frontend tests/typecheck/build, QA/pilot, catalogue/schema/route/frame/inventory/control checks and CodeQL as applicable. Final runtime PRs require actual executed backend shards and pilot/QA steps, not a path-filtered green job. Run `scripts/coord/premerge.sh <pr>` against fresh actual master immediately before an all-green/merge report under the then-current steward rules.

Use `pypsa-gui/TESTING.md` and current CI commands, frozen supported environments and temporary test storage. Backend: `PYTHONPATH=<repo> <test-python> -m pytest <slice files> -q -p no:warnings` (parallelize only when pytest-xdist is present). Frontend: `node node_modules/vitest/vitest.mjs run <slice files> --maxWorkers=2`, then the actual build/typecheck command from package.json. Do not install missing packages just to inflate verification, use real customer DBs or run paid probes. Test fixtures already isolate DB/project/app storage.

Existing baseline evidence: 382 project/persistence backend tests, 187 execution/comparison backend tests, 224 frontend tests and 74 standalone QA checks passed; three new safe-restore expectations failed. Those are audit results, not green implementation checks. Each future PR records exact source/commands/results and its remaining unsupported cases; never sum overlapping reruns into a larger coverage claim.

## Review, packaging and handoff

Obtain applicable repository engine/design/UX review when implementing gated semantics or UI; no reviewer contact or approval is represented by this specs PR. Exact-head premerge/CI remains mandatory for runtime merge decisions. Independent domain review, representative usability, hosted voice testing and real-use calibration stay pending.

Each slice handoff records current master/head, complete or pending acceptance IDs, source/compatibility decisions, migration/portability behavior, outstanding defects, verified commands and the next bounded slice. A plan checkpoint never transfers approval tokens or authority. PM14 is complete only for the delivered supported scope; deferred modules/families are named, never claimed fully covered.
