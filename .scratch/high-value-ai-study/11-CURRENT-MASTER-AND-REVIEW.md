<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Current master, residual delivery and independent review

Frozen final review source: `880496ef679af1efc51a6dd3d62f9e41a75f2937` (2026-10-22); initial native review source: `7a14d27038de424d07a832b16e2465297c7b8b7d`. PR #567 initial reviewed head: `36b5342ad5588ecad8400cd1b9af29a36864431b`. [Source hashes and outcomes](current-master-reconciliation.json) preserve each assessment separately from the historical 87546/322df/089aea baselines. Reassess subsequent master changes before implementing affected contracts. Do not merge master into this docs branch merely because it moved.

## What is delivered versus left

| Unit | Delivered foundations on assessed master | Residual implementation scope |
|---|---|---|
| HS-01 | Catalogue, bounded discovery/component schemas, native support dispositions, provider selection | Exact domain/service schema paging and freshness, complete route/editor dispositions, executable reachability tests under offer caps |
| HS-02 | LC-23 S1 ignores caller completion results and reads native receipts/jobs; existing edits and S2 queue settlement | Shared proof contract across normal finish, late worker, settlement and reconciliation; versioned durable proof, exact attempt/origin/finite metrics, crash/restart and legacy-proof hardening |
| HS-03 | Durable threads, append-only event-log fold, history bounds, on-demand procedures, provider-neutral stable prompt | Structured source-revalidated context packet and exact UTF8 packing/overflow contracts; preserve hard decisions and complete tool pairs |
| HS-04 | S8 `investment-case/1` NPV/unique IRR and `investment-lcoe/1` nominal/real LCOE; `site-import/1`; result registry/card mapping; H2a three-read/two-round explanation; merged AC-PF Link-dispatch fix | Coverage/plausibility records, additional independently checked native metrics, stronger interpretation/source guards. Payback/cover ratios/PPA price remain unchecked until their own adapters pass |
| HS-05 | LC-23 S1 24-step brief, phase pauses, input chain; S2 owned queued-job settlement, one/two-case admission, no implicit solve retry; existing user Continue path | Revision/boundary-bound hardening of **existing** `continue_task_id`/`continue_at_card`; bounded diagnosis/repair/verification; remaining plan-card UI and admitted report/family adapters |
| HS-06 | Inventory, matrix, finite checked pilot, native regression suites, replay and metrics | Per-case source/schema/artifact evidence linkage, adversarial journeys, bounded diagnostics and safe replay enforcement; no universal coverage claim |
| HS-07 | Packaged skills/workflows and registries already exist | Add tested native domain procedures only as their tools/evidence become available; no general shell or agent-team runtime |

Data-to-model's equipment spreadsheet mapping, manual mapping form and TS-04 refinement now also exist; preserve parsing, native schema/unit selection, preview, cell lineage and the under-lock approved-card digest/readback checks. PDF/datasheet/tariff/PPA extraction remains a separate admitted LC-16 follow-on. Safe restore save-first, bundle provenance and manifest guards merged independently: retain their tests rather than describing all restore defects as still open or claiming this harness PR fixed them.

The initial machine-readable unit DAG is traceability for residual contracts, not a requirement to rebuild shipped predecessors. Original 48 capability cases and historical 24 assessment checks stay historical plans; the current harness plan has **53 planned acceptance cases**, including six explicit loop cases and 14 individually identified family journeys. Counts overlap in purpose and must never be summed into a tested-coverage statistic.

## Review scope and disposition

The owner requested a separate agent. `/root/harness_567_independent_review` independently assessed the specs, fresh native source, owner decisions and official Claude Code references. Its initial verdict was **NO-GO on the original specs**. Four findings required changes:

1. Reconcile shipped features and remove the duplicate checkpoint/resume protocol.
2. Define bounded native validation/diagnosis/correction, persistent budgets and stagnation termination.
3. Require atomic native proof on every completion path, including normal/late/automatic paths and process loss.
4. Bind each family journey to concrete domain inputs/oracles and its own parameterized evidence node.

The amended 08/09/10 contracts and JSON address these findings. [Independent verdict and conditions](12-INDEPENDENT-REVIEW.md) record the separate agent's final assessment; pending is not approval. A design GO permits the residual specs to guide queued implementation. It does not certify shipped runtime, all domains, live-model reliability, remote CI, merge readiness or production use. Independent domain review and real-use calibration stay pending by owner choice.

## Claude Code practices translated into this harness

This is a comparison with public documentation, not a reverse-engineering claim about Claude Code internals or a product benchmark. Saved retrieval records/digests identify the exact reference text inspected; official documents may change later.

