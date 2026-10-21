<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Delivery, tests and value measurement

## Sequence and gates

Traceability IDs are slices inside existing packages, not new runtime modules or permission to start implementation. Preserve the owner's package order, design-first W10 and the normal UX/domain gatekeeper review before code. Start with one already-supported native path. Native adapter availability and all external gates below are required; a satisfied internal dependency alone does not make a slice runnable.

| Slice | Acceptance outcome | Existing owners | Internal predecessor |
|---|---|---|---|
| HV-S1 | Study brief and support mapping | card-workspaces H1/W9, start_task/LC-05, W0 | None; external gates still apply |
| HV-S2 | Reviewed native study traversal | LC-05, S7, native execution adapters | HV-S1 |
| HV-S3 | Durable resume and bounded delivery | LC-12, LC-19, LC-21 | HV-S2 |
| HV-D1 | Comparable sources and evidence coverage | LC-18/S6, W10 | None; external gates still apply |
| HV-D2 | Domain-specific native robustness | native study/adequacy engines, LC-20 | HV-D1 |
| HV-D3 | Bounded recommendation and next evidence | card-workspaces H2/S8, LC-21 | HV-D2 |
| HV-I1 | Source/schema mapping and validation | profile upload/TS-01/TS-04, LC-16 spreadsheet/equipment mapping, W9/W10 | None; external gates still apply |
| HV-I2 | Reviewed native apply and readback | native field/profile apply, S7 | HV-I1 |
| HV-I3 | Document-family extraction after spreadsheet mapping | LC-16 slice 2: PDF/datasheet/tariff/PPA mapping, native engine field adapters | HV-I2 |

Recommended relative sequence: keep existing quick wins in W9/W10/S7; extend the study brief and narrow structured mapping where their owning packages are ready; add approved traversal/apply; then native evidence comparisons and domain robustness; finally durable deliverables and LC-16 document extraction after spreadsheet mapping. User-group demand may move mapping ahead of study traversal. Do not hold all usability improvements for a speculative end-to-end agent.

Each implementation PR needs the acceptance cases mapped to its slice, applicable current native unit/integration suites, semantic/tool schema checks, and browser evidence for changed UI. Runtime changes must follow AGENTS/steward requirements and current CI/premerge; this research PR is not a green merge report. Use synthetic inputs and fake providers by default. An independently calculated oracle is required for numerical additions. A fake-provider pass checks harness behavior, not open-ended model quality.

Rollout is opt-in through existing readiness/configuration controls. Separate observe/propose from effectful actions. If an adapter is missing, keep its path unavailable with a native reason. Do not create a new per-feature budget/price accounting service to implement this proposal. Reuse existing caps, record actual usage where established, and report unknown estimates honestly. Pause/rollback uses existing runtime/provider readiness and source-compatible native records; do not delete receipts or claim generic input rollback.

## Planned acceptance matrix

The following 48 cases are future implementation requirements. None ran as new-feature tests in this PR. `implementation-plan.json` is the canonical ID/slice mapping; this table is rendered from it.

