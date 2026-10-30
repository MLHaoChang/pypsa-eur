<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Implementation handoff

## Live scope

This package is a research-to-implementation backlog. There is no new admitted dataset, importer or runtime change. The source assessment inspected master `aba252d2384f55116bbd15516a5b025951ba265e`; this branch starts at that master. At handoff, **LG-01 is the first assignment**. Read `README.md`, `spec.md`, `research-protocol.md`, the assigned issue and its planned validation cases before editing. `plan.json` indexes 19 tickets/95 planned cases and completion dependencies.

Do not interpret the ticket IDs as a mandate to implement every engine or admit every source. Missing public evidence, rights or a native consumer produces an explicit NEEDS INFO/NO GO and a useful safe fallback. Independent domain review and real-use calibration stay pending. Existing owner decisions and repository rules are read fresh, not copied into this handoff.

## Rebaseline before starting

1. Fetch actual master; read current AGENTS, steward, owner decisions, outsourced register and relevant harness/GUI instructions. Check whether another agent owns the canonical money-year or H2 transformer issue. Avoid duplicate changes.
2. Compare master/PR status with the baseline below. A merged PR may satisfy a dependency; a still-open spec does not provide runtime. Record the observed SHA, not this package's historical value.
3. Run the scoped source/consumer audit. Update allowed paths and regression names from code. No implementation changes outside the reviewed scope. Request engine/schema/UX/security review through the owner/coordinator when required; this package does not authorize external contact.
4. Claim the assigned local issue and record the exact research question/deliverable. Start with sources and native fields; do not start by installing an engine or scraping a large catalogue.

## PR/source reconciliation baseline (2026-10-25)

| PR | Exact inspected head | How to use it |
|---|---|---|
| #491 | `c35bfd0b7a218a343c9b5febc8f1b69aa26f0836` | Original cross-sector/source research; documentation only. Source IDs may need aliases, not duplicate admission. |
| #524 | `3a99ed44f6867a48d7f6a0251a7ffacd37a4885f` | NAM six-collection research; reconcile NAM-0 and LIB01–06 with these tickets. Do not merge its branch merely to get data. |
| #704 | `d7dea035757ea8ba14c2861a0eb4f4f920a95f75` | Template ledger appendix; does not fix money years or populate data. |
| #694 | `101e8745ca44eb45b20a8ebb1a1a076cf5edb2b8` | Profile tuning/compare backend; not a technical/commercial data collection. |
| #712 | `c117493f494a74d7d19d9a053286cb7f347730e9` | Optional manual envelopes; its runtime is an explicit apply dependency for footprint references until merged. No default OEM dimensions are supplied. |
| #711 | `bd26ea2cb8bfe06b9b302d4bf9853756a50432a9` | Scene-generation/AI/Blender specification and execution guide. No runtime campus generator or process binding is implied. |
| #527 | `5b2634355c218828e6f54fd6dd326987a8d38331` | Background rapid-load research; owner RLR-527 supersedes its proposed ticket/workflow framing. |

All these PRs were open/unmerged when refreshed for this package. This table does not assert CI, reviewer verdicts or merge readiness. Do not rewrite their branches from this task.

## Ticket delivery record

For each assignment, add a dated implementation record under this feature directory or its canonical feature, containing:

- assigned ticket, owner, fresh baseline and dependency/admission decisions;
- exact file allowlist and any approved scope amendments;
- exact source resource/hash, per-field source spans, licence/allowed scope, unverified/excluded cells and independent oracle;
- planned cases mapped to actual test functions; red-before-green evidence and scoped regression results;
- source head, applicable exact-head CI and review evidence; runtime ingestion/enablement state;
- source-data versus interpretation versus numeric evidence limitations;
- unresolved fields, fallback, next ticket and final handoff path.

Existing immutable packs/pins, user overrides and numerical hashes remain unchanged unless a new explicitly selected version is created. Research is allowed to conclude that a public field cannot be filled. No source-value guess, fabricated tested count or full-coverage approval.

## First implementer prompt

> Work on LG-01 in `.scratch/library-gap-implementation/issues/01-source-consumer-register.md`. Start from fresh master and read AGENTS, current steward, owner decisions and outsourced register. Follow this package's source protocol. Reconcile existing shipped Library paths, source rights, version/basis and #491/#524 identifiers into a reproducible source-to-native-consumer register; update stale statements without deleting historical evidence. No runtime changes or new source ingestion. Record baseline, exact paths, validation and unresolved decisions. Open one scoped documentation PR using the repository template and release fragment. Stop before LG-02 or any data/engine change until its dependencies and plan gates are met.

Skipped by this specification: actual source downloads/extraction beyond the previous assessment, ingestion, engine implementation, live fetch enablement, paid calls, contact with publishers/reviewers and merge. The 95 acceptance cases remain planned.