| Public reference | Relevant practice | GridSpine implementation contract |
|---|---|---|
| [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works) | Gather context, act, verify and course-correct; tools and permission boundaries | Retain ordinary dispatch; native proof before dependent progression; bounded validation/correction, no unauthorized automatic repair |
| [Hooks](https://code.claude.com/docs/en/hooks) | Lifecycle verification and explicit stop-loop protection (`stop_hook_active`, bounded continuations in documented hook flows) | Deterministic validation points and persistent diagnostic/stagnation bounds; **no downloaded hooks or user scripts**, no assertion these are Claude's exact limits |
| [Memory](https://code.claude.com/docs/en/memory) | Durable instructions/context require deliberate management | Source-backed working packet, provenance labels, hard-constraint retention and explicit overflow rather than silent omission |
| [Skills](https://code.claude.com/docs/en/skills) | On-demand procedures with explicit scope | Extend existing trusted registries; static named-tool/stop-condition validation plus native journey tests |
| [Sandboxing](https://code.claude.com/docs/en/sandboxing) | Execution boundaries outside model instructions | Existing ACL/locks/restrictions/approval/effect fences on every admission; no generic code worker required |
| [Subagents](https://code.claude.com/docs/en/sub-agents) | Bounded delegated scope and separate context | Use independent review for this design; general in-product agent teams remain deferred, not necessary for first gains |
| [Costs](https://code.claude.com/docs/en/costs) | Context/tool choices and usage observation | Native call/round/byte bounds and separate provider token meanings; unknown usage stays unknown, no invented dollar accounting |

An optional fresh request for `best-practices.md` returned HTTP403; it is not represented as retrieved evidence. The seven successful earlier reference downloads were independently hash-checked by the reviewer.

## Next implementation and gates

First extend HS-01's missing service-schema/disposition contracts in its existing package. The shared proof/bounded-loop hardening can follow under the current harness owner, preserving LC-23 S1/S2. Context and checked-result extensions follow their native dependencies; develop each unit's negative journey tests with it. Existing UI/engine review gates, owner queue and exact implementation-head CI/premerge remain mandatory. No new build package, paid model call, reviewer contact, runtime edit or merge is authorized merely by reading this handoff.

## Current foundation validation

Six existing suites on `7a14d27038de424d07a832b16e2465297c7b8b7d` passed: **142 tests, zero failures/errors/skips**, including LC-23 briefs/queue settlement, investment, site import, result views, equipment mapping and AC Link dispatch. The exact JUnit/source/hash are recorded separately; none of the 53 planned acceptance cases was executed. An initial restricted-network attempt stalled in local TestClient setup and was interrupted; the unchanged run passed with local networking enabled. No paid calls or test relaxation occurred. Inventory and matrix checks on7a14d27 passed:279 tools/17 result tabs/21 panels,170 controls/14 families; full coverage remains not established. Historical test counts keep their original sources and must not be summed.

## Later master reassessment

Master advanced to `1b1aa5ccc5dd4c8ca9fdf2ec680fd96cb5f687b5` via #583 during review. Its five-file delta adds an advisory mixed-money-year warning in Assets & sizing and treats hand-typed replacement costs as having unknown money year. There is no overlapping harness implementation change. Preserve that advisory behavior: acknowledging the warning is not inflation normalization or verified finance evidence, and it must not silently block ordinary Run. Proposed checked finance claims still require their own exact money basis. This is compatible with HS-01/HS-04; retain the incoming regressions. The142-test evidence remains pinned to7a14d27; targeted tests for the incoming cost-year change are recorded separately, not relabeled or summed.

The incoming cost-year/reference-application suites passed **30 tests with zero failures/errors/skips** on1b1aa5c. Their separate JUnit/source/hash are retained in the reconciliation record. The reviewer independently passed42 existing goal/budget seam tests on7a14d27; that result is also separate and does not establish the new LOOP contracts.

## Frozen final delta:880496ef6

A subsequent174-file delta to the frozen snapshot merged opt-in nominal/real LCOE checking (#481/#482), the equipment mapping form and approved-card digest recheck (#581), matrix-driven scope availability (#588), grouped results (#587), campus/connection placement and typed cost units. Preserve them rather than rebuilding them. LCOE uses retained energy/value/equity series and the engine SAM-generalised definition; old reports lacking series stay unavailable. Payback, cover ratios and solved PPA price remain withheld. Checked report support never overrides the run-separability matrix: unknown/non-runnable kinds remain unavailable as scope runs. Typed costs carry capacity basis, money year and grid-connection inclusion; conversions/refusals belong to the native loader, and old packs remain provenance-only when pricing is refused. PACE-3 opens only its three named follow-ons, not arbitrary new AI packages. Focused foundation/semantic tests on this snapshot are recorded separately from earlier results. Later changes require reassessment before implementation; this design review freezes here rather than making a continuous-current-master guarantee.

On the frozen880496ef6 snapshot,11 focused native/semantic suites completed with **447 passed and one existing empty-parameter skip**, zero failures/errors (139.887s). The skip is `test_every_known_gap_is_still_a_gap[NOTSET]`: no known gaps were parameterized, not a skipped required behavior. This is current foundation/semantic evidence, not execution of the53plannedacceptancecases or full regression. Inventory/matrix checks on880 passed:279 tools/17 result tabs/23 panels,170 controls/14 families; full coverage remains not established.
