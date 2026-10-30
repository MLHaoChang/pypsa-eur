<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GridSpine harness: 2026 research synthesis and integration assessment

Research cutoff: **29 October 2026**. Source-code baseline: actual fetched `master` **06df80d420677f06f63b9a71b13d028227bc03bd**. This is a research and design assessment, not an implementation, official gatekeeper verdict, or claim of production/domain validation. “GridPipe” in the request is interpreted as the GridSpine application in `MLHaoChang/pypsa-eur`.

## Recommendation

Keep the existing domain harness. Borrow the strongest ideas from the 2026 literature incrementally: explicit typed references, executable admission/transition contracts, recoverable engineering state, independent native outcome verification, and controlled evaluation of harness changes. Do not replace the harness with a generic coding-agent framework or grant arbitrary Python execution to the application copilot.

The object-oriented work the owner may have seen is **NVIDIA-labs OO Agents: Native Python Object-Oriented Agents** [1]. Its useful insight is that actions, state, context and contracts can share a comprehensible programming interface. A Python class is not itself a permission boundary, and a type annotation is not proof of physical correctness. NOOA itself warns that its in-process code validator protects the agent loop, not the host; external sandboxing is required for host isolation. GridSpine should borrow a narrow immutable reference/facade pattern over its existing services before considering any new runtime.

The first engineering priorities are more concrete than a framework migration:

1. Add a deterministic concurrency test and close the **documented revision-check gap during an envelope-controlled handler** before expanding unattended authority.
2. Extend the existing evaluation suite with whole-trajectory, provider-conformance and compaction-counterfactual cases. Measure actual correctness, useful completion, refusal and recovery separately.
3. Connect persistent user constraints and durable budget reservations to the runtime boundaries that consume them. Their storage/helpers already exist; persistence alone does not enforce them.
4. Pilot typed immutable domain references on one existing read and preview path. Expand only if this reduces duplicate checks and ambiguity without weakening admission.
5. Continue already-planned checked evidence and bounded study loops through their existing owners and tickets. They are not new discoveries or grounds to widen checked-tool admission.

## Scope and research method

Four parallel tracks were launched for object-oriented/programmatic architecture, verification/evaluation/security, context/memory/skills, and a read-only current-code/backlog audit. Two scholarly-tool batches stalled and returned no results; their partial handoffs are labelled as such, and the coordinator completed those primary-record and relevant full-PDF section checks directly. Only verified evidence enters the synthesis. Discovery used academic search and public primary records; substantive claims were checked against primary arXiv records/full text where available. First submission, revision and publication status are distinguished. Surveys organize evidence but do not supply a new controlled performance experiment. A workshop paper is not a main-conference paper, and an author-reported acceptance is labelled as such.

This is a targeted literature and source-code review, not an exhaustive systematic review or meta-analysis. Published preprints are included because much current harness research is preprint-only. Search snippets were not treated as final evidence: the current SkillsBench and AGENTS.md revisions materially change earlier summaries. Recursive Language Models is a December **2025** origin with 2026 revisions, not a 2026 first release [20].

No paid model/media calls, live model experiments, deployments, application changes, reviewer messages or PR actions were performed for this research. Source tests were inspected rather than rerun. The literature's benchmark gains are not GridSpine measurements. Two practitioner pages at OpenAI and Anthropic returned HTTP 403; their contents are not represented as inspected primary evidence.

Supporting notes contain version histories, primary URLs, evidence limits and code line references:

- [Object/programmatic architecture](source-notes/object-architecture.md).
- [Reliability, evaluation and security](source-notes/reliability-evaluation.md).
- [Context, memory and skills](source-notes/context-memory.md).
- [Additional primary checks and retrieval limits](source-notes/root-source-notes.md).
- [Current implementation and backlog audit](source-notes/current-harness-audit.md).
- [Separate synthesis/code-claim review](source-notes/synthesis-review.md): research-only GO after correcting intentional provider offer limits and Guided/Expert confirmation differences; not an official implementation or domain verdict.

## What the research supports

The surveys distinguish observation, context, control, action, state and verification rather than treating the prompt as the entire agent [16–18]. The source-code study compares eleven particular coding-agent systems [6]. Such inventories are useful architecture maps; an observed pattern or absence in selected repositories is not evidence that every product should copy it.

### Object-oriented agents: useful interface, conditional adoption

