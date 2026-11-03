<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Archived initial review — superseded

Historical NO-GO and intermediate findings only. The final verdict is [12-INDEPENDENT-REVIEW.md](../12-INDEPENDENT-REVIEW.md); do not treat this archive as the current verdict.

# PR #567 independent implementation-spec review

Review scope: implementation-spec readiness, independently checked against fresh master. This is not runtime certification, CI/premerge approval, production permission, or independent electrical/finance/domain review.

Original reviewed PR head: `36b5342ad5588ecad8400cd1b9af29a36864431b`. Fresh actual-master source: `7a14d27038de424d07a832b16e2465297c7b8b7d`, detached at `/workspace/gridspine-harness-independent-review-master`. Original specification source pin `089aea276c75b098c8d349cb5fa0eb4cb7267d64` is historical and materially predates shipped work. Line references in initial findings refer to the original PR head, obtainable with `git show 36b5342:<path>`.

Initial decision: **NO-GO** for the original implementation specifications. Four findings below require concrete corrections before implementation. The overall approach is sound: native engines perform effects/calculation; model proposals remain subordinate to typed schemas, source-bound proof and ordinary approvals; unsupported domains remain unavailable. The rejection concerns implementable incremental contracts and test oracles, not the absence of a general shell or agent team.

The amended candidate was re-reviewed below. **Current candidate: NO-GO**, narrowed to the remaining F4 profile-fixture scope/anchor correction. F1–F3 are closed, and most of F4 is closed. Exact reviewed-file hashes are recorded in the final section and companion JSON. Promised edits are not treated as completed edits.

## Findings requiring correction

### F1 — P1: reconcile shipped behavior before specifying replacement checkpoint/runtime work

Locations: `.scratch/high-value-ai-study/08-HARNESS-IMPLEMENTATION-SPEC.md:7`, `:15`, `:19`, `:53`, `:128`; `09-HARNESS-DATA-AND-STATE-CONTRACTS.md:61`, `:75`; `02-STUDY-COPILOT-SPEC.md` current-runtime descriptions; `OWNER-DISPOSITION.md` step-cap description; machine plan source/status fields.

Fresh master already implements study-copilot S1/S2. `services/assistant_tasks.py:23` contains the 12/24-step and 16k/32k limits, and `:405` ignores caller result during native reconciliation. The actual COP-1 path is authenticated stream `continue_task_id` → `continue_at_card` (`assistant_tasks.py:389`), state `paused_at_card`; `resume_task` refuses that state with `task_pause_needs_user`. Original 09:75 instead proposes `task_checkpoint_ref`, a one-use session intent and `resume_task`, with state `checkpoint`. That is a competing implementation, contrary to the specification's own instruction to adopt LC-23.

Also shipped: investment S8 checked calculations, checked site import, equipment-table mapping, the AC-PF Link dispatch fix, H2a result explanation and Safe restore fixes. The AC prerequisite must become a retained regression gate, not instructions to implement the same native fix again. S8 residual coverage must be described against the actual native admitted scopes; it is not a new universal finance adapter.

Example: an implementer follows 09:75 and adds a new resume handshake, while the existing plan card sends `continue_task_id`. Two different continuation mechanisms now disagree on which state and click grant boundary passage.

Required remediation: add a fresh-master delivered/residual reconciliation, keep historical test evidence on its actual source, and specify only residual hardening on the existing native path. For boundary races persist an exact boundary generation/phase plus semantic view revision/digest. Validate and consume atomically under the existing task lock. A delayed click for boundary A cannot clear B in another session. Polling should not accidentally change the semantic boundary identity; source/auth/cancellation changes must invalidate admission. Preserve existing state/error names rather than introducing synonyms.

Required tests: old/current stream compatibility; model/MCP `resume_task` refusal; two simultaneous current clicks; delayed A click at B; cancelled/stale/revoked task after rendering; valid native Continue clears exactly one boundary and still grants no write/run permission.