| ID / slice | Level | Stimulus | Required observable result |
|---|---|---|---|
| S01 / HV-S1 | contract | Objective omits critical-service definition, stage dates or target units. | Missing required fields shown as questions; no guessed values, edits, sends or submissions. |
| S02 / HV-S1 | integration | Goal requests unsupported frequency/dynamics or standalone AC without admitted dispatch. | Fresh native support reason; unsupported steps blocked, no substitute adequacy/compliance claim. |
| S03 / HV-S1 | browser | Open one card walkthrough in Guided, change a native field in Expert and return. | Single companion/entry; shared source reflected, focus/keyboard/reduced-motion usable; composer never auto-sends. |
| S04 / HV-S1 | integration | Card deselected but another selected run consumes its price/VOLL input. | Outside-scope results excluded; consumed prerequisites remain visible; no silent selection or parameter loss. |
| S05 / HV-S1 | contract | Provider proposes a plan above the current admitted finite cap, nested task control, project-rebinding or invalid forward refs. | Native admission refuses; bounded cap increases land only in LC-05 with tests, no hidden loops or crossing project scope. |
| S06 / HV-S2 | integration | In Guided and Expert, inspect exact write/run step then deny its required card; attempt auto-approval from brief acceptance. | Native write/queue counters stay zero; every write/run needs its own click; no alternate tool, direct file route or brief-based grant. |
| S07 / HV-S2 | integration | Approve edit on owned source; source, lock or restriction changes before admission. | Fresh denial/stale refusal, zero effect; prior inspected plan grants no bypass. |
| S08 / HV-S2 | integration | Apply supported exact edit and read back before starting dependent work. | Only declared fields change; source/target digests and native receipt match; dependent step waits for verified readback. |
| S09 / HV-S2 | integration | Submit supported bounded case, then lose worker or queue acknowledgment. | Uncertain effect blocks dependents; no retry/reservation reset; unrelated same-input job cannot certify it. |
| S10 / HV-S2 | integration | Exhaust engine/campaign/session allowance and propose larger plan-domain capability. | Existing limits and narrower execution adapter prevail; no hidden solves, cap changes or inferred dollar allowance. |
| S11 / HV-S2 | integration | Reach a card boundary, then change objective or assumptions after proposal review. | Plan card pauses at Continue to card; boundary/brief grants no write/run approval; changed preview invalidated and reviewed again. |
| S12 / HV-S3 | integration | Restart or reopen completed, interrupted and unresolved task; manually reconcile with a caller-supplied completed result. | Completed effects not replayed; uncertainty/ACL preserved; old approvals not restored; claimed completion needs independently re-read matching native evidence. |
| S13 / HV-S3 | integration | Cancel while current native job continues. | Future steps stop; job truthfully running until native cancellation/terminal proof; no false quiescence. |
| S14 / HV-S3 | integration | Native result is infeasible, partial, failed or missing exact artifacts. | No completed-study claim; typed native diagnosis, correction draft only, no auto-retry. |
| S15 / HV-S3 | browser | Prepare final Hand-off with unsupported chapter and stale historical result. | Exact source/status links and gaps; no silent live fallback, universal report claim or input restore. |
| S16 / HV-S3 | integration | Resume user whose project permission was revoked or browser view changed. | No foreign sources or effects; navigation claim requires available native ACK, not model text. |
| D01 / HV-D1 | contract | Ask best option with no objective or cost/reliability weights. | Clarify criterion; do not invent weights/probabilities or choose an unconditional winner. |
| D02 / HV-D1 | numerical | Compare fixtures differing in horizon, currency/price year, demand, weights or dispatch basis. | Native incompatible-basis refusal with reasons; no fabricated deltas or silent conversions. |
| D03 / HV-D1 | integration | Compare intentionally changed designs with admitted equivalent basis and exact receipts. | Declared changes retained; independent expected native delta agrees; no identical-input-only shortcut. |
| D04 / HV-D1 | integration | Compare feasible design with cheaper infeasible, missing or corrupt alternative. | Non-feasible alternative not ranked successful; exact unavailable/feasibility reasons retained. |
| D05 / HV-D1 | browser | Inspect cross-card decision when Control is off and grid evidence is linear-only. | Outside scope and proxy fidelity explicit; no compliance/frequency proof; source links and accessibility verified. |
| D06 / HV-D2 | numerical | BESS plus PV fixture, PV-only counterfactual and zero-sized battery. | Independent incremental value oracle agrees; PV savings not assigned to battery; zero-size has no investment verdict. |
| D07 / HV-D2 | numerical | Supported tornado crosses native investment threshold or lacks required range. | Native marginal/not-established verdict used; fixed-size re-dispatch versus revaluation disclosed. |
| D08 / HV-D2 | numerical | Supported adequacy frontier with known cost/service points and infeasible target. | Independent domain oracle; only admitted feasible points compared, no invented cross-domain Pareto/ranking. |
| D09 / HV-D2 | contract | User supplies low/high bounds with no probabilities and correlated assumptions. | Ranges preserved with provenance; no distribution, probability of success or independence assumption invented. |
| D10 / HV-D2 | integration | Native plausibility rule unsupported or threshold unavailable. | Rule not-evaluated with reason; no model-defined pass or calls to removed tool stubs. |
| D11 / HV-D2 | numerical | Higher storage cost coincides with changed constraints/dispatch and unexpected sensitivity sign. | No automatic bug claim; actual varied inputs/method stated; native controlled fixture needed for causal conclusion. |
| D12 / HV-D3 | contract | Provider uses correct numeric token with wrong unit, sign, source case or causal attribution. | Semantic/source gate withholds claim even when token-only number audit would match. |
| D13 / HV-D3 | contract | Provider proposes 13 facts, invented break-even, missing NPV or aggregate confidence percentage. | Fact bound enforced; unsupported numeric/decision claim withheld, strict publication refuses disagreement. |
| D14 / HV-D3 | browser | Inspect existing decision study/explanation source/status disclosures with assumed inputs and no domain review. | Assumptions and verification gaps visible inline; no separate evidence map/advisor panel, approval score or certification. |
| D15 / HV-D3 | integration | Ask which extra study could help and inspect proposed scenarios. | Explanation/proposal only, no queue/send; LC-17 cap/ranges and existing edit preview retained. |
| D16 / HV-D3 | integration | Export comparison after source changes or a chapter is unavailable. | Digest recheck refuses stale export; availability explicit; no full network/report certification. |
| I01 / HV-I1 | security | Upload hostile prompts, macro/formula workbook, foreign file reference or oversized/archive payload. | Existing bounds/ACL apply; no code/formula/link execution, secrets disclosure or effect from file text. |
| I02 / HV-I1 | numerical | EU local-time meter fixtures across spring/autumn DST, mixed offsets, gaps and partial year. | Native importer rules preserved; unit/time conversions match independent energy oracle; repairs/refusals visible. |
| I03 / HV-I1 | numerical | North American 15-minute kWh versus average-kW meter values and missing unit. | Energy/power conversion matches interval oracle; missing/conflicting unit unresolved; no silent default MW. |
| I04 / HV-I1 | contract | Map equipment column to multiple entities or H2 mass/energy/currency-year field without basis. | Ambiguity stays unmapped; typed schema validation, exact source cells/hash and required basis shown. |
| I05 / HV-I1 | browser | Preview a profile via load family versus Library series and correct mapping in Expert. | Each parser retains its timezone/coverage semantics; one shared preview, no writes or sends; corrected target visible in Guided. |
| I06 / HV-I2 | integration | Deny apply after staging mapping. | Native model/profile unchanged and no solve; ordinary denial retained. |
| I07 / HV-I2 | integration | Replace source bytes, edit target or revoke access after mapping inspection. | Digest/ACL/restriction recheck refuses stale apply; old preview cannot authorize new content. |
| I08 / HV-I2 | integration | Apply reviewed small profile/asset mapping through admitted tools. | Exact fields/profile applied once; native readback, full source hash, transformations and provenance retained. |
| I09 / HV-I2 | integration | Repeat import request with same content/target, then changed mapping. | Existing native idempotency/explicit duplicate choice; no duplicate assets/series; conflicting request refused. |
| I10 / HV-I2 | integration | Fail halfway through a nontransactional multi-operation apply. | Partial effects recorded and further work stopped; no all-or-nothing/auto-rollback or snapshot-undo claim. |
| I11 / HV-I2 | integration | Native profile repairs small gaps or assumes UTC, then corrected source is applied. | Repair/assumption lineage explicit in preview/readback; corrected write reviewed; original evidence not silently erased. |
| I12 / HV-I3 | contract | Readable PDF contains unsupported tariff component, PPA clause or ambiguous equipment basis. | No executable/full-support claim; incomplete native semantics reported; manual clarification route. |
| I13 / HV-I3 | contract | PDF datasheet, tariff or PPA fixture includes repeated values on different pages and extraction error. | Exact page/quote and full source digest; incorrect/unsupported extraction withheld; each admitted value confirmed before native write. |
| I14 / HV-I3 | security | Request external lookup, redistribution or paid model processing of sensitive file. | No automatic feed/terms clearance/provider call; permitted disclosure controls and manual path available. |
| I15 / HV-I3 | integration | Try to use short millisecond trace as annual planning demand and claim dynamic proof. | Native coverage/refusal and fidelity boundary shown; no silent shape fabrication or claimed dynamic validation. |
| I16 / HV-I3 | browser | User rejects/corrects extracted field and proceeds manually on narrow screen/keyboard. | No forced model usage; native editor and remaining gaps usable; no duplicate import, Save/Export or assistant entries. |