NOOA makes methods callable actions, fields explicit state, docstrings instructions and annotations typed contracts; an LLM loop can supply designated method bodies [1]. This unifies surfaces that are otherwise split between schemas, tools and callbacks. The transfer to GridSpine is an internal, narrow interface over validated native identities, not exposure of the mutable PyPSA network or unrestricted object methods.

LLM-as-Code instead emphasizes program-owned control flow: code governs sequence, branching and termination while the model is called for bounded reasoning [2]. AgentSPEX makes typed workflow steps, state, branches and submodules inspectable [3]. These support clear stage inputs/outputs and bounded transitions. They do not demonstrate that all domain work should be expressed in a new DSL or that a model-proposed plan is safe because its syntax is typed. AgentSPEX's 23-person study favored readability/approachability but generally favored LangGraph for complex multi-step workflows; it is not an unqualified complex-workflow UX win.

Practical distinction: ordinary exploratory chat can remain a bounded model-led loop. Once a user approves a concrete study or multi-step change, native code should own its authority, budgets, dispatch, source identity, stop conditions and outcome receipts. GridSpine already does much of this.

### Contracts and verification: software owns enforceable claims

The enterprise contracts study separates replaceable model composition from code-owned source/routing/output checks [4]. Its fixed scenarios and model substitutions support this separation, but not universal truthfulness. Invalid model compositions may fall back to a deterministic composer; final contract preservation is not 100% raw-model correctness or proof that upstream promoted claims are true. Verification must match the claim: schema checks validate shape, native read-back validates a recorded effect, integrity checks validate record origin/content, and scientific calculations validate only their declared domain/basis.

The categorical-architecture paper makes this distinction especially explicit [19]: preservation of three supported structural certificate types across five compiler targets does not establish behavioral correctness or “no hallucinations.” Its reference implementation and small model/task experiments do not justify adding that framework to GridSpine. The transferable idea is a conformance suite that ensures adapters preserve the same guards and verifier semantics.

A related scientific-reporting prototype provides a concrete example of seeded-defect evaluation: its current manuscript reports deterministic gates finding all 27 injected defects on matched fixtures, versus 11 for one generic LLM-review prompt [15]. The designed detector/defect match, small domain-specific set and single reviewer baseline limit that comparison; it is feasibility evidence, not a universal detector advantage.

For GridSpine, an independent calculation, solver receipt and exact source identity are stronger numerical evidence than another model agreeing with an answer. A software checker is still bounded by its assumptions, coverage and implementation; independent domain review remains separate.

### Memory and compression: grade the next action, not the summary

SWE-MeM and CAMG show mechanisms for retaining useful state across limited contexts, with important training and benchmark dependencies [9,11]. They do not establish that adding a summarizer or vector database improves any hosted model. PAIR offers a more directly transferable diagnostic: continue from the **same checkpoint** before and after compression while holding the downstream environment fixed [12]. Compare verified outcomes and recovery effort, including repeated-run reliability.

Current GridSpine history trimming preserves tool-call/result pairing and a short deterministic activity summary; durable parent state and recoverable result spill already exist. The question is whether the combination preserves the exact engineering constraints, units, source/run selection, invalidations and unfinished work needed for the next permitted action. A summary cannot mint an approval, certify an old number as current, or convert a discussed result into the active result.

RLM keeps large source material outside the root prompt and accesses it programmatically [20]. The near-term application is bounded native filtering/aggregation and evidence locators for long result tables, not unrestricted recursive agents. Recursive calls carry their own error, cost and latency tails.

### Skills and instructions: focused procedures must earn their cost

SkillsBench v4 reports 87 tasks, eight domains and 18 model–harness configurations; average pass rate rises from 33.9% to 50.5% with curated skills, but 13 of 87 tasks have negative changes [7]. These are benchmark averages, not an expected GridSpine uplift. Focused skill bundles are more promising than exhaustive manuals, and self-generated skills are not automatically useful.

Evaluating AGENTS.md v3 finds no significant general task-resolution gain from repository context. Developer context has a marginal +2.4 percentage-point result (p=.21), while generated context is marginally negative and inference costs increase [8]. This does not justify deleting mandatory project rules: policy compliance and multi-session coordination are different objectives from the measured Python issue-resolution task.

