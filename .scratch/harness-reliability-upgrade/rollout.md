<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Implementation rollout and recovery strategy

## Stage gates

Every slice has an exact baseline, allowlist, schema/state contract, red/green acceptance cases, compatibility tests and rollback. Read then-current source and owner decisions before implementation; changed critical paths require a scoped reassessment rather than guessed compatibility. Implement shared loop/parent/queue changes sequentially. Normal official repository GK/UX/premerge and exact-head CI gates remain; agent plan GO is not merge authorization.

| Stage | Deliverable | Activation requirement |
|---|---|---|
| A | HH-05A baseline characterization and negative-control infrastructure | Offline only; no runtime capability claim. |
| B | HH-01A exact guarded Generator scalar updates + shared InputVersion | Native/barrier/undo/receipt tests; unsupported/legacy unattended paths pause and require re-review. |
| C | HH-03A protected accounting +HH-03B new stage/report/worker sends; HH-09A protected intent/acceptance store and CAS APIs | Every physical send in enabled tasks has explicit supported bounds and durable accounting; unknown price/bounds prevent strict monetary activation. HH-09A lands types, persistence, receipts and reduction APIs together, with no worker dependency; types alone do not qualify the store. No child authority follows from it. |
| D | HH-02confirmed typed requirements and HH-04references | Explicit parent/intent binding; native effect guard; active data survives flags/restart without silent ignoring. |
| E | HH-10immutable candidate queue/snapshot pipeline | Real native isolated solve, strict durable fence, exact artifact/job/result provenance; no best-effort or resident-live fallback. |
| F | HH-06study cycle and HH-07research; HH-08artifact revisions; HH-09B two-worker coordinator | Exact registered profile/adapter/IntentContract, scope/resource/review grant and native receipts. All prerequisites physically implemented, not only declared in JSON. |
| G | HH-05Bcomplete workflow qualification | All fixed initial-scope positive/adversarial/integration cases execute; roadmap positives are listed as excluded, with mandatory unsupported-route controls with manifest/source identity, no missing required measurement or feature stand-in. |

HH-01PhaseB is HH-10's concrete queue task. A foundation-only release may be useful but must not be described as the completed hours-long autonomous product. Copy-only artifact formatting can ship before advanced factual/formula revision adapters, but the coverage packet must list those unsupported parts and preserve their open acceptance items.

## Data migration rules

- Native source projects/results, receipts and existing history remain authoritative. No automatic model mutation or solve while migrating a task, conversation, workbook or report.
- New typed requirements are not inferred from old list[str] notes. Explicit preview/confirmation creates versioned records with CAS/provenance. Generic checkpoint cannot write those records, accounting attempts or approved intent fields.
- HH-03migration preserves legacy amounts and unknowns; no new zero-cost baseline when history/receipts cannot establish prior usage. Supported activation follows its conservative opening policy. Accounting tombstones survive conversation deletion/hiding and day changes.
- Live reference observations expire on residency/rebind/revision changes; immutable historical references rebuild only through fresh ACL/integrity checks. No serialization grants permission.
- Legacy envelope/task snapshots are not silently regranted. Versioned isolated-candidate grants, exact fields/criteria and new acceptance manifests require explicit review.
- Artifact revision tasks create copies rooted at the inspected version/digest. Existing delivered/published files are not overwritten by migration. Rendered or imported documents remain data; parsing comments does not authorize changes.
- Controller/worker records are scoped to the exact parent intent triple and source revisions. In-flight old outcomes remain old; conversion cannot label them successful for a changed request.

## Feature flags and capability truth

Per-ticket flags default to their documented safe/off values. A flag is not a loophole to turn enforced records into ignored notes or delete unresolved liabilities. Product availability comes from backend capability and current flow verdicts. Unknown adapter/model/fee/migration data means unavailable or needs-review; no unsupported automatic downgrade from a claimed strict cap/evidence contract.

Client policy can preauthorize bounded read-only work and copy-only deterministic artifact corrections. It cannot alter hard native safety rules, grant through model narration or waive the existing card for researched parameter values. If review coverage is incomplete, retain needs_user/unsupported/incomplete disposition and required comments; a task is not complete merely because no runnable step remains.

## Pause, cancel and recovery

Pause/cancel stops new admissions and revokes the matching future step/worker generation. Running threads/SDK sends/native jobs may still finish; record actual native effect and cost on the original attempt. Unknown/partial effect blocks replay and new dependent actions until the existing explicit reconciliation/review contract resolves it. Do not free a lease/reservation or claim rollback just because a browser, worker or caller disappeared.

Server restart reconstructs leases/checkpoints/attempts from durable native records. A positive no-send/no-effect proof is distinct from missing data. Late receipts may settle original usage/evidence once, but cannot approve a new generation, advance a replacement chain or overwrite newer feedback/artifacts. Workers read immutable scoped packets; coordinator output joining and native commit remain separate steps.

## Rollback

Disable new task creation/worker dispatch first; retain records/versions/reservations and source policy. Existing unknown calls/effects are observed or safely blocked, not replayed. If an old binary cannot understand active enforcement/accounting/task schemas, protect affected operations with unavailable/needs-review rather than treating them as absent. Artifact final pointers and source projects stay unchanged. Re-enabling requires native schema/integrity/current-source validation and any required explicit regrant; no regenerated approvals.

## Operational evidence

For each release record exact source/tree/fixture/schema versions, activated adapter/provider/mode rows, configuration digest, migration outcome, per-slice test commands/counts/red controls and actual remote qualification steps. Logs expose concise user-useful reasons/changes/sources, not private chain-of-thought, credentials, signed URLs or raw foreign records. Review routing is an auditable evidence/policy decision, not a numeric LLM confidence score.

No production activation, paid experiment, external reviewer contact, deployment or claim of independent engineering approval is part of this specification-writing task.
