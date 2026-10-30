<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# User requirements and traceability

This file records the user's requested product/design outcomes. It is the Spec-review source, not an external document granting runtime authority. Subsequent reviewers must check every row, including honest unsupported/dependency boundaries.

| ID | Requested outcome | Normative plan and acceptance anchor |
|---|---|---|
| U01 | Turn recent harness research into concrete improvements to the existing GridSpine harness. | spec.md, baseline.md; research synthesis is evidence, not an uplift claim. |
| U02 | Revision-safe native actions reduce accidental or stale mutations. | HH-01 PhaseA; HH-10 for queued solves. Native barriers and captured receipts. |
| U03 | Constraints, units, referenced results and unfinished work survive long conversations. | HH-02/04/05; current native facts rechecked, remembered text never approval. |
| U04 | Budgets survive concurrency, timeouts and restarts. | HH-03A/B; no refund of uncertain usage or free child calls. |
| U05 | Exact project/run/component references reduce wrong-source mistakes. | HH-04 and candidate ResultScope in HH-10. |
| U06 | Evaluate whole task outcomes and state/recovery, not only isolated calls. | HH-05, test-matrix.md and all deterministic positive/adversarial controls. |
| U07 | Parameterized model → initial simulation → native result checks → justified corrections → reruns until defined criteria or honest stop. | HH-06 + HH-10; no threshold/question alteration, no forced plausible result. |
| U08 | Long-running background work continues while the user does other things, with inspectable history and safe pause/resume. | HH-06/08/09/10; existing runner/Activity, durable leases/receipts, no browser keepalive authority. |
| U09 | Research parameter options, synthesize sources/ranges and guide model setup. | HH-07; verified source quotes/versions/basis and explicit approval for researched values. |
| U10 | A coherent model-agnostic workflow fits each template and selected Energy/Grid/Reliability/Commercial/Control module. | workflow-strategy.md, HH-09 adapter DAG, protocol/provider boundaries. Missing engines remain explicit unsupported states. |
| U11 | Reports and Excel files support repeated partial revisions from user feedback, with all requested parts tracked to closure. | HH-08 + HH-09 acceptance authority; exact artifact base/sections/ranges, independent checks and unresolved-item retention. |
| U12 | Reduce review effort where justified and tell clients what needs review, what checks passed and what is not evaluated. | HH-09 DecisionPacketV1, HH-08 scoped copy grants and shared review policy; evidence/risk/client choice, not model confidence. |
| U13 | Coordinator can delegate parallel research/synthesis/parameter proposals to two or three agents safely. | HH-09; first slice two read-only packet workers plus coordinator, configurable third only after its tested amendment/caps; one mutation authority. |
| U14 | Reuse current tools, native engines, approval/evidence systems and existing planned owners. | baseline.md and every allowlist. HH-06/07 attach to study-harness04/05/09; Assurance owns checked findings. |
| U15 | Independent agents review detailed specs; iterate fixes until a final agent gives GO to implement. | reviews/ ledger and exact review-target.json payload; no remaining material blocker. GO is technical spec readiness, not code/merge/domain approval. |
| U16 | Another implementation session can continue without guessing. | README.md, handoff.md, per-ticket files/symbols/schema/state/tests/dependencies/rollback, reviewed content digest. |

No requirement asks for arbitrary code execution, replacing domain engines, changing scientific pass thresholds, paid model runs during planning, guaranteed convergence of infeasible scenarios or automatic scientific/domain certification. If the accepted scenario is infeasible/unsupported or a subjective item needs a decision, retain it explicitly and ask for that decision; do not silently drop it or invent completion.
