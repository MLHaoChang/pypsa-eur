# Project management: master feature audit

Status: assessment; runtime defects remain unfixed. Baseline master `a68db6cfcf254df17a5b0d96da7a4c995f26423f`, 21 October 2026.

This audit distinguishes actual specifications from proposed product expectations. A passing existing test establishes its tested contract, not the completeness of the whole feature. Newly added audit probes deliberately assert stronger rollback expectations; their failures are recorded separately from existing regression results. See `VALIDATION.json` for actual commands, counts and limitations.

## Assessment basis

Existing contracts:

- Approved project bundle design: `docs/superpowers/specs/2026-07-26-project-bundle-load-export-design.md`.
- Project-write safety design: `docs/superpowers/specs/2026-08-14-project-write-safety-design.md` (including explicit deferral of trash).
- Undo router contract: `docs/superpowers/specs/2026-09-13-network-undo-router-lift-design.md`.
- Unsaved result visibility: `docs/superpowers/specs/2026-08-27-unsaved-results-visibility-design.md`.
- Approved generic project flow: `.scratch/project-workflow/design.md`.
- Reconciliation and sensitivity plans: `.scratch/project-workflow/workspace-reconciliation.md`, `integration-sensitivities.md`, `modular-cards.md`. These contain planned slices; their presence is not implementation evidence.
- Current terminology: `pypsa-gui/CONTEXT.md`; Scenario is a Project row with a parent; saved snapshots, solver time snapshots and undo snapshots are distinct.

Expected product quality: find the intended study; change the intended model; know what ran; inspect the exact result; restore a declared checkpoint without silently mixing later state; keep project/access isolation; make comparisons only on an explicit basis. Where that expectation is stronger than the current spec, this audit names it as an extension rather than a regression.

## Feature-by-feature baseline

