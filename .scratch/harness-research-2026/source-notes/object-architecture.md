# Object/programmatic architecture: coordinator completion

The delegated scholarly-tool batch stalled and returned no results. Its transparent partial handoff is preserved below. The coordinator independently completed primary metadata and full-PDF section checks for all three core sources via public arXiv retrieval; no source claim below depends on that failed batch. PDFs/text extractions and selected-primary-metadata.json remain under workspace scratch.

## Verified primary findings

- **NOOA**, Furgale et al., https://arxiv.org/abs/2607.20709v1, first22July2026, arXiv preprint. FullPDF introduction, interface/capability evaluation and limitations inspected. Methods are actions, fields state, docstrings instructions, annotations contracts; designated ellipsis methods invoke the model loop. Live in-process object references and code actions are central. Its limitations explicitly say model-written code executes in the agent's own process and the validator protects the agent loop, not the host. External sandboxing is required for host isolation; the preferred deployment named by the authors is OpenShell. A framework interface test pass is not engineering correctness, and pass-by-reference deployment has materially different trust assumptions from JSON tools. The recommended GridSpine pilot borrows typed interfaces while deliberately retaining serialized immutable identities and native admission; it is not the full NOOA live-object/runtime design and inherits none of its benchmark claims.
- **LLM-as-Code**, Qi et al., https://arxiv.org/abs/2606.15874v2, first14June2026/v2 22June. FullPDF control-flow discussion, case study and results inspected. Program owns known workflow structure and bounded calls; paper explicitly concedes open-ended exploration can benefit from LLM orchestration. GUI result table compares leaderboard baselines with different step budgets (up to100 versus the proposed15); treat as a case study, not a controlled estimate for native engineering tools. Conference'17/2017 template text in thePDF is not evidence of a 2017 or accepted conference publication.
- **AgentSPEX**, Wang et al., https://arxiv.org/abs/2604.13346v1, first14April2026, preprint. FullPDF language/state sections, evaluation and user study inspected. YAML workflows express typed steps, branches, loops, explicit context and submodules; paper reports7benchmarks. User study has23participants comparing rendered workflows: participants liked AgentSPEX readability/approachability but mostly preferred LangGraph for complex multi-step workflows. Do not turn this into an unqualified claim of better complex-workflow UX. Borrow explicit stage/verification contracts inside existing GridSpine cards before proposing a new visual workflow language.

## Public implementation availability checked separately

- https://github.com/NVIDIA-NeMo/labs-OO-Agents at main8598d4085ba2124e014d56eb612736ace0309c7e (29Oct2026). Public, not archived. GitHub license classifier returnedNOASSERTION, but the retrieved full LICENSE is Apache License2.0 with NVIDIA copyright notice. This is file inspection, not a transitive dependency/licensing audit.
- https://github.com/ScaleML/AgentSPEX at maindb99b7b6802fe15ef2997c82ec1d49e5228128f6 (11June2026). Public, not archived; API LICENSE metadata Apache-2.0.
- Neither package was installed or executed. Current GridSpine schema/dispatch/approval/evidence seams remain authoritative; dependency adoption requires its own compatibility, maintenance, sandbox and release review.

## Concrete pilot boundary and tests

Reuse existing frozen selectors and owner/project/run/evidence resolvers. Add no mutable network/world-model store. Pilot one existing read and preview via immutableProjectRef/RunRef/ComponentRef values; deserialized references grant no authority. Recheck ACL, binding, exact revision and receipt/source at use. Reject cross-project references, stale previews, ambiguous labels, missing retained artifacts and unsupported strict reads. Provider offers and Guided/Expert card policies remain intentionally different; enforce each declared policy rather than identical tool lists/cards. Compare context size, duplicated checks, reference errors and native outcomes against current code; abandon the facade if it adds indirection without benefit.

---
## Original delegated partial handoff (retained for provenance)

# Object-oriented / programmatic agent architecture — verification handoff

Research cutoff: 2026-10-29. This subtask produced **no independently verified publication record or full-text assessment** before cutoff. Do not cite this note as corroboration of paper metadata, benchmark claims, dates, repositories, or licenses.

## What was completed

- Read the managed-cloud runtime skill and networking reference.
- Inspected `/etc/codex/network-policy.json`: policy snapshot is unrestricted HTTP via inherited sidecar proxy; VPN not configured.
- Discovered academic search tool schemas and prepared arXiv-scoped OpenAlex searches with publication range 2026-01-01 through 2026-10-29 for “object oriented agent”, “AgentOS”, “object centric agent”, and “agent programming code execution”.
- The combined environment-status / scholarly-search call did not return usable search results and was aborted after approximately 901 seconds. Therefore no paper metadata or methods were obtained from that call.
- No shell HTTP primary-source retrieval was completed. No repositories, model calls, or media generation were run; no application edits were made.

## Leads supplied by coordinating researcher — NOT independently verified here

These identifiers and descriptions were passed by the parent researcher. They must retain that provenance until the parent confirms them from primary sources:

| Lead | Supplied identifier | Supplied description |
| --- | --- | --- |
| OO Agents: Native Python Object-Oriented Agents | https://arxiv.org/abs/2607.20709 | NVIDIA-labs / NOOA; methods as actions, fields as state, annotations as contracts, ellipsis method bodies completed by an LLM. Parent supplied author shorthand P. Furgale et al. |
| LLM-as-Code | https://arxiv.org/abs/2606.15874 | Programmatic agent architecture lead; title expansion, authors, methods and results unverified in this subtask. |
| Code as Agent Harness | https://arxiv.org/abs/2605.18747 | Code-based harness lead; exact title, authors, methods and results unverified in this subtask. |
| AgentSPEX | https://arxiv.org/abs/2604.13346 | Parent described typed steps, branches, loops, reusable modules, state, sandbox, checkpoint, verifier and visual graph; parent supplied author shorthand Pengcheng Wang et al. |

**Unknown for every lead here:** exact full author list, exact title spelling, native arXiv v1 date, latest revision date, accepted venue, peer-review status, DOI, benchmark protocol, baseline parity, sample sizes, cost accounting, source-code URL and license. An arXiv identifier is not evidence of peer review. Its YYMM component is not a verified first-submission date.

## Architecture questions suitable for primary-source evaluation

These are engineering evaluation criteria, not findings about the supplied papers:

1. An object-oriented agent interface may make state ownership, allowed actions, schemas and reusable behavior easier to express. Determine whether the implementation actually enforces these contracts or merely presents type hints to the model.
2. Executing LLM-generated method bodies requires an external execution boundary. Ordinary Python types and class encapsulation do not themselves provide a security sandbox, prevent arbitrary I/O, restrict subprocesses, or mediate credentials.
3. For GridSpine, a bounded experiment could expose one existing domain operation through a typed state object and explicit methods, preserving its existing approval, retry, persistence and audit controls. Measure end-to-end task success, unauthorized action attempts, recovery after restart, latency and total model/tool cost against the current harness.
4. A workflow compiler / typed-step approach should be tested on dynamic branches, tool errors and interrupted runs, because improvements on fixed benchmark flows may not transfer to production coding tasks. Distinguish generated-program correctness from completed-task correctness.
5. Repository availability, an open license, reproducible evaluation artifacts and model/provider dependence must be verified separately from any paper’s headline score.

## Scope limit

No comparison with GridSpine source was performed in this subtask. Any integration recommendation above is provisional; the coordinating researcher owns the source audit and synthesis. Do not promote these leads to a verified bibliography based on this note.