Demystifying Agent Skills separates procedural anchoring, skill selection and downstream success [10]. For GridSpine, measure whether a curated workflow gets the user to a verified outcome with fewer wrong steps; counting loaded skills or exact skill-name matches is insufficient.

### Self-improvement: offline proposals and held-out validation

Self-Harness mines trace failures, proposes bounded harness edits, and validates proposals with regression testing [5]. This is useful as an **offline development process**. It does not authorize the deployed assistant to rewrite its tool authority, approval policy, thresholds or checked-answer rules.

A release candidate should state a falsifiable prediction, use a separate held-out task set, preserve prior safety cases, and retain rollback and versioned evidence. Evaluate the same model under both harnesses, then test provider transfer. More models, reviewers or retries are not automatically more reliable; correlated mistakes, extra spend and unverified completion must remain visible.

### Injection and tool safety: check effects at the boundary

The security studies examine explicit tool-boundary rules and verify-before-commit approaches [13,14]. Their results are benchmark-specific, not proof of universal injection resistance. In GridSpine, an uploaded tariff file, document, MCP description or tool response is data. It cannot grant permissions or alter the approved project, fields, values, budgets or source.

Existing argument snapshots, exact offered-tool checks, cards, saved restrictions, evidence gates and external-MCP limits are valuable. New object/skill/workflow interfaces must exercise these same boundaries. If untrusted external stdio servers become a product requirement, OS-level isolation is a separately scoped project; environment filtering is not an OS sandbox.

## Current implementation: do not duplicate these capabilities

All entries describe inspected source at the pinned master, not newly executed tests.

| Capability | Current evidence | Implication for research adoption |
|---|---|---|
| Provider-neutral tools and schemas | `harness/protocol.py`, `catalogue.py`, `services/chat_tools.py`, parity/dispatch tests | Consolidate semantics; do not introduce a second tool catalogue or permission system. |
| Bound approvals and restrictions | `confirm.py`, `session.py`, `approvals.py`, `restrictions.py` | New facades must recheck exact scope/revision through the existing path. |
| Finite budgets and retries | `budget.py`, runner charging, task retry limit of two | Improve durable admission/attribution where incomplete; do not add a generic retry loop. |
| Replay and failure classification | `replay.py`, event log, `failure_taxonomy.py`, `run_lifecycle.py`, error-kind manifest | Extend tests and attribution rather than propose these foundations as missing. |
| Durable history and state | `history.py`, `eventlog.py`, `services/durable_conversation.py`, `spill.py` | Evaluate state survival and connect typed constraints; a new generic memory database is not a prerequisite. |
| Native effects and recovery | `effects.py`, task receipts, experiment fencing/reconciliation | Preserve uncertain-effect stops; no success from model prose or handler JSON alone. |
| Checked numerical publication | `assurance.py`, native profiles, records with MAC/rederivation, charts/exports | Extend one bounded domain adapter at a time; whole-tool admission is not implied. |
| Study runner and envelopes | `runner.py`, run profiles/gates, approvals and monitoring | Complete existing bounded correction-loop tickets rather than build a parallel orchestrator. |
| Tool discovery and selection | `providers/wiring.py`, discovery offer refresh, toolsets/MCP | Test selection recall under catalogue pressure; outsourced selection work already exists. |
| Pilot evaluation | `pilot_evaluation.py`; README documents suite v12 / 292 cases | Extend finite-case evidence; do not call it a production error-rate estimate. |

At this baseline the checked-tool allowlist has **seven tools**, not the three shown in an old audit. General `get_results` is still refused in strict turns. K #851's native plausibility read and G-b #853's frontend work must not be described as merged master features. Their current scope/amendments must be re-read when implementation resumes; this research does not change them.

## Prioritized integration roadmap

These are proposed slices, not authorization to implement. Existing tickets retain ownership and gates. Initial work should be backend-only wherever possible, using the same five workflow sections and companion.