| ID | Feature and expectation | Actual master behavior / verdict | Evidence and remaining work |
| --- | --- | --- | --- |
| PM01 | Discover projects and identify recent work | **Partial.** Accessible root cards, missing-folder state and Resume exist. Recency uses creation time; UI calls it Updated. No home search/filter/list/pin/archive feature. | Home helpers and frontend tests; F06/F09 below. Search/pins are new UX, not failed existing requirements. |
| PM02 | Resume correct project and section | **Exists with limits.** Last-project resume exists; active section is already persisted per project in browser storage. Guided/Expert share section context. | `store/activeSection.ts`, `uiStore.ts`, project-actions tests. Do not propose building last-section resume from scratch. Browser key is project name, not a server-owned cross-device user/org preference. |
| PM03 | Create blank/example/imported/duplicate project | **Implemented for existing paths; broader Builder remains planned.** Wizard supports blank/template/file/clone; specialized studies and local folder behavior remain mode-specific. | Wizard clone/home creation tests; creation/template backend paths reviewed. No full live-browser test of every example in this audit. Reuse existing paths and reconcile W2/#496, rather than invent another creator. |
| PM04 | Save/autosave and reload model/config/results | **Substantial tested foundation.** Identity guards and per-file atomic writes; sidecars/config/results-state and user input series travel through explicit bundle paths. | Save guards, reload, sidecar tests and standalone save/load QA. A sequence of atomic file writes is not an all-or-nothing multi-file transaction. Browser autosave timing is not a full UI E2E result here. |
| PM05 | Switch projects without losing/misrouting state | **Implemented and targeted-tested.** Session-bound contexts, resident registry, save/switch logic and undo separation. | Project-switch/undo/session tests; frontend switch tests. Do not claim stale process-global warning is current architecture. Same-user multiple tabs and pending requests remain important release scenarios. |
| PM06 | Create independent variant from intended source | **Implemented with explicit saved-source semantics.** Authenticated scenario creation copies the base's on-disk bundle. UI saves an active source first. Parent link does not make a live inherited configuration. | Scenario-type/tenancy/bundle tests; `projects._create_scenario_db`. Route docstrings referring to an in-memory fork are stale. Later base edits do not automatically propagate. |
| PM07 | List and navigate nested variants | **Implemented; summary needs correction.** Scenario forest handles nested branches. Home count helper counts direct children only. | Forest/helper/frontend tests. Define direct-child vs family count; avoid flattening ancestry or creating a new variant table detached from existing project IDs. |
| PM08 | Edit variant category/description | **Implemented.** Scenario category is typed metadata; old description prefixes are migrated/tolerated. | Scenario-type/tenancy tests. Category baseline is a label, not a unique enforced comparison-baseline pointer. Generic root descriptions/client/tags remain new metadata work. |
| PM09 | Run one project or several variants | **Implemented for native queue solves.** Scenario tree can queue a branch/subtree with confirmation, active-project save handling and duplicate protection. Queue captures config and stores job states. | Scenario panel/frontend tests; queue config/dedupe/authz tests. This does not certify a generic all-modules Run for every variant; the newer orchestration/scope work must supply that. |
| PM10 | Cancel/retry/recover interrupted jobs | **Implemented with tested controls.** Cancel queued/running work, terminal status, explicit requeue, persisted listing and boot reconciliation. | Queue suites. Interrupted work is not silently called completed. Queue dismissal is a view/history action, not restore or deletion of every result artifact. |
| PM11 | Undo recent edits | **Implemented for a limited write surface.** Per-context in-memory network/user-series undo, default 20 steps/500 MB and burst coalescing; save/load/reset can change the undo baseline. | Undo tests and middleware source. Not a durable history of commercial/config/sidecar changes, not batch rollback across variants, and not a universal redo/restore facility. |
| PM12 | Save version/checkpoint | **Implemented for last-saved bundle files.** Source is on disk; explicit UI copy says Save first for unsaved edits. Maximum 50 saved snapshots with pruning. | Snapshot and bundle tests; router source. Not all project kinds/artifacts are covered: current snapshot route requires network.nc. Newest snapshot is labelled current without proving equality to the working model (F05). |
| PM13 | Restore a checkpoint and recover previous working state | **Fails stronger safe-revert expectation.** Successful saved-state restore/rekey is covered, but three fresh HTTP probes expose omitted unsaved changes and post-checkpoint state surviving. | F01–F03 reproduced. Current UI safety-net wording overstates protection. No single-click safe full-project rollback should be promised yet. |
| PM14 | Compare exact saved version without restoring | **Missing exact-source integration.** Snapshot-row Compare opens general saved-project A/B rail without the clicked snapshot ID. | F04 source-confirmed. Need source-bound version reader and request, or remove/rename misleading action. |
| PM15 | Compare two saved projects/variants | **Implemented, detailed and tested; comparison basis incomplete.** Ten result tabs, availability states and numerical invariants exist. Scenario-row comparability only checks snapshot count; global Best objective spans the supplied project list. | Compare suites and F07. Keep the engines; add basis metadata and scoped ranking rules. Equality of counts is insufficient for a like-for-like comparison. |
| PM16 | Compare base plus many variants | **Planned, not complete native feature.** Existing compare is A/B. LC-18 and S6 specify base plus up to eight cases, software deltas and incompatibility reasons. | Source/plan comparison. Do not mark spec text as shipped. No need for a competing new comparison engine. |
| PM17 | Export/import complete declared project bundle | **Implemented for an explicit file/dir set.** Network/results/config and named sidecars, uploads/reports/studies/sites travel; licensed texts excluded. Raw network import is narrower. | Bundle/campus/Library tests and save/load QA. Document included/excluded artifacts. Sensitivity receipt, experiment history and retained run evidence are not universally covered by that allowlist (F10). No “everything in this folder” guarantee. |
| PM18 | Rename/delete without breaking descendants or active work | **Implemented with confirmations/guards and tests.** Stable registry ID, rename updates, descendant/cascade checks, queue/lock refusals; deletion is permanent. | Tenancy/frontend/save-guard tests. Trash/soft-delete was expressly deferred by existing spec; Archive is new scope, not an incomplete trash implementation. |
| PM19 | Sharing, locks and tenant isolation | **Implemented and tested with specific semantics.** Membership is inherited through the root tree; locks are project-node/user keyed, not a single tree lock. Session-bound context resolves the active project. | ACL/lock/tenancy tests. Workspace copy incorrectly describes root-owned lock/global active slot. No branch-private ACL or real-time same-project coediting claim. |
| PM20 | Readiness and result freshness | **Implemented for native section/run contracts, still uneven across management.** Flow items target owning fields; acknowledgements are value-bound; result evidence is source-bound. | Project-flow and retained-evidence tests. A non-null objective is not enough to say all selected work is fresh or checked. A management summary must project canonical states, not invent a global green percentage. |
| PM21 | Reports/files/activity management | **Pieces implemented, not one complete project index.** Existing report store, uploads, sidecars, logs and retained evidence are separate sources. | Bundle/report/queue paths read; no full Files-view or audit-trail UI E2E exercised. Build an authorized summary/index; do not create a duplicate file or result store. |
| PM22 | Generate sensitivity variants from parameter changes | **Implemented in a bounded tool seam.** `run_sensitivity_sweep`: 1–4 new child cases, 1–20 changes each, six component classes, explicit attribute allowlist; isolated preflight, saved base, receipts/fingerprints, queue/reuse and partial outcomes. | `workflow_tools.py` and tests. Static updates preserve time-series overrides. Not arbitrary tariffs/finance/horizon/process writes; not existing-variant bulk edit; not proof all higher-level conversational LC-17 work is complete. |
| PM23 | Change one assumption across existing variants | **Not implemented as proposed.** Component bulk-update operates within one active model. Sweeps create cases; tree Run queues solves. Neither provides reviewed multi-variant common-assumption application. | Search/read across routes, scenario UI, workflow tools and sensitivity receipts. Recommend the bounded feature in `02-SHARED-ASSUMPTIONS-SPEC.md`. |
| PM24 | Run an earlier version without disturbing the working design | **No dedicated first-class action found.** A saved version can be restored before running, but that mutates working files and has the F01–F03 limitations. | Snapshot and queue contracts. Prefer an explicit isolated branch from the selected version, then ordinary queue execution; requires provenance and supported version-copy path, not a restore hidden behind Run. |