### F2 — P1: bounded validation/repair and no-progress behavior are not fully specified

Locations: `08-HARNESS-IMPLEMENTATION-SPEC.md:65`, `:144`, `:159`, `:189`; `10-HARNESS-DELIVERY-AND-TESTS.md:54`, `:59`, `:73`; original machine plan closed choices/cases.

The original contract bounds stored plan length, context bytes and native case counts, and prohibits automatic effect retry. It does not define a falsifiable native validate → diagnose/correction draft → approved correction → revalidate loop, a no-progress key/threshold, or how diagnostic allowances survive goals, continuations, retries and recreated sessions. A repeated diagnosis can consume many model/tool rounds without advancing a declared plan step. A model's assertion that the goal is complete is not native completion proof.

This is a residual guard: master already has 25 tool calls/turn, 8,192 output tokens/request, session output and optional daily token gates; goal rounds default five/max twenty; transient pre-output stream retry max three; H2a lowers explanation to two provider rounds/three tools. Those must be preserved and tested, not described as absent. The optional daily cap defaults off and reported tokens can be unavailable; context bytes are not spend guarantees.

Required remediation: define a finite native diagnostic epoch plus a task-wide allowance that cannot reset through automatic retries/goal churn/context trimming. Define exactly which validation passes, supplemental reads, correction drafts, dispatches and provider attempts consume each counter, including fallback/SDK retries. Debit before admission, atomically and durably; unknown provider usage cannot zero counters. Two equivalent unchanged native failures must stop or another explicit finite no-progress threshold must be chosen. Source change follows stale/review semantics, not an invisible fresh allowance. Keep waiter polling distinct from validation failure and retain actual job state. A cap ends partial/To review, never verified success. No new dollar-budget endpoint or autonomous effect retry is needed.

Required tests: repeated identical and equivalent/alternating validation failures; no validator available; diagnosis before dependent effect; correction is proposal-only until its separate approval; task/attempt/session/provider/goal reconstruction; concurrent debit; missing/corrupt ledger; pre-output retry and fallback charge; lower-cap precedence; zero provider construction after refusal; unknown usage; polling timeout remains waiting; user-facing partial status at every termination.

### F3 — P1: normal, late and reconciliation completion need one explicit atomic proof contract

Locations: `08-HARNESS-IMPLEMENTATION-SPEC.md:53–73`; `09-HARNESS-DATA-AND-STATE-CONTRACTS.md:68`, `:97–99`; `10-HARNESS-DELIVERY-AND-TESTS.md:37–42`.

Original HS-02 directs the verifier primarily through `resolve_task_step`. The state table requires terminal success with proof, but tests focus on uncertain-step reconciliation. Fresh normal `finish_step` (`assistant_tasks.py:777–806`) can promote generic successful results and advance the fingerprint chain without a versioned `completion_proof`. It is not sufficient to harden only the manual reconciliation path.

Example: a normal handler returns a successful transport shape with an absent/wrong edit receipt. If only reconciliation is changed, it can still advance the next effect through ordinary finish. A crash between receipt, proof and status publication must not leave an authoritative completed state lacking proof.

Required remediation: explicitly share the same family-native verifier across normal completion, late receipts, queue/foreground settlement and manual reconciliation. Persist originating attempt/fence, receipt, native proof and authoritative status atomically or leave an uncertain recoverable state. Every proof-dependent reference/progression must enforce this requirement. Native receipt readback alone must not relabel an unrelated same-input job. Positive native quiescence/fencing is needed after restart; an empty process-local worker/session registry is not evidence that an external worker stopped. Missing closure/no-effect proof leaves uncertainty and reservations intact.

Required tests: normal and late handler success without/wrong receipt; native readback mismatch; completion crash barriers; same-input replacement worker; restarted origin whose external worker remains live; normal valid effect advances once; legacy completion is historical only; no retry/requeue or ledger reset from missing proof. Quiescence and successful proof are distinct assertions.