| Order | Slice and value | Existing home / minimal seam | Release acceptance |
|---|---|---|---|
| 1 — P0 | Close the documented envelope authority-to-effect revision gap | Harness/native mutation owners; README documents a human edit landing after runner precheck but during the handler entering the chain. Recheck expected input identity under the actual mutation lock. | Barrier test inserts edit precisely between checks; no unintended effect or false receipt; unchanged request succeeds; no deadlock or widened lock scope. A documented limit is not a claim that an exploit was reproduced here. |
| 2 — P0 | Extend trajectory and semantic conformance evaluation | Existing pilot, replay, fake-provider, dispatch and journey suites; no replacement evaluation platform. | Preserve semantic source/ownership/unit/stop/evidence invariants across wires. Each provider enforces its exact sent-offer allowlist and retains required eligible tools within its declared limit; each UI mode follows its own confirmation policy. No false completion on stale/uncertain workers. |
| 3 — P0 | Durable provider/reviewer resource admission | Reuse parent reservation/settlement helpers and current provider/runner metering. Audit found helpers but no general production send-path callers. | Concurrent calls cannot reserve the same allowance; crash/timeout preserves uncertain reservations; late usage settles once; no restart budget reset. This is an integration finding, not a demonstrated unlimited-spend exploit. |
| 4 — P1 | Typed persistent engineering constraints | Existing durable conversation state + study brief/gates; promote confirmed requirements with identity, unit, scope, source and revision. | Constraint survives two trims, resume and provider switch; contradictions clarify; superseded constraints cease applying; imports cannot create policy/approval; incompatible edit cannot silently claim compliance. |
| 5 — P1 | Immutable native reference facade pilot | Existing project registry, reference resolution, request scopes, job adapters and result views. One read + one preview path first. | Foreign/stale refs refuse; duplicate labels clarify; retained data never falls back live; serialized ref is not authority; native services remain source of truth. |
| 6 — P1 | Targeted context packing and counterfactual continuation | Existing history/eventlog/spill/workflow loader; measure before changing policy. | No-trim/current/candidate summaries replay the same task state; complete tool pairs, correct selected run and constraints, explicit missing/expired evidence; output and permissions cannot be inferred from a summary. |
| 7 — P1, existing | Checked plausibility evidence adapter | Assurance issue 10, dependent on K; domain service owns the five rules; study-harness issue 05 consumes them. | Positive supported finding publication, extra claims withheld, exact kind-only admission, MAC/rederive and drift/tamper tests; preserve refusal of every unsupported `get_results` variant. |
| 8 — P1, existing | Bounded input/result correction loops | Study-harness issues 04/05 through the current runner, profiles and native gates. | Known defect fixed within frozen tunables/budgets; unchanged fail-set + input identity stops; no repeat solve without justified changed inputs; no receipt means no completion. |
| 9 — P1, existing | Curated stage procedures and isolated source-ledger research | Existing workflow loader and study-harness 09; curated first, not self-installed code. | Relevant units/tool order/verifiers; confusable procedure tests; verified source quote/URL; researched value always confirmed; injected source cannot broaden tools or writes. |
| 10 — P2, conditional | Cross-domain experiment adapters / displayed-data ACK coverage | Existing experiments and navigation contracts, one native domain/view at a time. | Exact native job/result/window/basis; partial effects block retry; rendered-data mismatch blocks “showing” claims; unsupported control/process fidelity stays explicit. |
| 11 — P2, conditional | Restricted external execution | Only if untrusted MCP servers or executable analysis become an approved requirement. | OS filesystem/network/credential isolation, cancellation and native admission checks; harmless adversarial fixture servers. Not needed to implement typed references or ordinary result explanations. |

### Object-oriented pilot: concrete design boundary

A minimal value interface could carry `ProjectRef(project_id, revision)`, `RunRef(project_id, run_id, source, manifest_digest)` and `ComponentRef(project_id, revision, component_class, component_id)`. Add explicit time window, units or calculation basis where the operation requires them. Reuse current frozen dataclasses/selectors where they fit; do not introduce a parallel persistent model.

The factory uses existing owner/project/reference/evidence resolvers. A method or adapter calls the existing dispatcher/service, with fresh ACL, binding, input-revision and assurance checks. Copying or deserializing a reference grants no capability. Approval cards still bind concrete arguments; stale references require a fresh preview. The model receives bounded serializable data, not a live mutable network or file handle.