## Findings and priorities

### F01 — restore safety checkpoint omits unsaved model edits (high, reproduced)

Authenticated test: save project and checkpoint, change a bus x coordinate from 0 to 25 through the network API without Save, restore the checkpoint, inspect the automatic before-restore network. It contains x=0, not x=25. The checkpoint protects saved files, not the unsaved working model. Undo is cleared by restore. UI says the current state is auto-saved, which is too broad.

The existing create-snapshot contract intentionally snapshots saved files; this finding does not redefine that endpoint. Required safe-revert improvement: detect unsaved work and either save/capture the exact live state before the safety checkpoint, or refuse/offer explicit discard with accurate consequences. Flushing pending frontend/debounced edits and server dirty state both matter.

### F02 — restoring an earlier absent sidecar retains later settings (high, reproduced)

Authenticated test: checkpoint a project with no stress-scenario file, add a valid stress scenario, restore the old checkpoint. The API still returns the later scenario instead of the original empty list. Restore skips absent files except special cases, matching a narrow documented legacy behavior but not a complete state-replacement expectation.

Use a versioned presence manifest to distinguish known absence at capture from an old/unknown snapshot format. Remove post-checkpoint files only for the declared restore domain, with safety backup and migration rules. Do not blindly delete arbitrary folders.

### F03 — restoring an earlier absent directory retains later uploads (medium, reproduced)

Authenticated test: checkpoint without uploads/, then add an audit-only upload file and restore. The file remains. The directory-copy helper skips absent source directories. The restore comments describe cleanup but the invoked helper does not remove an absent directory.

The same manifest/restore-domain policy must handle directories. Directory-copy errors are currently best effort, so no full multi-file/directory transaction or complete archive guarantee should be asserted without additional fault-injection/recovery work.

### F04–F10 — source-confirmed integration/UX limitations

- **F04:** snapshot Compare does not bind snapshot identity. Block exact-version comparison claims until a supported reader exists.
- **F05:** newest checkpoint is labelled current, though neither restore selection nor model equality is tracked. Say Latest saved version; show Working copy separately.
- **F06:** Updated/recency uses created_at. Distinguish created, saved/modified and personal last-opened; audit shared timestamp update coverage before exposing a new metric.
- **F07:** global Best objective is not a validated design ranking; row warning checks only snapshot count. Show explicit comparison groups and basis differences.
- **F08:** Workspace copy is stale about the active-network architecture and tree-wide lock. Preserve actual session isolation and project-node locking semantics.
- **F09:** root-card variant count excludes nested descendants. Name direct children explicitly or consistently count family descendants.
- **F10:** sensitivity_case.json is authored by workflow_tools but absent from the generic project bundle/snapshot file lists; experiments/ and run_evidence/ are not generic bundle directories. Verify desired export/clone provenance contract before expanding history claims. This is source-inspected, not an assertion that all current readers expose corrupt results.

## Revised recommendation relative to the brainstorm

Keep project overview, clearer variants/runs/versions and search/list design. Change delivery order:

1. **State safety and truthful semantics first:** F01–F03, exact checkpoint identity, capture manifest and restore-failure recovery; correct F04–F09 copy/navigation. Keep permanent-delete behavior accurate.
2. **Connect existing capabilities:** expose the existing queue, source-bound results, per-project resume and scenario forest in a coherent project summary. Do not rewrite these working services or build another workflow engine.
3. **Common assumptions across selected variants:** a first-class bounded parameterization feature in section 8, with preview, conflicts, checkpoints, operation receipts and explicit reruns. It can be opened from project management, but parameter ownership stays in existing editors.
4. **Wider comparison and isolated version runs:** extend S5/S6/LC-17/18 where appropriate; exact version source and comparison metadata first. Existing native A/B engines remain the numerical source.
5. **Organization conveniences:** tags/pins/archive and richer activity after the above; defer coediting, arbitrary role expansion, portfolio totals and generic PM boards.

A run failure does not by itself require reverting inputs: failed/cancelled jobs should leave an intelligible attempted result. Offer Restore inputs separately, protect current work and retain historical failed evidence. Viewing historical results never restores model inputs automatically.

## What this audit does not certify

No exhaustive proof of every route or module; no full remote CI/premerge report; no live-browser validation of all examples; no two-user browser coediting study; no restart/fault-injection transactional restore proof; no domain/compliance certification. Existing automated suites and the new targeted probes are separate evidence with explicit scope. Production data was not touched and no paid model/media calls were made.