### F4 — P2: family journeys need domain-specific inputs/oracles and individually addressable evidence

Locations: `10-HARNESS-DELIVERY-AND-TESTS.md:61–74`, `:86–92`; `harness-implementation-plan.json` E2E fixture mappings; `validate_specs.py:161–167`.

All fourteen original E2E cases are bound only to `FX-OWNED`, an ownership/ACL fixture. E2E-06 says adequacy **or** FMEA, E2E-07 reads a native connection assessment, and E2E-08 calls for economics values; these have no selected domain inputs, independent expected result or exact unavailable-only scope. Only FX-S7/FX-S8 contain numerical oracles. Every E2E case names the same unparameterized `test_family_journey` target, leaving room to count one function's green result for fourteen families. The validator verifies fixture-ID membership and matrix consistency, not sufficient numerical/domain semantics.

Required remediation: map each journey to concrete existing native fixtures/oracle nodes, exact chosen operation and expected source/status/value. For adequacy choose and declare horizon/outage/critical-service definition and a hand-checkable ENS/LOLE case; do not silently claim FMEA too. For electrical work either bind an actual supported numerical regression (e.g. LP Link dispatch preserved through AC PF) or explicitly scope the new case to unsupported-only/native-disposition behavior. Bind commercial work to exact S8/tariff fixtures, experiments to origin/phase fixtures, and imports to actual equipment/profile fixtures. Parameterize case node IDs or otherwise provide an unambiguous executed-node mapping per journey. Exclusions remain beside each evidence link.

Required tests: removing the required domain fixture fails the consistency check; an ACL-only fixture cannot certify engineering behavior; source/method/basis mutation fails; missing individual parameterized result leaves that family pending. This is a request for falsifiable synthetic acceptance, not independent domain certification.

## Checked aspects that do not independently block implementation

- Context design preserves untrusted origins, current versus discussed sources, tool-use/result pairs, byte accounting including envelopes, and explicit required-content overflow refusal. Token size remains unknown without the native adapter tokenizer; no cost guarantee follows.
- Discovery is additive, native-schema-derived, bounded and does not create callable tools or approval authority. Clarify cursor progress for a single oversized field: typed unavailability must not produce endless zero-return pages. This can be a small CAP regression within HS-01.
- Effects retain per-step cards in Guided/Expert. Brief acceptance and native Continue grant no effect authority. Cancellation, dismiss/stop-monitoring and native job cancellation are distinct.
- Wider checked analysis remains exact-source/slot-bound and optional; unavailable quantities remain unavailable. Native numerical agreement is separate from engineering validity. The S7/S8 fixtures' stated arithmetic is internally consistent.
- Provider seams should share all native gates; offering-cap and schema-conversion checks must not be mistaken for live language-quality evidence.
- Rollback preserves unresolved evidence and requires quiescing chat and MCP effect serving before incompatible downgrade. Old binaries must not be assumed to enforce new proof fields.
- Trusted packaged procedures may guide reads and proposals but cannot alter tiers, permissions or model-independent validation. General shell/subagents are correctly deferred.

## Official Claude Code source verification

Read the locally downloaded official sources and verified all seven against `/workspace/scratch/harness-assessment/official-source-records.json`: byte count and SHA256 match for how-claude-code-works, hooks, skills, sub-agents, memory, sandboxing and costs. Retrieval record is 2026-10-21 UTC; these are publicly documented practices, not access to Anthropic's private implementation.

Relevant verified passages:

- `https://code.claude.com/docs/en/how-claude-code-works.md`, local lines 13–23 and 49–58: gather context, act and verify; lines 132–148: compaction/tool-output thrashing and deferred context; lines 152–160: checkpoints cover files, not remote external effects.
- `https://code.claude.com/docs/en/hooks.md`, local lines 2700–2706: `stop_hook_active` and eight-consecutive-continuation protection. Copying hook syntax is unnecessary; the transferable practice is bounded continuation with explicit terminal behavior. Lines 861–863 distinguish hook timeout from a guaranteed fail-closed permission gate.
- `https://code.claude.com/docs/en/sub-agents.md`, local line 304: `maxTurns` returns partial output at the bound. This supports truthful partial termination, without recommending subagents for this package.
- Skills/memory/sandboxing/costs support on-demand procedures, persisted-versus-current context, actual enforcement boundaries and exact reported-usage semantics. They do not establish GridSpine domain correctness, universal rollback or a promised percentage cost reduction.

## Independent checks and evidence limits

- Ran the offline original spec validator successfully: seven existing-owner units, 47 planned harness acceptance cases, fourteen families; historical counts retain original sources. This is consistency evidence only, not proof that planned cases execute or that the source remains current.
- Independently verified all seven official-source files against their recorded byte sizes and SHA256 hashes.
- Ran unchanged fresh-master `test_harness_goals.py` plus `test_chat_budget_gates_seam.py` with fake providers: **42 passed**, exit 0. Command used the shared test-environment Python, `PYTHONDONTWRITEBYTECODE=1`, no pytest cache provider, BLAS thread limits, faulthandler 45 seconds and a hard 180-second process limit. Local network allowance was needed for TestClient's in-process socket/event-loop setup. The earlier restricted-network attempt printed 29 successes then stalled and was stopped; that attempt is neither a completed pass nor a product failure. This verifies shipped budget/goal behavior, not proposed LOOP guards.
- No paid/model calls, runtime/source edits, reviewer contact, push or merge. Only this report is written by this reviewer; implementation-spec changes are the author's work.

## Candidate re-review

**Verdict on the exact files below: NO-GO for implementation-spec readiness**, with one narrow remaining P2 blocker. This is a substantial improvement over the original candidate; the main harness contracts now meet the requested design standard.

Closed findings:

- **F1 closed:** 11 and the reconciliation JSON distinguish shipped S1/S2, checked investment/site import, AC Link dispatch, equipment/profile and Safe restore foundations from residual work. The existing `continue_task_id`/`continue_at_card` and `paused_at_card` path is retained, with exact semantic boundary references and stale/cross-session tests. No replacement planner/checkpoint API is required.
- **F2 closed:** 08's bounded validation/correction section and LOOP-01–06 establish finite, persistent, pre-debited counters, distinct validation/supplemental-read categories, lower-cap precedence, equivalent-failure stagnation and truthful partial termination. Provider retry/fallback attempts count; usage uncertainty cannot lift bounds. Unknown old ledgers require reviewed compatible handling. Native waiters retain their own finite observation behavior.
- **F3 closed:** normal finish, late receipt, automatic settlement and reconciliation share the proof requirement. 09 explicitly requires bounded cross-process native transactions, expected revision/attempt and fixed lock ordering; no storage lock spans provider/solver/human work. Receipt/proof publication and crash reservation tests are explicit. Guarded-version migration and no mixed old/new effect serving are stated.
- **F4 substantially closed:** all 53 cases have individual parameterized evidence nodes; all fourteen family journeys have distinct setup/oracle/native-anchor fixtures. Adequacy's 60/240/180 MWh and EUR27,000/year case matches its actual native regression. Campus coverage is honestly scoped to adapter/source/settings behavior, not independent physics certification. Tariff and S8 arithmetic are explicit. A re-review caught that the first version of FX-J09 increased demand above FX-S7's wind availability; the author corrected it to a separate sufficient 4 MW firm generator, marginal cost EUR10/MWh and weights [1,3], yielding EUR140/150 and delta EUR10. The corrected two-case setup is feasible.

