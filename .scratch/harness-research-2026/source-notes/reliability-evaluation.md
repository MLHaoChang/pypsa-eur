# Reliability/evaluation/security: coordinator completion

The delegated search batch stalled for roughly15minutes and returned no results; the original partial note is preserved below. The coordinator completed public primary metadata/full-PDF section checks directly. No claim here is attributed to an independent result from that failed batch. Source PDFs/text remain in workspace scratch; publication status is preprint unless independently stated otherwise.

## Verified sources and boundaries

- **Self-Harness**, Zhang et al., https://arxiv.org/abs/2606.09498v3. First8June2026, currentv3 20August. Read method/split/experiment sections. Weakness mining → minimal proposals → regression validation; three model families across Terminal-Bench2.0, SWE-benchVerified and AppWorld. The SWE study uses a100case subset with67held-in/33held-out; AppWorld uses180examples90/90. Reported relative gains against minimal initial harnesses do not imply gains against GridSpine's mature harness. Borrow offline failure→hypothesis→held-out regression workflow, not production policy self-edit.
- **From Prompts to Contracts**, Ahn/Kim, https://arxiv.org/abs/2607.08028v1, first9July2026. Read model-substitution, ablation, discussion and conclusion. Five corporate groups/25companies and270composition-boundary runs support preservation of specific code-checkable contracts, not correctness of promoted claims or investment quality. Invalid model outputs may fall back to a deterministic composer; do not describe final harness success as100%raw-model correctness. Claim eligibility/source promotion is upstream and not established by this experiment. Hosted live snapshots are not bit-for-bit reproducible. Published artifact DOI10.5281/zenodo.21269426 is reported by the paper, not separately downloaded here.
- **Source-code study of eleven systems**, Barbaste/Darrigol/Vu/Wiltberger, https://arxiv.org/abs/2609.00006v1. Primary abstract history lists15July2026; arXivID is2609.00006. Preserve the record as observed rather than infer a month from the ID or invent a revision. Read abstract, taxonomy and architecture discussion. Eleven sampled coding runtimes plus a contrast meta-harness; source snapshots,13observations/29patterns. The authors explicitly do not benchmark or rank. Observed framework/vector-retrieval absences are sample observations, not universal rules or causal evidence to remove a dependency.
- **ClawGuard**, Zhao et al., https://arxiv.org/abs/2604.11790v2, first13April2026/v2 11May. Read boundary enforcement and limitations. User-confirmed task rules checked at tool calls address malicious web/MCP/skill content. Direct compromise of user instructions/system configuration is outside its primary threat model. Rule generation/confirmation and native effect semantics remain important trust points. GridSpine already has immutable argument cards/offer checks/restrictions; new value is adversarial conformance and exact mutation admission, not another blanket LLM guard.
- **VIGIL**, Lin et al., https://arxiv.org/abs/2601.05755v2, first9January2026/v2 14January. Read method and limitations. Verify-before-commit with speculative hypotheses/backtracking, evaluated on959tool-stream cases. It does not provide a deterministic universal semantic verifier; exploring hypotheses can introduce significant overhead. Native effect admission and uncertain-effect stops must remain independent.
- Clinical deterministic-gate corroboration and surveys/formal-architecture evidence are in root-source-notes.md; context/skill controlled studies are in context-memory.md.

## Integration and validation conclusion

Use independent native outcomes, immutable source identities, exact read-back and typed policy checks. Extend existing replay/pilot/provider journeys with controlled failures, cost and recovery attribution. Keep required positive utility controls; refusal of everything is not useful safety. Change one harness component at a time with model/task/budget held fixed and a separate holdout. Prevent planted extra numerical claims from checked publication, but never equate MAC-valid evidence with correct physics. The documented envelope revision gap deserves an exact barrier test before broader unattended execution. Existing task retries, failure taxonomy, replay and event logs are already implemented, not missing features.

---
## Original delegated partial handoff (retained for provenance)

# Reliability and evaluation research handoff (partial)

Research cutoff: 2026-10-29. This subtask produced **no independently verified paper evidence**. An academic/web discovery batch containing environment-status, OpenAlex search, and web search calls remained blocked for approximately 899 seconds and was then aborted; no results were returned. The parent requested an immediate cutoff and explicitly prohibited additional search/full-text retrieval. Consequently, no numerical result, publication date, version, peer-review status, or paper-method claim below should be cited as verified by this note.