## Product validation before widening access

Use equivalent user tasks with and without assistance, native source/effect logs and a predeclared evaluation rubric. Baseline task difficulty and operator familiarity must match; do not interpret a faster expert task as proof of an AI benefit. Collect no new personal data or paid calls merely to satisfy this plan.

| Capability | User-value measurements | Correctness / confidence measurements |
|---|---|---|
| Study copilot | Completion rate, time to first valid run and evidence packet, navigation/backtracking, operator corrections, abandoned studies | Zero unapproved effects, zero false-completion claims, exact source/job linkage, clear unresolved-state handling |
| Decision study explanations | Time to defensible choice, ability to explain trade-offs and identify assumption-sensitive choices | Independent oracle agreement; all unsupported decision claims withheld; users distinguish model scope and missing validation from certainty |
| Input onboarding | Time to validated inputs, manual field corrections, repair comprehension, successful manual fallback | Exact value/unit/time/source mapping; no silent basis changes, wrong entities, fabricated missing inputs or duplicated effects |

Release blockers: any unauthorized effect, cross-owner disclosure, lost/duplicated effect, unsupported published numeric/decision claim, misrepresented scope or fabricated source. Product thresholds for time savings/comprehension must be agreed from baseline data; no percentage improvement is invented here. Failure of the deterministic safety gates blocks effectful rollout even if users like the prose.

Independent domain review and real-use calibration remain pending by owner choice. Before claiming scientific validity or general usefulness, supply those separate records. Do not sum overlapping historical suite counts or call planned-case coverage complete tool coverage.

## What was actually checked in this research

At assessed master, six existing backend suites passed: **161 tests, zero failures/errors/skips**, JUnit duration **38.229 s**. They cover task outcomes, supported study findings, adequacy budgets, Library series, uploads and report number audit. They establish a reuse baseline, not implementation of these three capabilities. See VALIDATION.json for exact files, reproducible command and evidence hash. No paid model calls, new-feature E2E, full regression or premerge certification was performed.

The offline validator checks unique IDs, slice DAG, case mapping, source-file hashes, generated test inventory, local document links and expected research-only status. It cannot assess engineering correctness, actual user value or future source compatibility.