Measure a pilot against the current implementation: duplicate identity checks removed, wrong-run or ambiguous-reference errors, context size, latency and verified task outcome. Prefer the current design if the facade adds indirection without measurable benefit. NOOA as an installed framework is **explore-only**, not a recommended production dependency at this stage. Its public [repository](https://github.com/NVIDIA-NeMo/labs-OO-Agents) and AgentSPEX's [repository](https://github.com/ScaleML/AgentSPEX) were available at the inspected snapshots; license-file/API inspection indicates Apache-2.0 content (NOOA's GitHub classifier returned NOASSERTION, so the actual file was read). Neither package or its dependency tree was installed/audited. The proposed facade deliberately avoids NOOA's live mutable object execution model.

## How this fits the product workflow

Keep the existing Energy/Capacity, Grid, Reliability, Commercial and Control sections and the single companion. No new “harness mode,” framework picker or mandatory skill selector is required.

For “change this battery limit, run the study and explain the trade-off,” the companion resolves the exact project/scenario and constraints, prepares the native diff, obtains the existing approval, executes only the admitted steps, checks the native job/evidence, and explains results on the declared source and basis. If the user edits the model mid-step, input identity changes, or a worker's effect is uncertain, the existing Activity/card flow explains why work paused. A model assertion is never the completion receipt.

For a data-center example, a user's confirmed 40 MW connection limit should remain an explicit scoped requirement while comparing battery/genset candidates. The study uses only the selected modules: a capacity result does not establish AC grid-code compliance or fast-transient control performance. Grid, reliability, commercial and control steps consume compatible native results when configured; missing prerequisites lead back to the relevant configuration step. Their optionality and dependencies must follow backend capability contracts rather than be inferred from a picture or generic plan.

Relevant guidance is shown at the current step. Advanced detail stays in existing Expert controls. The user may ask for help parametrizing any supported module, but unsupported time-domain/grid-code fidelity must be disclosed instead of approximated silently. Research-based changes should reduce repeated questions and wrong steps, not introduce additional decisions for the user.

## Test and validation matrix

This is proposed validation, not executed research results. Start with deterministic offline fixtures. Real-model experiments require a separately authorized budget; independent domain review and representative-use calibration remain pending by owner choice.

| Test family | Representative case | Oracle / required result |
|---|---|---|
| Capacity/dispatch | Small known investment/merit-order case, weighted snapshots and period costs | Hand-worked/native solver facts on declared basis; no confusion between annualized cost and horizon totals. |
| Grid | Known two-bus case, variable ratings, LOPF versus AC result source | Native reference; correct units/source and loading definition; no P-only result represented as AC compliance. |
| Reliability | Designed versus unintended loss, native ENS/cap facts, missing sample series | Warning/error distinction respects admitted design; unavailable LOLE evidence remains unknown. |
| Commercial | Known tariff/contract cash flow with currency, money year and weighting | Independent deterministic settlement/finance facts; missing commercial basis cannot become an exact ROI claim. |
| Control/process | Supported native dynamic/process fixture and deliberately unsupported model fidelity | Exact engine outcome and declared time basis; refusal/clarification for unavailable control or time-domain claim. |
| Reference isolation | Same label across projects; current, discussed and retained run | Exact owner/project/run resolution; no fallback or leaked values. |
| Approval/effect concurrency | User edit at the precheck/handler boundary; expired/revoked card; late worker | Re-admission or safe pause, no unintended authority and no automatic repeat of uncertain effects. |
| Persistent state | Unit correction/constraint and unfinished task across trims/restart/rebind | Authoritative scoped state survives or becomes explicitly unavailable; old approval does not revive. |
| Context counterfactual | Identical checkpoint with no trim, current trim, candidate structured trim | Grade action correctness, valid completion, refusal, recovery effort and full token cost separately. |
| Tool/skill pressure | Distractor descriptions and confusable procedures under the 128-tool provider limit | Necessary eligible tool remains available; being offered does not authorize it; procedure selection and task success reported separately. |
| Injection | Imported document/tool/MCP text requests policy change or secret access | Untrusted text cannot grant authority, mutate declared requirements or bypass source/approval gates. |
| Adapter conformance | Same semantic task on supported provider wires and both UI modes | Same source/authority/native outcome invariants. Tool sets may differ under provider caps; Guided and Expert obey their intentionally different card policies. Prose wording need not match. |
| Budget/recovery | Concurrent sends, crash between send and settlement, unknown late usage | Reservation persists, exactly-once settlement where established, explicit uncertain state, no reset by new session. |
| Checked publication | Native positive control plus planted extra number, wrong source and corrupted record | Only admitted facts published; unsupported strict request limited/refused before handler; corruption/drift fails closed. |

For each candidate compare the same model/task/budget with and without the change; keep a separate held-out set. Report useful verified completion, false acceptance, false refusal, exact-source errors, repeated failed actions, recovery success, native solve count, all agent/reviewer/compressor input/output/cache usage, and p50/p95 latency. A process quality score or average success alone can hide unsafe failures. Finite zero failures is not a population “zero error” claim.

Suggested initial experiment size is a small stratified fixture set (for example 12–20 state-boundary cases), explicitly exploratory rather than statistically representative. Later model repeats should report all-repeat success as well as mean success and uncertainty. Do not sum overlapping synthetic suites into one coverage or error-rate denominator.

## Implementation handoff and gates

Each slice should name its owner, existing ticket or new gap, exact allowed files, unchanged security boundary, seeded failing case, acceptance oracle, rollback and deferred coverage. Re-read current `AGENTS.md`, owner decisions and open PR amendments against fresh master before building. Do not merge these research recommendations into K/G-b or change their scopes implicitly.

Start with the deterministic envelope barrier test and the evaluation/state-survival pack. Add the durable reservation and typed-requirement integrations as separately reviewable slices. Pilot the object-reference facade only after its hypothesis and compatibility tests are concrete. Existing Assurance/study-harness follow-ups retain their gatekeepers and ownership.

No agent self-modification in production, generic multi-agent layer, automatic skill installation, new vector database, universal framework migration, distributed project state, unbounded recursion or arbitrary code execution is recommended. These can be revisited for a measured product need, not adopted merely because a paper implements them.

## References and evidence status

The numbered source catalogue follows. Version-specific detail and limitations are in the supporting notes. Survey summaries and small demonstrations are not treated as controlled evidence of a GridSpine benefit.

| Ref | Paper and authors | First release; version used | Evidence status and limit |
|---|---|---|---|
| [1] | [NVIDIA-labs OO Agents: Native Python Object-Oriented Agents](https://arxiv.org/abs/2607.20709v1), Furgale et al. | 22 Jul 2026; v1 | arXiv framework/experimental report. Object interface is not an application security or physics guarantee. |
| [2] | [LLM-as-Code: Agentic Programming for Agent Harness](https://arxiv.org/abs/2606.15874v2), Qi et al. | 14 Jun 2026; v2 22 Jun | arXiv programming/control-flow proposal with a computer-use case study; transfer must be measured. |
| [3] | [AgentSPEX: An Agent SPecification and EXecution Language](https://arxiv.org/abs/2604.13346v1), Wang et al. | 14 Apr 2026; v1 | arXiv workflow language/system, benchmark and user-study evidence; not validation of GridSpine workflows. |
| [4] | [From Prompts to Contracts: Harness Engineering for Auditable Enterprise LLM Agents](https://arxiv.org/abs/2607.08028v1), Ahn and Kim | 9 Jul 2026; v1 | arXiv public-data enterprise prototype, model substitutions and scoped ablations; fixed contract/task domain. |
| [5] | [Self-Harness: Harnesses That Improve Themselves](https://arxiv.org/abs/2606.09498v3), Zhang et al. | 8 Jun 2026; v3 20 Aug | arXiv empirical self-improvement study; regression/held-out evaluation, model-specific results, not production self-edit authorization. |
| [6] | [Harness Engineering: Anatomy, Architecture, and Evolution of Coding Agents -- A Source-Code Study of Eleven Systems](https://arxiv.org/abs/2609.00006v1), Barbaste, Darrigol, Vu and Wiltberger | Primary record lists v1 15 Jul 2026; arXiv ID 2609.00006 | Observational source-code study; not randomized evidence that a particular architectural pattern causes success. |
| [7] | [SkillsBench: Benchmarking How Well Agent Skills Work Across Diverse Tasks](https://arxiv.org/abs/2602.12670v4), Li et al. | 13 Feb 2026; v4 14 Jun | Benchmark study. NeurIPS 2026 Evaluations and Datasets acceptance is author-reported, not independently verified proceedings here. Version-specific results only. |
| [8] | [Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?](https://arxiv.org/abs/2602.11988v3), Gloaguen et al. | 12 Feb 2026; v3 29 Sep | ICLR 2026 MemAgents workshop per authors' ETH publication record; not ICLR main conference. Marginal task effects are not general policy-compliance results. |
| [9] | [SWE-MeM: Learning Adaptive Memory Management for Long-Horizon Coding Agents](https://arxiv.org/abs/2606.28434v1), Gao et al. | 26 Jun 2026; v1 | arXiv trained memory policy for coding, including workflow comparisons; hosted-model drop-in gains not established. |
| [10] | [Demystifying Agent Skills: Why They Work-Until They Don't](https://arxiv.org/abs/2608.14036v1), Jiang et al. | 14 Aug 2026; v1 | arXiv matched skill/memory comparisons and sampled mechanism analysis; retrieval precision and downstream success differ. |
| [11] | [Coding Agent Memory Post-training: Unlocking the Memory Potential of Pre-trained File Operations for Long-Horizon Tasks via Reinforcement Learning](https://arxiv.org/abs/2609.34422v2), Luo et al. | 28 Sep 2026; v2 30 Sep | arXiv file-memory training and interventions; limited task/model scales and single-episode scope. |
| [12] | [Adapting Context Compression for Long-Horizon Agents with Counterfactual Continuations](https://arxiv.org/abs/2609.36526v1), Min et al. | 29 Sep 2026; v1 | arXiv checkpoint-matched continuation diagnostics on office/API benchmarks, mainly 2k–4k compression budgets. |
| [13] | [ClawGuard: A Runtime Security Framework for Tool-Augmented LLM Agents Against Indirect Prompt Injection](https://arxiv.org/abs/2604.11790v2), Zhao et al. | 13 Apr 2026; v2 11 May | arXiv tool-boundary security experiments; benchmark-specific threat model, not universal protection. |
| [14] | [VIGIL: Defending LLM Agents Against Tool Stream Injection via Verify-Before-Commit](https://arxiv.org/abs/2601.05755v2), Lin et al. | 9 Jan 2026; v2 14 Jan | arXiv tool-stream injection defense/evaluation; a verification protocol does not replace native effect admission. |
| [15] | [Deterministic Integrity Gates for LLM-Assisted Clinical Manuscript Preparation](https://arxiv.org/abs/2606.09500v4), Nam, Jeong and Kim | 8 Jun 2026; v4 14 Jun | arXiv scientific-reporting feasibility study; matched seeded defects and one generic review comparator, not independent clinical-quality certification. |
| [16] | [Externalization in LLM Agents: A Unified Review of Memory, Skills, Protocols and Harness Engineering](https://arxiv.org/abs/2604.08224v1), Zhou et al. | 9 Apr 2026; v1 | Survey/synthesis, not a new controlled efficacy experiment. |
| [17] | [Code as Agent Harness](https://arxiv.org/abs/2605.18747v1), Ning et al. | 18 May 2026; v1 | Survey of executable interfaces/mechanisms, multi-agent code artifacts and verification gaps. |
| [18] | [From Question Answering to Task Completion: A Survey on Agent System and Harness Design](https://arxiv.org/abs/2606.20683v1), Guo et al. | 14 Jun 2026; v1 | Survey/model–harness comparison; leaderboard/vendor comparisons are not equivalent to controlled harness ablations. |
| [19] | [Harness Engineering as Categorical Architecture](https://arxiv.org/abs/2605.12239v1), Banu | 12 May 2026; v1 | Formal/implementation proposal with limited structural preservation experiments; no proof of hallucination-free behavior or task-resolution gains. |
| [20] | [Recursive Language Models](https://arxiv.org/abs/2512.24601v3), Zhang, Kraska and Khattab | **31 Dec 2025**; v3 11 May 2026 | Older foundation with 2026 revisions and author-reported NeurIPS acceptance; recursive access is not indefinite memory or free computation. |

Except where explicitly qualified above, no peer-reviewed acceptance was independently established. arXiv availability is not peer-review certification. Paper-specific details and primary version histories are in the source notes; bibliographic titles abbreviated for long subtitles are identified by exact arXiv version links.


## Deliverables and residual limits

This package contains the synthesis, an implementation/backlog audit and three literature notes. Raw primary downloads remain in the workspace scratch evidence folder; PDFs and scraped pages are not application dependencies. Research did not change application code or either open implementation PR. No runtime benchmark, independent domain approval or production error-rate measurement is claimed. Adoption remains conditional on native tests, scoped review and the owner's normal approvals.
