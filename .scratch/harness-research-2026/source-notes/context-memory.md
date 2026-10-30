# Context, memory, and skills: evidence through 2026-10-29

Research scope: LLM agent harnesses for coding and scientific/engineering tools. Six substantive 2026 papers plus one 2025 foundation revised in 2026. All seven were read in primary-source arXiv full-text HTML, with arXiv submission histories checked. Claims below use the latest version available at the cutoff unless explicitly marked v1. Search snippets can be stale: two important papers changed materially between first release and current versions.

## Practical conclusion

The useful direction is a small, explicit working state backed by recoverable evidence and task-specific procedures, evaluated against the existing harness. The evidence does **not** justify a blanket larger prompt, generic persistent vector memory, automatic skill creation, arbitrary recursive delegation, or a universal context-length threshold. Compression should preserve the information required by the next actions; its value must be judged by those actions and their verified outcomes, not summary readability.

GridSpine already implements several relevant ideas. The next step is to measure whether its existing trimming and workflow interfaces preserve engineering state, then test a narrowly scoped improvement. Learned memory papers support architectural experiments but do not establish that their gains will transfer unchanged to a hosted frontier model or a 300k coding session.

## Main sources, dates, and status

| Source | First arXiv submission; revisions checked | Publication status verified at cutoff |
|---|---|---|
| [Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988v3), Gloaguen et al. | v1 2026-02-12 14:15:22 UTC; v2 2026-06-23; v3 2026-09-29 | ICLR 2026 **MemAgents workshop**, oral / runner-up best paper per [authors' ETH lab publication record](https://www.sri.inf.ethz.ch/publications/gloaguen2026agentsmd). Not an ICLR main-conference paper. |
| [SkillsBench](https://arxiv.org/abs/2602.12670v4), Li et al. | v1 2026-02-13 07:06:06 UTC; v2 2026-03-07; v3 2026-03-13; v4 2026-06-14 | Author [September 2026 news](https://www.yiminl.com/news) reports acceptance to NeurIPS 2026 Evaluations and Datasets Track, poster. Acceptance is author-reported; conference proceedings were not independently verified. |
| [SWE-MeM](https://arxiv.org/abs/2606.28434v1), Gao et al. | v1 2026-06-26 04:55:24 UTC; no revision listed | arXiv preprint; no acceptance/venue verified. |
| [Demystifying Agent Skills](https://arxiv.org/abs/2608.14036v1), Jiang et al. | v1 2026-08-14 07:26:38 UTC; no revision listed | arXiv preprint; no acceptance/venue verified. |
| [Coding Agent Memory Post-training / CAMG](https://arxiv.org/abs/2609.34422v2), Luo et al. | v1 2026-09-28 06:40:28 UTC; v2 2026-09-30 | arXiv preprint; no acceptance/venue verified. |
| [Adapting Context Compression / PAIR](https://arxiv.org/abs/2609.36526v1), Min et al. | v1 2026-09-29 02:18:13 UTC; no revision listed | arXiv preprint; no acceptance/venue verified. |
| [Recursive Language Models](https://arxiv.org/abs/2512.24601v3), Zhang, Kraska, Khattab | **v1 2025-12-31 03:43:41 UTC**; v2 2026-01-28; v3 2026-05-11 | Older foundation with 2026 extensions. [First author's project post](https://alexzhang13.github.io/blog/2025/rlm/) reports acceptance to NeurIPS 2026. [OpenReview record](https://openreview.net/forum?id=ColZtoh6As) could not be opened by the web tool; acceptance is therefore author-reported here. |

## What each contributes, and what it does not establish

### 1. Repository instructions: useful conventions must earn their context cost

[AGENTS.md v3 full text](https://arxiv.org/html/2602.11988v3) evaluates SWE-bench Lite (300 Python tasks) and CTXbench (138 tasks from 12 repositories with committed context files). LLM-generated context reduces average resolution by 0.5 percentage points on SWE-bench and 2 points on CTXbench, while increasing cost by 20% and 23%. Developer context improves resolution by 2.4 points on average, but this is not statistically significant (p=.21); it outperforms generated context, and still adds steps/cost. Agents follow the added instructions, increasing exploration and testing, without becoming better at locating relevant files.

Implication: keep non-obvious project requirements and reliable command/provenance pointers; do not assume a generated architecture overview improves performance. The study tests Python task resolution, not security, policy compliance, or multi-session project coordination. It cannot justify deleting mandatory owner rules.

Version trap: [v1 abstract](https://arxiv.org/abs/2602.11988v1) says context tends to reduce success. v3's result is more precise: generated context is marginally negative, developer-written context marginally positive, neither effect significant. Use the current conclusion.

### 2. Curated procedural skills: paired evaluation beats skill-count metrics

[SkillsBench v4 full text](https://arxiv.org/html/2602.12670v4) covers 87 terminal/container tasks across 8 domains and 18 model–harness configurations. Curated skills raise task-macro pass rate from 33.9% to 50.5% (+16.6 points), but 13/87 tasks have negative deltas. Domain deltas include Natural Science +28.8, Industrial & Physical Systems +15.7, Software Engineering +11.6, and Mathematics & OR +9.7 points. Focused bundles outperform exhaustive documentation. Self-generated packs fall below the no-skills baseline in all three dedicated-harness configurations tested, though this mixes content quality, discoverability, and creator/solver interference.

Implication: GridSpine's scientific workflows are plausible targets for curated recipes containing units, tool ordering, checks, and applicability boundaries. The evidence is not a promise that every skill helps. The paper explicitly calls for length-matched and retrieval-only controls and warns about long-horizon generalization and composition.

Version trap: [v1](https://arxiv.org/abs/2602.12670v1) had 86 tasks / 11 domains / 7 configurations / 7,308 trajectories / +16.2 points, with Software Engineering +4.5 points. Do not combine those statistics with v4.

### 3. SWE-MeM: context management can be part of the agent action space

[SWE-MeM full text](https://arxiv.org/html/2606.28434v1) provides `compress(analysis, start_step_number, end_step_number, content, remaining_work)`. The agent chooses timing and span; the replacement preserves ordering and appends remaining work. SFT and memory-aware GRPO train the behavior with OpenHands/Qwen backbones under 32k context. SWE-bench Verified resolution reaches 43.4% (4B) and 60.2% (30B); the 30B base is 51.6%, workflow-only 58.4%, SFT 58.8%. Compression choices are evaluated jointly with issue resolution rather than by compression ratio alone. A tool-removed 256k-context 4B ablation drops to 37.6%.

Implication: prototype selective eviction of obsolete log spans while retaining decisions and unfinished work. The strongest results involve training and changed policies; they are not evidence that adding a compression tool to any current hosted model yields the same improvement. The workflow-only baseline also uses a proprietary model to choose compression actions, which adds cost and changes the comparison.

### 4. Skill mechanisms: improve execution, then test retrieval separately

[Demystifying Agent Skills full text](https://arxiv.org/html/2608.14036v1) holds underlying trajectories fixed while comparing cleaned workflow memory with distilled skills. In matched comparisons, skills improve success by 6.06 points. Mechanism analysis over 238 valid open-coded labels attributes 65.7% of skill cases to procedural anchoring and 4.5% to knowledge injection. The normalized record pool has 8,135 trials; the taxonomy sample is about 3% of it. As candidate pools grow from 5 to 100, actual-use precision falls from 29.6% to 3.3%, while success remains roughly stable; offline identification and downstream use are separate measurements, not consecutive stages.

Implication: evaluate whether a workflow causes the right tool sequence and verification, not whether its exact name was invoked. Add confusable workflow distractors. Limits: selected terminal/tool benchmarks, few harness/model configurations, and sampled mechanism labels; not open-ended scientific collaboration or persistent organizational memory.

### 5. CAMG: ordinary files can carry agent state across context boundaries

[CAMG v2 full text](https://arxiv.org/html/2609.34422v2) trains file-based memory using ordinary shell/file operations in Shop, Coding, DeepResearch, and AutoResearch environments. It records objectives, evidence, tests, and next steps in a continuation file before replacing context, then reads it afterward. Blanking saved files at the first boundary reduces average CAMG success by 14.1 points; task-mismatched transplantation reduces it by 11.5 points. That intervention is stronger evidence of memory use than counting writes.

External results are modest: 4B/9B resolve 15.8%/27.6% of SWE-bench Verified and medal on 1/22 and 2/22 MLE-bench Lite competitions. One MLE medal moves the result by 4.5 points; confidence intervals are wide. Frozen comparators use automatic summary prompts, so method differences include policy training and memory interface. The paper limits memory to one episode/user; it does not establish multi-user or indefinite memory. Product implication: structured, bounded continuation records are more directly actionable than an unvalidated cross-project memory system.

### 6. PAIR: evaluate a compaction boundary by continuing from the same state

[PAIR full text](https://arxiv.org/html/2609.36526v1) replays from a checkpoint immediately before versus after compression, repeats continuations, and adapts a structured summary prompt using the harmful boundaries. It keeps downstream agent, tools, and decoding fixed. Main evaluations use AppWorld's 168-task test-normal split and OfficeBench, with tau2 Retail as a stress test. Three runs report average success plus the fraction succeeding in **all three** runs. Compression can preserve mean solvability while reducing repeat reliability and adding recovery actions.

Implication: sample a few real GridSpine trim boundaries in a sandbox and compare downstream action correctness/recovery cost. Limit: main compression budgets are only 2k–4k tokens, benchmark environments are checkpointable API/office simulations, and offline continuations add model calls proportional to boundaries/horizon. This supports the diagnostic method, not a direct numeric prediction for engineering chat or 300k developer sessions. Deployment uses only the adapted prompt; readaptation may be needed after tool/task shifts.

### 7. RLM: move large source material outside the active prompt

[RLM v3 full text](https://arxiv.org/html/2512.24601v3) puts a long prompt into a Python REPL variable. Code can inspect it, select slices, call submodels programmatically, and keep intermediate results outside root-model context. Tests include long-document QA, information-dense aggregation, and code-repository QA; input can reach 10M+ tokens. This is a mechanism for accessing and computing over source data, not evidence of indefinite autonomous task persistence.

Implication: favor deterministic filtering/aggregation of large engineering result tables before model interpretation. Treat recursive semantic calls as an optional, budgeted extension. Recursion is not universally better: a no-subcall variant wins one CodeQA setting; deeper calls propagate syntax errors; costs/runtimes have long tails. The authors explicitly flag exploding subcall costs and underexplored natural tasks/guardrails. Label the idea 2025-origin with 2026 revisions/acceptance, rather than inventing a 2026 first publication.

## Existing GridSpine seams observed at the supplied snapshot

Snapshot: `/workspace/harness-research-2026`, supplied commit `06df80d420677f06f63b9a71b13d028227bc03bd`; no repository files changed.

- `pypsa-gui/backend/harness/history.py:582` onwards: deterministic summary of trimmed turns, maximum 1,200 characters, 8 lines; `_describe_dropped` retains shortened user request plus tool names. `trim_session_messages:654` drops complete turn groups to preserve tool-use/result pairing. This is compact activity memory; inspect downstream cases before concluding it preserves engineering decisions or evidence.
- `harness/spill.py`: already stores oversized tool results whole, exposes preview plus a `read_result` locator; owner/session bound, bounded by item/session size and 24h TTL, and disabled during checked assurance turns. The architecture is already recoverable external data, though retention and accessibility are conditional.
- `harness/workflows/registry.py`: trusted package Markdown, typed workflow/step metadata, applicability by UI context/project kind, and current-step body. This already supplies procedural structure and progressive disclosure.
- Developer coordination is separate: `.claude/hooks/context-guard.sh` warns at 300k, `.claude/skills/steward/SKILL.md:90` prescribes live-state handoff, `docs/agents/session-brief.md` provides task/decision/file/dependency fields. None of the papers validates 300k as the universally correct threshold. It is an owner policy to preserve until separately tested/changed.

## Candidate experiments, proposed only (no paid calls run)

1. **Turn-trim state survival.** Build 12–20 replay fixtures around existing `trim_session_messages`, including a changed unit, selected scenario/snapshot, changed network revision, failed attempt, user constraint, pending approval, and computed result provenance. Compare current activity summary against a compact structured record: objective, binding/revision, hard constraints with units, approved decisions, evidence locators, invalidated conclusions, unfinished work. Check that answers and next permitted actions remain correct after trim, and that stale approval/result evidence is not treated as current. Preserve complete tool pairs and existing assurance boundaries.
2. **Checkpoint continuation comparison.** At the same environment snapshot and tool state, compare no-trim, current trim, and candidate structured trim. Use three repeats when future model budget is authorized. Report all-three success, task pass rate, extra recovery calls, repeated failed actions, cumulative agent+compressor tokens, and p50/p95 wall time. Keep model, tools, decoding, task, and budget fixed; do not infer compression harm from unrelated whole-run traces. This is the PAIR method scaled down to a practical pilot.
3. **Workflow usefulness and applicability.** Use representative engineering workflows already present: renewable profiles, run-study, explain-results, investment decision. Paired conditions: existing step guidance, minimum tool/reference pointers, curated concise procedure, and length-matched irrelevant/reference text. Include 0/1/2–3/>3 modules and confusable neighboring workflows. Grade scenario/revision correctness, units, provenance, numerical invariants, and verified outcome. Track exact workflow selection separately from success. Favor executable assertions and fallback paths over prose expansion.
4. **Memory-content intervention.** On isolated replay copies, remove or replace the structured continuation state while leaving project state fixed. A genuine memory gain should disappear when its evidence is removed and should fail safely on a mismatched project/revision. This detects memory that merely correlates with success. Never transplant customer state or run this against live user projects.
5. **Large engineering result access.** Compare current spill/paging against deterministic filtered aggregate views for long solver logs or timeseries tables. Verify numerical totals and lossless locator recovery, including expiry/restart paths. Only after deterministic access is effective consider an RLM-like bounded semantic subcall pilot; cap depth/calls/tokens and grade against exact calculations. RLM's headline long-input QA numbers do not establish grid-engineering numerical correctness.
6. **Developer handoff fidelity as separate work.** For a few archived coding sessions, test whether a fresh session can recover current SHA, current review verdicts, outstanding blockers, and next action from the existing note alone. Add structured fields only when a measured omission matters. Separate fixed owner rules from mutable task state; do not rewrite 300k policy on paper evidence alone.

## Additional sources considered, lower priority

- [Active Context Compression / Focus](https://arxiv.org/abs/2601.07190v1), v1 2026-01-12: full text checked; 22.7% token reduction but only **five** selected SWE-bench Lite tasks, both conditions 3/5 successful. Useful minimal prototype, inadequate evidence for general reliability. IEEE conference *format* in arXiv comments is not proof of acceptance.
- [Workflow-to-Skill / W2S](https://arxiv.org/abs/2606.06893v1), v1 2026-06-05: full text checked. Reconstructs workflows/semantics/attachments from traces, with 70 reference skills and replay consistency evaluation. Useful design vocabulary for verification/rollback/state requirements; internally constructed replay benchmark is weaker evidence for downstream scientific/coding success. The abstract/title use RWSA while the body describes Skill-IR/WSA; use cautiously and pin the version.
- Pure context-engineering position/practitioner papers were deprioritized in favor of controlled task studies. No claim is made that search was exhaustive.

## Retrieval record and limits

Discovered tools via `ALL_TOOLS`; used public web search for discovery/status and primary public arXiv pages through Python `urllib` for exact metadata and full text. Read cloud-runtime skill and networking guidance, inspected supported network policy, confirmed current enforced unrestricted HTTP policy, preserved inherited proxy/CA/TLS, used shell `with_additional_permissions.network.enabled=true`. No API keys read, no paid model/media calls, no PR actions, no repository edits.

Primary arXiv abstract/full HTML files and text extractions are saved under `context-sources/` next to this report. All selected full-text downloads succeeded. First local retrieval attempt failed because `requests` was not installed; retried with stdlib `urllib`. The web tool could not open RLM's OpenReview forum, so its acceptance is explicitly author-reported. Older snippets were corrected against pinned current primary versions; v1 abstracts were fetched for the two material version changes. No 2026 publication date or acceptance was inferred solely from a title or conference-style formatting.