## Setup actually inspected

- Read the cloud runtime skill and its networking reference.
- Read `/etc/codex/network-policy.json`: version 1, HTTP policy unrestricted through inherited proxy; VPN not configured. No secrets were read or printed.
- Discovered academic OpenAlex/arXiv lookup tools and web search tool metadata. Did not successfully receive research results from them.
- No repository source files, Git state, PRs, paid model/media/API calls, or external applications were modified by this subtask. This scratch note is the only artifact.

## Candidate sources supplied by parent, pending its verification

These are lead URLs from parent-agent messages, **not independently inspected sources**. Exact titles, authors, dates, versions, publication status, methods, and metrics require the parent's primary-source checks. Keep these leads separate from the final bibliography unless those checks have been completed elsewhere.

| Candidate supplied in parent handoff | Primary-source lead | Unverified interpretation supplied by parent |
|---|---|---|
| Self-Harness | https://arxiv.org/abs/2606.09498 | Weakness mining, minimal harness proposals, regression validation. |
| From Prompts to Contracts | https://arxiv.org/abs/2607.08028 | Model-substitution and ablation experiments; exact counts and causal scope need full-text verification. |
| Source-code study of agent systems (Barbaste, Darrigol, Vu, Wiltberger) | https://arxiv.org/abs/2609.00006 | Parent located ID; parent reports record lists 15 July 2026 v1 despite September-shaped identifier. Preserve that discrepancy rather than silently correcting; check primary version history before citing date. |
| Categorical architecture paper | https://arxiv.org/abs/2605.12239 | Parent inspected full text: structural certificate preservation across targets; very small escalation experiment; SWE-bench Lite experiment with zero resolutions. This supports at most a structural-conformance idea, not improved coding-task success or hallucination-free behavior. |
| ClawGuard | https://arxiv.org/abs/2604.11790 | Runtime rules around tool execution; current full text may differ from abstract. |
| VIGIL | https://arxiv.org/abs/2601.05755 | Verify-before-commit security mechanism. Do not infer universal protection from tested injection cases. |
| MedSci Skills | https://arxiv.org/abs/2606.09500 | Seeded-defect checks in a few scientific pipelines; at most narrow feasibility evidence. |

## Design inferences to validate against GridSpine and primary papers

The following are engineering proposals, not demonstrated findings from sources inspected by this subtask:

1. Treat every model/harness change as an experiment against a frozen, versioned task suite with task-level outcomes, verification failures, cost, latency, and repeated-run reliability. Include an untouched holdout; tune on separate development cases.
2. Evaluate verifier false acceptance and false rejection independently. Passing visible tests is evidence about those tests, not proof of task completion, semantic correctness, safety, or absence of side effects.
3. Budget recovery loops explicitly. Compare one attempt, retries with identical context, feedback-guided repair, and fresh restarts under equal compute limits. Log repeated failure signatures and stop conditions; do not assume retries are independent or that self-verification improves results.
4. Test interruption and replay at operation boundaries: before execution, during execution, after side effect but before durable acknowledgment, and after acknowledgment. Use idempotency/deduplication controls and observable state reconciliation.
5. Tie an admitted operation to the exact checked revision and state; inject a state change between validation and execution to test whether checks become stale.
6. Keep retrieved documents and tool output outside the authority boundary. Add injection cases that attempt to alter operation scope, resource target, authorization, secrets handling, and reporting; measure attempted and actual unauthorized effects separately.
7. Pin benchmark data, evaluator revisions, model revisions where available, and harness configuration. Report contamination exposure and long-horizon failure modes separately from aggregate benchmark success.

## Limitations and exclusions

- No completed primary-paper metadata or full-text review in this subtask.
- No verified 2026-v1 versus 2026-revision classification; no peer-reviewed/preprint classification.
- No verified negative-result citation for self-verification, retries, or contamination. The design tests above must not be presented as literature findings.
- No GridSpine repository inspection was completed by this subtask. Parent's code audit must establish whether proposed controls already exist.
- Do not extrapolate narrow benchmark, security-case, or seeded-defect results to production guarantees.
