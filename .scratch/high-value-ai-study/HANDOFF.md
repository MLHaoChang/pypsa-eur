<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Handoff: next high-value AI capabilities

Status: specification/research only. Do not implement automatically on reading this file.

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
- Structured file onboarding first, per-parser semantics intact. PDF family support needs its own admitted adapter. No automatic file instruction execution, unknown-value fill, rights clearance, implicit uploads to paid providers or universal undo claim.

## Resume checklist

1. Inspect environment/network readiness without credentials disclosure; read root AGENTS, steward, harness README and current owner decisions.
2. Fetch/inspect actual master, #558, #559 and this PR. Reassess source pins and overlapping work; do not copy closed or superseded proposals into code.
3. Run `python .scratch/high-value-ai-study/validate_specs.py`. It verifies spec consistency only. Read VALIDATION.json for actual baseline tests versus future tests.
4. Obtain required product/design/domain reviews in the existing process before building UI/engine changes. No review was contacted or approval claimed in this research session.
5. Implement one admitted acceptance slice in the owning package, with its matrix cases, semantic tests, source-bound browser/effect evidence and normal CI/premerge requirements. Do not count baseline tests as new-feature acceptance.
6. Update outcome measurements and scope honestly. Independent domain review and real-use calibration remain pending by owner choice; no paid calls or reviewer contact are authorized by this handoff.

This PR is not a merge/deployment request. No production activation, equipment control, two-case follow-up implementation or external account/data acquisition occurs here. Scope ends at publication of these specifications and their validation evidence.
