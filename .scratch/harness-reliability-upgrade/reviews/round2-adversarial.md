<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Independent final adversarial review — preliminary, not GO

Reviewed HEAD: `8c9bcc986f8b09f374565f65a0df6cb50dfec2dd`.
Reviewed content payload: `9f584bd85e65b9db0c1a7a25b936dc498c556b8713a47a9f797994376ce2ade4`.
Planning base: `60d8332d64b2edef1b204fb5980311e6102229a8`; inspected-source delta is separately pinned as `30c79cf750c42314eedd0597d06b1780b92a81fe`.

All 28 manifest-listed content-file SHA-256 values and the aggregate over the canonical JSON `files` array matched. No runtime, paid, network, solver or application test was run. Review scope is technical implementability and adversarial composition of the detailed plan, not runtime correctness or official GK/UX/domain/merge approval. Axis reports remain required before a final verdict.

## Material findings on this payload

**FA1 — Conflicting placement of the shared parent process lock.**

Locations: `issues/03-durable-call-budgets.md:67`, `issues/09-coordinator-delegation.md:80`, and shared order in `spec.md`.

HH-03 requires `durable_conversation._LOCK → directory transaction`. HH-09 requires the directory transaction before participating process/task/receipt leaf locks without excluding this same `_LOCK`. The current service uses `_LOCK` on many parent writers. A budget writer following HH-03 can own `_LOCK` while waiting for the directory transaction; an acceptance writer following HH-09 can own the directory transaction while entering a reused `_LOCK` helper. Neither can progress. The plan must choose one explicit order for this exact lock, propagate it to every parent consumer, and prohibit the reverse acquisition. Test the known budget/acceptance/checkpoint/resume path with deterministic barriers and lock-order instrumentation, including native-lock acquisition constraints. This is an implementation contract conflict, not a request for a new locking architecture.

**FA2 — Acceptance growth can consume the budget store's settlement capacity.**

Locations: `issues/03-durable-call-budgets.md:71`, `issues/09-coordinator-delegation.md:72`.

The budget contract limits the whole parent file to 1 MiB and explicitly reserves future terminal-record growth so admitted calls can settle. The acceptance aggregate, embedded in that same parent, independently permits 2 MiB and has no obligation to preserve the budget writer's reserved headroom. A call can be admitted while its parent fits; acceptance events can legally grow that parent past 1 MiB while the call runs; then a complete late receipt encounters an oversized parent and cannot satisfy the exact-file settlement contract. The plan needs compatible whole-parent and subsystem limits plus a common write check that preserves all outstanding attempts' terminal growth, including for non-budget writers. Add a deterministic boundary test that races acceptance/history growth with late usage, then proves settlement once and field preservation after restart. New work may refuse at capacity, but existing admitted liability must remain settleable without deleting obligations or relaxing bounded reads.

## Checked paths without an additional material finding so far

- The HH-10 input/output/preseal observation/section/terminal-manifest/final-scope/external-receipt chain has an explicit acyclic construction order and tamper/failure controls (IS32–IS33); the former round-one circular digest defect is resolved in this payload.
- The first numeric journey is a fixed-generator dispatch/capacity-design iteration, with independent 13 MWh / 760 EUR then 130 EUR arithmetic, a frozen economic goal, physical checks and separate K prerequisites. Missing K or unsupported broader physics blocks activation rather than being counted as a pass.
- Research proposal capability is separate from native application. The exact electrical Generator.p_nom setup adapter needs its explicit-card composition; active lineages receive proposals only and a new researched assumption requires exit/setup review/new lineage, retaining liabilities.
- Queued/running fences, cancellation, uncertain submissions, immutable candidate ownership, retained historical results and late provider usage do not authorize replacement work or reset limits.
- HH-09A is an independent authoritative CAS/receipt/reducer store before delegation, with user-origin intake confirmation, all requested-part mappings, exact review receipts and mandatory unresolved-item retention. Workers supply untrusted evidence, not closure authority.
- HH-08 preserves exact artifact bases, unsupported requested items and ordinary engineering/research approvals; its copies cannot manufacture missing scientific evidence or close the parent merely because exports succeed.

This record is preliminary. Both material findings require resolution and re-review against the resulting new content payload, and the independent Spec and Standards reports must be available before any final GO.
