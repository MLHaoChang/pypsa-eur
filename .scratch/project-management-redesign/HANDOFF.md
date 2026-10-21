# Handoff: project management audit and multi-variant assumptions

Baseline: master `a68db6cfcf254df17a5b0d96da7a4c995f26423f`, rechecked remotely at the end of this audit on 21 October 2026. Worktree `/workspace/gridspine-project-management-review`, branch `claude/project-management-review`.

Publication: draft specifications PR from `claude/project-management-review`, covering the entire project-management implementation and chatbot integration. The PR description and attached chat artifact identify its URL and current head. No runtime implementation changes or restore fixes are included; this is not merge-ready runtime code.

Read in order:

1. `04-IMPLEMENTATION-PLAN.md`, `implementation-plan.json` and `05-API-AND-UX-CONTRACTS.md`: complete scope, 15 dependency-bound packages, 98 required acceptance cases, proposed code/API/UI/test targets and 9 domain contexts. Every audited feature maps to implementation packages.
2. `03-CHATBOT-INTEGRATION-SPEC.md` and `chatbot-integration.json`: 12 capabilities, 6 chat slices and AI01–AI26; actual resolvers/edit cards/access/navigation/job tools reused.
3. `01-MASTER-FEATURE-AUDIT.md`: 24 features, current/spec/desired distinctions, F01–F10 and revised priorities.
4. `findings.json`, `master-feature-audit.json`: structured findings and per-feature baseline.
5. `02-SHARED-ASSUMPTIONS-SPEC.md`: canonical section-8 Variants drawer, guarded application/revert and BV01–BV20.
6. `VALIDATION.json`: exact test evidence, source hashes and limits.
7. `README.md`: original brainstorm, now explicitly superseded where the audit refines it.

Completed verification:

- 382 existing backend project/persistence tests: pass, zero skips.
- 187 existing backend execution/comparison tests: pass, zero skips.
- 224 existing frontend tests in 13 files: pass, no pending tests; mocks/fixtures, not live-browser E2E.
- Save/load standalone QA: 74 checks pass.
- Three new safe-restore expectations: all fail, confirming specific limitations. They are retained as audit probes, not fixed/skipped/relaxed tests.

The failed probes use throwaway authenticated projects and fresh HTTP read-back. They establish: (F01) before-restore protects saved files but misses unsaved live edits; (F02) later stress settings survive restoring a checkpoint where the sidecar was absent; (F03) later uploads survive a checkpoint where uploads/ was absent. Runtime source is untouched. A UI promise that current state is completely auto-protected is therefore too strong.

To reproduce probes: use the current repo's `pypsa-gui/TESTING.md`/conftest isolation, copy `probes/audit_restore_probes.py` to a unique temporary filename under backend/tests, run that explicit file, then remove only that temporary copy. Normal tests/conftest discovery supplies fixtures. Do not load tests.conftest twice as a plugin. In this environment the final backend runs required permitted networking for HTTP test machinery; pytest-xdist was absent, so run sequentially. No dependency installation needed. Offline data, thread/cache limits and throwaway app storage were used; never run against real projects/DBs.

Raw logs/XML/JSON: `/workspace/scratch/project-management-audit`. They are ephemeral, not required for the authored package, and not uploaded. VALIDATION.json pins their hashes and records final outcomes. Temporary frontend node_modules symlink is removed after the audit; shared dependencies untouched.

Revised product decisions:

- Reuse existing project/scenario/queue/A/B comparison/session-context infrastructure.
- Last-section browser resume already exists. Membership inheritance is at the root; edit locks are per project node/user. Workspace copy does not describe current behavior accurately.
- Variants and Scenarios are the same entity, not two inventories. Existing copies are independent: base edits do not propagate automatically.
- Put Edit assumptions across variants in section 8; management links/embeds the same table/drawer. No duplicate field editors.
- Start with one supported scalar family and at most eight explicit selected targets as a proposed UI operation bound. Existing sweep remains capped at four new cases; planned eight-case comparison is not permission to expand execution.
- Preview current/effective values and old→new changes, preserve differing variant assumptions by default, refuse ambiguous mappings and recheck revision/permissions/locks on apply.
- Durable operation journal, safe checkpoints, read-back/staleness and conflict-checked reverse patch precede one-click batch apply. No claim that DB rollback makes filesystem updates atomic.
- Edit, Run selected variants, View historical run, Restore saved version and Run a version in an isolated copy are separate explicit actions.
- Safety fixes before visual redesign; richer organization/portfolio/role features later.

Next work: PM00 rebaseline/reconciliation, then a bounded approved implementation slice from the concrete plan. This specs publication is not implementation approval or a waiver of current review gates. Re-read actual master, instructions/specs/owner decisions and compare source pins. Reconcile W2/W7/S5/S6 and open PR #496; do not turn pending spec rows into shipped features. Existing exact-head engine/UX/steward review gates remain applicable before implementation. No reviewer contact or external approval was performed in this task.

Chat integration additions are specification-only. Start with exact context/read-only adapters alongside the redesigned overview; shared edits, version actions and group continuations wait for native contracts. Each actor/target restriction and ordinary approval remains authoritative, strict unmapped tools refuse, and no history import revives authorization. Tests run in the preceding audit do not certify new AI journeys.