Remaining **F4-residual, P2**: `harness-implementation-plan.json` FX-J03/FX-J10, and `10-HARNESS-DELIVERY-AND-TESTS.md` E2E-03/E2E-10. FX-J03's native anchor is still `test_assistant_capabilities::test_native_import_confirmations_and_rebinding`, which is a NetCDF network import/rebinding test. The two-row CSV profile fixture must explicitly use the existing direct `upload_load_profile` path and its actual foundation `test_chat_tools_dispatch::test_upload_load_profile_decodes_base64_and_dispatches`. It must say this is a short network profile, not the full-calendar measured-load importer; period labels 2030/2040 must share the explicitly selected 2030 operational timestep axis (or specify an actual admitted per-period copy operation). FX-J10 inherits the same interpretation. This keeps the positive case executable without silently changing native parser/axis rules. The author agreed to this correction, but it is not present in the exact reviewed files. Apply it, regenerate the corresponding matrix/machine records, rerun the offline validator and request a focused re-review. No broad harness redesign is needed.

Additional later-master review: actual master advanced from `7a14d27038de424d07a832b16e2465297c7b8b7d` to `1b1aa5ccc5dd4c8ca9fdf2ec680fd96cb5f687b5` via #583. I independently inspected the entire five-file delta: flow service, Library reference application, two money-year test files and release note. It adds an advisory mixed-cost-year warning and treats hand-typed overridden cost years as unknown; it does not change harness/task/proof/provider behavior or require redesign. An acknowledged warning, or absence of a warning, does not establish common money basis or inflation conversion. Prior source `currency_year` metadata on a hand-typed value cannot become established context/checked comparability. The specs' unknown-basis/native-source rules remain applicable. Add a separately labeled later-master record; preserve the author's 142-test foundation evidence on 7a. Do not relabel that evidence onto 1b or count new money-year tests as new harness acceptance.

Independent re-review checks:

- Re-ran the current candidate's offline validator successfully: 53 planned cases, 22 fixtures, fourteen family links; review status remains pending and no future case is represented as executed.
- Independently checked the fixture arithmetic and two-case firm-capacity feasibility for S7, S8, adequacy, TOU tariff and corrected network comparison. These are sanity checks, not execution of the proposed journey tests or domain approval.
- Independently executed 42 shipped goal/budget tests on 7a as recorded above. The author's separate 142-test baseline was inspected through its source/hash/JUnit validation; it was not my own execution and the counts must not be summed into universal coverage.

Exact candidate SHA256 values (relative to `.scratch/high-value-ai-study/`):

| File | SHA256 |
|---|---|
| 08-HARNESS-IMPLEMENTATION-SPEC.md | `e981f5467a3efb46afc6ca263e0295fb323413fa5dc084398f69e80f431c6cf0` |
| 09-HARNESS-DATA-AND-STATE-CONTRACTS.md | `98b197e9b4b9ab3aafd44fc6d6db5a7bb271db5f5f266fb8442c8377994ed713` |
| 10-HARNESS-DELIVERY-AND-TESTS.md | `ee02c49bf579edd0524bbf78b312bb8b9d879838b8850a1671431e32d047b9dc` |
| 11-CURRENT-MASTER-AND-REVIEW.md | `d7a6945d87ed4779f063985d56b1f0dc8a2cda95013a478ce1c6dbb78767bd38` |
| harness-implementation-plan.json | `d694f500a38106120d75fa66167fcd4af885f68433ad2705c2a8f0944d65904d` |
| validate_specs.py | `2f023136b05edbf4c887f20142b8af46c3f70200a778075491c2545e22f5c591` |
| current-master-reconciliation.json | `1b0a93887e5b2d4d8cd4b5287f23d9721eaa01bd63567f80a31769963a201b80` |

The machine-readable review is `/workspace/scratch/harness-567-independent-review.json`. No runtime/source/spec file was edited by this reviewer. This verdict does not assert that shipped runtime is incorrect; passing shipped regressions does not approve an incomplete future contract. Independent domain review, live calibration, actual 53-case implementation execution, CI/premerge and production approval remain separate gates.
