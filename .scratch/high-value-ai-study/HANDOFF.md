<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Handoff: next high-value AI capabilities

**Latest review:** [current-master reconciliation and residual scope](11-CURRENT-MASTER-AND-REVIEW.md). Master now contains LC-23 S1/S2 and other foundations. Use the amended 08/09/10 specs and 53-case plan; preserve shipped work. The independent verdict is separate from runtime/CI/merge approval.

Status: implementation specifications for review; runtime unchanged. Do not implement automatically on reading this file.

## Latest request and concrete implementation entry point

The owner explicitly asked to turn the harness recommendations into implementation specifications and publish them in a PR. Read `08-HARNESS-IMPLEMENTATION-SPEC.md`, `09-HARNESS-DATA-AND-STATE-CONTRACTS.md`, `10-HARNESS-DELIVERY-AND-TESTS.md` and `harness-implementation-plan.json` first. These now specify seven delivery units, exact contracts/code targets/dependencies and 53 planned acceptance cases, rather than relying on this handoff or the older assessment as the build plan.

Fresh source for this concretization: `089aea276c75b098c8d349cb5fa0eb4cb7267d64`. #559 is merged; its owner decisions are now on master. #569 also merged LC-23's concrete design: brief plans use 24 steps/32,000 serialized characters, plain plans retain 12/16,000, with 1,800-char step arguments. Adopt it, not another task schema. A proposed native checkpoint reference on the existing stream binds the actual Continue click; a model/MCP resume alone cannot grant it. New context limits are packing bounds, not token/cost guarantees.

The 53 cases remain planned and unexecuted; this docs PR changes no runtime, although several foundations have since shipped on master. Historical 370-test/244-case evidence is not relabeled for this source. Current inventory/matrix checks passed (276 tools/17 tabs/20 panels; 170 controls/14 families/full coverage not established). Keep speculative H08 agents/code workspaces out of the build.

## Context to carry forward

Owner asked for higher-value additions beyond the three inexpensive AI improvements and a **separate PR**. This package proposes study completion, decision/robustness and data onboarding within existing native workspaces/companion. Original three remain useful quick wins; ranking is judgment awaiting real-use evaluation.

Read README, value assessment, all three capability specs, contracts, then implementation/test matrix. `implementation-plan.json` maps 48 planned acceptance cases to nine acceptance slices and existing owning packages. No new runtime function names or callable API are promised. Sources pinned in `native-evidence.json` are the baseline, not a claim of the latest master at your future session start.

Publication also reviewed the incoming master change to `49791da3e1197f3872b9273d37ca97c47c57b13d` (#557 titles/descriptions only); original baseline tests were not relabeled. Reassess any later change.

## Decisions that must survive a session boundary

- Keep six cards including Hand-off, max three sub-options; server support/readiness and off-card semantics win. Do not silently select a card.
- Follow PM-558/RES-CARDS/CARD-WS/AI-558 relay in open #559 at its pinned SHA, then recheck the merged owner log and current design. #558 stays research only; drop Manage/PM00/PM14, defer journal/pins/tags/archive. Preserve separate Safe restore work.
- Fit slices into W9/W10/LC-05/LC-12/LC-19/LC-20/LC-21/S5/S6/S7/Library as appropriate. Do not launch new top-level modules, runtime, preview path, deferred caches/endpoints or dollar-cost system. Do not start early or reorder queued owner work.
- AI explains/proposes; native engines calculate, authorize, execute, record and verify. Ordinary approval and per-source provenance remain required.
- BESS findings are domain-specific. LC-20/21 are planned boundaries; a number audit alone does not establish a correct recommendation. No fake confidence percentage.
- Structured file onboarding first, per-parser semantics intact. PDF/datasheet/tariff/PPA extraction is included as LC-16 slice 2 after spreadsheet mapping; each value needs page/quote and confirmation, plus admitted native semantics. No automatic file instruction execution, unknown-value fill, rights clearance, implicit uploads to paid providers or universal undo claim.

## Resume checklist

1. Inspect environment/network readiness without credentials disclosure; read root AGENTS, steward, harness README and current owner decisions.
2. Fetch/inspect actual master, #558, #559 and this PR. Reassess source pins and overlapping work; do not copy closed or superseded proposals into code.
3. Run `python .scratch/high-value-ai-study/validate_specs.py`. It verifies spec consistency only. Read VALIDATION.json for actual baseline tests versus future tests.
4. Obtain required product/design/domain reviews in the existing process before building UI/engine changes. The owner-authorized independent design review is recorded in 12; standing product/engine/UX reviewers were not contacted and their implementation gates remain separate.
5. Implement one admitted acceptance slice in the owning package, with its matrix cases, semantic tests, source-bound browser/effect evidence and normal CI/premerge requirements. Do not count baseline tests as new-feature acceptance.
6. Update outcome measurements and scope honestly. Independent domain review and real-use calibration remain pending by owner choice; no paid calls or reviewer contact are authorized by this handoff.

## Harness assessment supplement

The owner's subsequent voice request asked how to make the harness more powerful. Read `07-HARNESS-GAPS-AND-ROADMAP.md` and `harness-assessment.json` for the newer assessment on master `322df0cf495260442eb35bb9c0c5d8e11a274ffe`, selected official Claude Code references and 24 additional planned foundation checks. It is research, not runtime work. The original 161-test baseline and 48 capability cases retain their original meaning and are not merged into a larger claimed coverage count.

Priorities: complete native capability/evidence coverage and task-completion proof; improve structured context; widen checked analysis; extend admitted cross-domain study phases, with journey evidence throughout. Existing skills, MCP, replay, durable memory and browser continuation already exist. Do not propose them as entirely missing. Keep optional specialist reads/code workspaces conditional and later; no general terminal or new agent runtime is authorized.

On the historical assessment baseline, manual task reconciliation could record caller-supplied completion after confirmation; current master LC-23 S1 ignores those bytes and reads native evidence. The remaining scope is shared proof/legacy/crash hardening. New verified claims must independently read native evidence; confirmation alone is not engineering proof. Source inventory/matrix counts do not prove every feature works. Consult the supplement's completed validation records before reporting results.

This PR is not a merge/deployment request. No production activation, equipment control, two-case follow-up implementation or external account/data acquisition occurs here. Scope ends at publication of these specifications and their validation evidence.

Read OWNER-DISPOSITION.md before implementation: AI-567b allows a cross-card brief on the existing plan card and a bounded LC-05 step-cap increase; COP-1 requires a click for every write/run and Continue at each card boundary. The newer decision supersedes earlier deferrals of document extraction; no evidence-map or advisor module is added. #566 now has a merged reviewed card-workspace design.
