<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Implementation agent: start here

Status: execution instructions proposed; no prototype accepted and no runtime implementation started

This guide turns the eight tickets into bounded assignments. Read [HANDOFF.md](HANDOFF.md) for live state, [execution-manifest.yaml](execution-manifest.yaml) for dependencies/test ownership, [decision-register.md](decision-register.md) for unresolved contracts, and [implementation-record-template.md](implementation-record-template.md) for the record each assignment must leave. The normative behavior remains in spec.md, contracts.md, optional-site-workflow.md and ai-guided-project-flow.md. The manifest indexes these requirements; it does not replace or weaken them. A conflict is a stop condition, not permission to select the easier rule.

## First assignment and source preparation

Start with **SC-00 N only**, issues/01-prototype-and-contracts.md. Its deliverable is a bounded native prototype, frozen contracts and reproducible evidence. Do not attempt the entire feature in one implementation PR. No Blender/photo installation is necessary to complete N. The current package is available from PR #711's branch until merged; it is not assumed to be on master.

1. Fetch actual master and inspect the spec PR's actual head/state. Record both full SHAs and the exact spec revision you read. Read AGENTS.md, owner decisions, the outsourced register and steward from current master, and harness/README.md before assistant work. Follow current repository instructions if these paths move.
2. If #711 is unmerged, read its docs separately; start an implementation branch from actual master, not the spec branch. Identify how the approved spec will be made available for the PR's review without pulling unrelated unpublished code into it. Coordinator owns merge/review routing; this document does not grant it to the implementer.
3. Inspect the active open-PR file list and register for conflicts. Pin the current owning functions, DTOs, tests and file allowlist in the assignment record. implementation-plan.md contains candidate paths, not an approved blanket allowlist. Do not edit another task's branch or generated JSON by hand.
4. Follow steward for conflicts/overlaps; master movement alone is not an instruction to merge it into an active green branch. Do not rebase, amend, force-push or merge a PR. Preserve other sessions' work.
5. Obtain applicable engine/UX/harness plan review through the owner/coordinator before implementation. Advisory design GO and existing checks on #711 do not grant runtime approval. Do not contact reviewers on your own.

## Contract freeze before dependent code

For SC-00 N, inspect native APIs, write minimal failing contract/prototype probes, and exercise the native seams needed to close D01–D05/D08/D11–D12. Record exact DTO/error examples and measurements, not just prose conclusions. Prototype code remains bounded and disabled from production until admitted through ordinary review. For SC-00 N, "disabled from production" means: probes live under the owning `tests/` directories, or in a module that no router or catalogue imports. The SC-00 N PR adds no mounted route, no chat tool, no `pypsa-gui/tool-error-kinds.json` kind and no change to the assistant inventory, so `pypsa-gui/backend/smoke/assistant_inventory.py --check` passes unchanged. Scene tools enter only in SC-07, under `pypsa-gui/backend/harness/README.md`. Implementing all V01–V98 is not a prerequisite for accepting a contract prototype.

Each decision closure includes source paths/symbols, alternatives considered, an accepted contract, exact positive/negative fixture and command/output, and the accepting review reference. If the evidence is unavailable, record blocked with the reason. Never label a decision accepted merely because an agent agreed with itself. D06 identity can remain open after N, but SC-02 cannot start until it closes; D07 is a separate SC-03 gate. If either needs additional proof, run a bounded decision/prototype assignment and record acceptance before the dependent runtime unit; do not start that unit and guess the contract. Optional B/P choices cannot keep accepted N open.

## Execution loop for every later assignment

