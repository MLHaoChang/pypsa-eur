<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Implementation-session handoff

## Entry recipe

1.Read README.md and spec.md, then user-requirements.md and the selected issue. Read the current repository AGENTS.md, pypsa-gui/AGENTS.md, harnessREADME, owner decisions and outsourced register. Read only branch-relevant linked references afterward; do not load every long spec into each model turn.
2.Verify planning base60d8332d64b2edef1b204fb5980311e6102229a8 and review-target.json. Recompute every listed file SHA and aggregate before relying on a verdict. reviews/ contains the actual independent technical reviews/fixes; a source SHA or GO for a different payload is insufficient.
3.Fetch current master and compare every allowed native/parent/provider/queue/artifact seam. If critical behavior changed, document exact delta and obtain scoped plan reassessment. Do not rebase/force-push someone else's branch or alter K/G-b/other outsourced scopes.
4.Choose one unblocked slice. Create its own branch/PR from fresh master following normal steward/GK/UX rules; the coordinator merges. This plan authorizes no production activation or paid call by itself.
5.Write the meaningful expected failing case on the pre-implementation head and compatibility positives first; prove intended behavior, not merely a missing import. Implement one vertical slice and run its exact relevant tests plus shared checks. Keep code changes inside the reviewed allowlist; extra files need a concrete amendment, not a guessed shortcut.
6.Update source/task/manifest/schema versions and release fragment accurately. Report exact commands/counts/source/fixture digests/skips, coverage and unresolved limitations. All current-head required remote steps must actually execute and pass; path-filter green is not a full suite. No skipped/loosened tests to get green.
7.Use handoff evidence to request the existing official reviews; this package's agent GO is technical design readiness, not official GK/UX/merge/domain approval. Never message domain reviewers, run paid services, deploy or merge unless separately authorized under the owner's rules.

## Dependency and shared-file order

HH-05Afirst. HH-01Aowns InputVersion/native mutation; HH-03A/Bowns provider lifecycle/strict spend; HH-09shared intent types may land before runtime with zero new authority. HH-02requirements and HH-04refs integrate those types. HH-10strict native candidate solve is required before HH-06autonomous solve/rerun. HH-07research, HH-08artifact revisions and HH-09coordinator runtime require their exact HH-03Bresource coverage and source/acceptance contracts. HH-05Bexpanded qualification is required for the whole product claim. Avoid simultaneous implementation PRs hand-merging shared loop, parent, card and native queue files.

## First bounded scopes

- HH-01A: native exact named Generator.p_nom/marginal_cost scalar updates only, including native defaults/candidate/undo/read-back discipline; unsupported unattended edits pause.
- HH-02: confirmed preserve_horizon + configured Generator.p_nom bound; result objectives and other fields require native adapters. Manual UI is not globally forbidden by an assistant requirement.
- HH-03A/B: declared main/reviewer/new-controller physical sends and fees with durable identity/reservation/settlement; no hard money claim without verified bound/rate capabilities. Legacy excluded media/probe calls remain explicit.
- HH-04: immutable internal saved Generator/read/preview and completed recorded-run references; no new public tool or live mutable object execution.
- HH-06/HH-10: saved generic single-period electrical LOPF isolated candidates, closed corrections, exact native criterion/evidence, at most the stricter approved solve limit. Do not claim every template/control/reliability/finance mode is covered.
- HH-07: research/read-only source ledger and approved parameter proposals; always card researched writes. Missing sources/rates/basis remain explicit not_available/proposal-only states.
- HH-08: copy-only supported text/format revisions first; factual/model/formula and unsupported artifact types follow their explicit adapter/review gate. All feedback items remain in shared acceptance ledger.
- HH-09: coordinator plus two immutable read-only workers initially; a third/recursive/parallel-mutating expansion requires its tested amendment and parent caps. No worker recommendation is an approved native effect.

## Completion and stop discipline

Completion requires every requested part's mapped native/artifact checks and required user decisions, not an LLM's “done.” Unresolved subjective/unsupported/infeasible/out-of-budget/uncertain work remains explicit, with next action and durable source/history. New feedback creates/revises intent without deleting earlier evidence. No approval, current fact, budget or native completion can be inferred from a summary, serialized reference, majority vote, missing log or expired lease.

If context runs low, save live state: selected issue/branch/fullhead, executed red/green evidence, pending exact checks, current review verdicts, unresolved findings/approved amendments, no-replay unknowns and the next concrete step. Keep standing policy in source docs, not copied into every status update.

## What has not happened

These are design/specification artifacts. The plan-writing task runs document/source-reference checks and independent agent reviews, not new application tests, native solves, model calls, deployment or domain calibration. Runtime functionality is unchanged until implementation PRs land. Current review status is authoritative only in the review manifest and final verdict file.