1. Select one manifest unit whose dependencies have accepted evidence. Copy the assignment record; freeze its file allowlist, route contracts, affected case IDs and explicit non-goals against fresh master. Record all applicable cross-cutting assertions, not just the primary-owner cases.
2. Write failing tests at the frozen public seam. Save command, exit code and failing assertion; demonstrate the failure is caused by missing intended behavior. Tests belong beside the existing owning service/component tests. Proposed test names in this package are not claims those files/functions already exist.
3. Implement the smallest end-to-end part through real native services. Use substitutes only for external acquisition, worker invocation and scripted-provider control. Do not mock native authorization, admission, persistence, algebra, result projection or receipt creation in the integration tests that claim those guarantees.
4. Run focused tests, relevant existing regressions and required semantic/generated checks. If a failure also occurs on master, preserve exact head/master reproduction and report it; never skip, xfail, loosen a check or add a timeout simply to obtain green. Follow steward if the same failure recurs.
5. Exercise the visible Guided/Expert path, manual/no-provider path, denial and stale-state recovery. Capture evidence on redistributable synthetic data. UI changes need actual browser/device evidence, not only jsdom snapshots. Honor existing tokens, dense UI and one companion.
6. Add the release fragment, update case evidence and decision references, and open/update one bounded draft PR. Report full head SHA, not only a branch name. Any push needs current-head CI and applicable renewed review; historical green checks do not certify a replacement.
7. Run current scripts/coord/premerge.sh immediately before reporting green as steward requires. Coordinator merges. Record accepted merged prerequisite SHAs before handing the next unit to another agent. At handoff, report unfinished work and the exact next failing test rather than claiming an entire ticket complete.

## Native delivery sequence

SC-00 N → SC-01 with SC-07 foundation → SC-02 with SC-07 bindings → SC-03 with SC-07 results. The foundation/bindings/results units partition SC-07 for sequencing; they are not new user-facing modes or a reason to defer all AI work to the end. Each vertical slice supplies its own guidance/context/fallback. Merge related work sequentially where files overlap.

SC-04 can follow stable native scenes without Blender. Its real Blender compatibility claim separately needs B. SC-05 needs B and SC-04. SC-06 layout needs the native proposal lifecycle/file policy; photo needs independent P evidence. Do not block native delivery on optional hardware. No optional engine is silently enabled when installed.

Native integrated release requires accepted SC-01/02/03 and all three SC-07 units, applicable native portions of the cross-cutting cases, all eight domain fixtures, packaged-device measurements and normal review/CI. A whole V row involving optional engines remains partial until those assertions are actually exercised. Report assertion-level status; never claim all 98 passed from native-only evidence. Blender/photo release adds their genuine engine evidence and affected cross-cutting assertions.

## Stop conditions and safe continuation

Stop the affected assignment and document a scope/contract question when the brief contradicts source, a necessary file is outside the frozen allowlist, target identity cannot be proven, quantities lack native meaning, a schema requires a new native model capability, or independent evidence cannot establish a required gate. Continue unrelated documentation/diagnosis only. A UI/editor fallback is an explicit supported fallback, not evidence that an unavailable AI action was implemented.

Do not create dummy electrical entities to unlock a scene, infer demand/parameters from geometry, add a second topology store, use approximate names for identity, add a second executor, or send private originals to models/workers by default. Optional-engine execution belongs only to an explicitly assigned B/P or corresponding capability unit with its packaging/resource contract; it never becomes a native prerequisite. Do not invoke paid providers, deploy, or claim engineering compliance under this specification assignment. Independently reviewed engineering/domain approval and real-use calibration remain pending.

## Paste-ready bounded assignment

> Implement unit `<unit_id>` from `.scratch/site-scene-generation/execution-manifest.yaml` using spec revision `<full_sha>` and the linked ticket. Start from fresh master; read AGENTS/steward/outsourced register and this execution guide. Verify each prerequisite's accepted evidence and decision closures before code. First record the actual base, approved file allowlist, frozen DTO/errors, applicable V assertions and required review gates using implementation-record-template.md. Write the failing public-seam tests before implementation. Keep native services authoritative, ordinary approvals/receipts and manual parity. Prototype units close their own decisions for acceptance; only runtime units require their decision gates closed before code. Do not implement other units, install optional dependencies outside the assigned packaging contract, make paid calls, contact reviewers or merge. Open/update one bounded draft PR with exact tests, fixture hashes, exclusions and next task. If source conflicts with the spec or evidence is missing, stop that work and state the issue instead of guessing.
