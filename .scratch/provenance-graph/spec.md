# Provenance graph: one staleness and "why this number" query

Asked by the owner on 2026-11-08. Owner decisions: COPILOT-1, TCU07-TRIAGE-1, MEMORY-1, PR-SIZE-1.
Reviewer: GK B (app, harness, finance).

## Problem

Every surface that shows a derived number decides on its own whether that number is still current. The survey below
counts about twenty hand-rolled checks. Each one compares a recorded input with the current input in its own way
(a hash, a uid, a recorded scale value, a timestamp), and each surface copies the verdict onto its own rows. When a
surface builds its own rows, the verdict gets lost. Issue TCU-07 is that case: the Guided ledger shows a cost priced
through a DC/AC ratio as fresh after the ratio changed, while the asset card shows it as out of date.

The assistant also cannot say why a number has its value. It can read a calculation's source
(`harness/calculations.source`) or a field's provenance entry, but it cannot follow the chain from the number back
through the price it was computed with to the input that set that price.

## What it is

A small, typed graph held in process and rebuilt from records the app already stores. It is not a graph database,
and it is not GraphRAG: no model extracts nodes or edges from text. Every node and edge comes from a typed record
that exists today, or from one that a slice adds on purpose.

### Node kinds

| Kind | What it is | Identity (built on) |
|---|---|---|
| `input` | A value a person, pack, template or measurement set: an asset field, a ledger row, a commercial cell | `(component_class, name, field)` from `n.meta["asset_params"]` (`services/asset_schema/provenance.py`); ledger row key; commercial key `<target>:<entity>/<path>` (`document_values_commercial.provenance`) |
| `derived` | A value computed from inputs: a priced cost, an annualised cost, a calculation metric | metric id from `harness.calculations.DEFINITIONS` and its window from `harness.fact_identity.window`; or the priced field it replaces |
| `run` | A solve, study, load flow, investment case or campus study that produced results | its input fingerprint (`run_gates.fingerprint`, `run_hashes.fork_file_hash`, the investment-case key) |
| `surface` | Where a number is shown: the asset card, its read-out, the Economics costs table, a Guided ledger row, a report cell, a chat answer | a stable surface id, such as `asset_card:<class>/<name>/<field>` |
| `memory` | Reserved. An assistant memory item (MEMORY-1, `.scratch/assistant-memory/spec.md`): a reusable parameter offered next to a field | Not built until the memory work lands; the kind and its edges exist in the type so that adding them is not a schema change |

### Edge kinds

| Edge | From → to | Meaning |
|---|---|---|
| `derived_from` | derived → input, derived → derived | the value was computed from this one |
| `priced_through` | derived → input | a price was scaled by this input (TCU-05: `dc_ac_ratio`, a Link's output efficiency); carries the value recorded at pricing time |
| `stamped_with` | input → (source) | the provenance stamp: status `default`/`customised`/`measured`, source id, source year, currency year, approver |
| `computed_in` | derived → run | the value was produced by this run; carries the run's fingerprint at that time |
| `shown_in` | derived or input → surface | this surface shows the value |
| `recalled_from` (reserved) | input → memory | the value was offered from a memory item (MEMORY-1). Never applied silently (COPILOT-1) |

Each edge that can go stale carries the recorded value it was made with (a scale value, a hash, a uid). The graph
compares that recorded value with the current one; the producer modules still decide what "current" means. The graph
does not recompute anything.

### The two queries

1. **`staleness(node) -> Verdict`**: walk the `derived_from`, `priced_through` and `computed_in` edges. The node is
   stale when any edge's recorded value no longer matches its current value, or when the current value cannot be read
   (fail closed, ADR-0001: unknown is not current). An input the graph does not model, or a node with no recorded
   input, reads `uncompared` and is named in the verdict, never fresh (GK B P1, #1170). The verdict lists every edge that broke, with the input and the
   two values, so the wording is the same on every surface (`staleNote` in
   `frontend/src/components/ProvenanceChip.tsx:60` becomes a formatter of this verdict).
2. **`why(node) -> Walk`**: the same walk, returned as an ordered list of steps (value, unit, source stamp, the edge
   that connects each step to the next). The assistant gets this as a read tool: "why is this cost 812 EUR/kW?" →
   the pack price, the DC/AC ratio it was scaled by, the stamp of each, and whether each is still current.

A surface asks "is what I show stale?" through its `shown_in` edge. It no longer re-derives staleness from its own rows.

### What it is not

- Not a store. The graph is rebuilt from `n.meta`, the study tables and the run records on each read, or cached per
  network version. It writes nothing of its own; `provenance.py` stays the only writer of `asset_params`.
- Not a recompute engine. It compares recorded and current values. It does not price or solve.
- Not a migration of every check at once. Each slice moves one family of checks, and each is one PR (PR-SIZE-1).

## Survey: hand-rolled staleness checks on master `64686f9d6`

Found with `grep -rnE "_stale|stale_keys|staleNote|out[- ]of[- ]date|staleness|freshness|fingerprint"` over
`pypsa-gui/backend` and `pypsa-gui/frontend/src` (tests excluded). "Slice" names the issue that moves it.

### Money path (slice 02)

| Where | What it compares |
|---|---|
| `pypsa-gui/backend/services/asset_schema/provenance.py:165` `staleness` | recorded scale input (`dc_ac_ratio`, Link efficiency) vs the column pricing would read now (TCU-05) |
| `pypsa-gui/backend/routers/network.py:802` | copies `staleness` onto each Economics cost row |
| `pypsa-gui/backend/routers/network.py:818` | copies `staleness` onto the asset card's provenance read |
| `pypsa-gui/backend/services/study/asset_view.py:181` `expert_edits` / `:207` `mark_expert_edits` | Guided ledger rows vs the fork's asset fields; carries no `stale` (TCU-07) |
| `pypsa-gui/backend/routers/studies.py:1074` | serves the marked ledger without staleness |
| `pypsa-gui/frontend/src/components/ProvenanceChip.tsx:60` `staleNote`, `:66` `staleBody`, `:86` `StaleChip` | wording of the out-of-date note and chip |
| `pypsa-gui/frontend/src/layout/properties/AssetFields.tsx:57`, `:351` | asset card and read-out call `staleNote` per field |
| `pypsa-gui/frontend/src/pages/economics/AssetCostsTable.tsx:28` `staleOf` | first stale scaled money field of a row |
| `pypsa-gui/frontend/src/pages/decision/LedgerReview.tsx:67` | ledger row shows provenance label only, no out-of-date note (TCU-07) |
| `pypsa-gui/backend/services/document_values_commercial.py:583` `provenance`, `:634` `_live` (slice 08) | `commercial.provenance` cell entries and whether the cell still holds the approved value |

### Runs and results (slice 04)

| Where | What it compares |
|---|---|
| `pypsa-gui/backend/services/run_gates.py:152` `_fresh`, `:799` `fingerprint` | results' input fingerprint vs the current inputs (LC-12) |
| `pypsa-gui/backend/services/run_envelope.py:147` `_fingerprint`, `run_evidence.py:862` `_input_fingerprint` | the same fingerprint, computed twice more for the envelope and evidence |
| `pypsa-gui/backend/services/flow_service.py:865` `_freshness`, `:888` `_loadflow_freshness`, `:913` `_freshness_by_kind` | per run kind: `fresh`/`stale`/`running` |
| `pypsa-gui/backend/services/dispatch_status.py:164` `has_fresh_dispatch` | dispatch results present and current |
| `pypsa-gui/backend/harness/edit_card.py:387` `_results_stale` | the edit card warns that results go stale |
| `pypsa-gui/backend/services/chat_tools.py:807` `_with_freshness` | stamps freshness on chat tool results |
| `pypsa-gui/frontend/src/hooks/useStaleRunKinds.ts:9`, `:21`; `layout/AppHeader.tsx:623` | header sentence "… results are older than the inputs" |
| `pypsa-gui/frontend/src/site3d/useSiteResults.ts:116`, `:125` | 3D results older than `freshSince` (client-side timestamps) |
| `pypsa-gui/frontend/src/pages/results/ReserveCommitments.tsx:161` `reservesOutOfDate` | reserve payload out of date |

### Studies, reports and the investment case (slice 05)

| Where | What it compares |
|---|---|
| `pypsa-gui/backend/services/study/report.py:623` `stale_reasons` | report vs study, ledger, each fork's `network.nc` hash and engine digest |
| `pypsa-gui/backend/routers/studies.py:1519` `_report_stale` | live reasons for the routes (one hash rule) |
| `pypsa-gui/backend/services/study/findings.py:1232` | fork file hash / compiled ledger match (`run_hashes`) |
| `pypsa-gui/backend/services/finance/investment_case_runner.py:145` `staleness` | stored report key vs the current key; unreadable key is stale |
| `pypsa-gui/backend/routers/results.py:1738` `_ic_campus_digest` | the `extra_assets` part of the investment-case digest |
| `pypsa-gui/backend/services/commercial/participants.py:673` `_stale_hub` | hub inputs vs value-flow config |
| `pypsa-gui/frontend/src/pages/decision/ReportStep.tsx:26` | parses `stale_reasons` text with a regex to decide whether a run is needed |
| `pypsa-gui/frontend/src/pages/results/investment/financeModel.ts:636` `studyStale`; `InvestmentCaseView.tsx:443` | three `stale` flags OR-ed together |
| `pypsa-gui/frontend/src/pages/hubDesign/chapters.ts:95`, `:230`; `cards/ResultsCard.tsx:77` | the same OR, and `dispatch === 'stale'` |
| `pypsa-gui/frontend/src/components/ChatLaunchGreeting.tsx:173` | review `stale` flag |

### Campus, site scene and other (slice 06, or left alone)

| Where | What it compares |
|---|---|
| `pypsa-gui/backend/services/campus_electrical_service.py:400` `_dips_out_of_date`, `:709` `_library_stale`, `:732` `_stale`, `:1927` `_least_cost_fresh`, `:1958` `_milp_stale`; `:224`/`:235` fingerprints | campus study and library hashes vs current profile and library |
| `pypsa-gui/frontend/src/pages/CampusDipsRestartSection.tsx:258` | `outOfDate` set per unit |
| `pypsa-gui/frontend/src/pages/DcArchitectureSection.tsx:97` | result key vs current spec key |
| `pypsa-gui/backend/services/site_bindings.py:60` `stale_keys`; `routers/site_scene.py:137` | site binding's recorded uid vs the live component |
| `pypsa-gui/backend/services/site_scene/associations.py:522` `_stale` | the same uid check (D06) |
| `pypsa-gui/backend/services/time_aggregation_service.py:82`, `:96` | config and weights fingerprints |
| `pypsa-gui/backend/services/adequacy/portfolio.py:149` `network_fingerprint` | adequacy cache key |
| `pypsa-gui/backend/services/assistant_tasks.py:257` `_fingerprint`, `:266` `_stale` | an assistant task's brief vs the last plan step's inputs (SC-11) |
| `pypsa-gui/backend/services/assistant_tools.py:746` `_preview_fingerprint` | chat preview vs current context |
| `pypsa-gui/backend/harness/loop_notice.py:89` `_repair_stale` | loop notice file repair (not a value's staleness: leave alone) |
| `pypsa-gui/backend/harness/edit_card_library.py:115` `_stale` | exception classifier (not a value's staleness: leave alone) |
| `pypsa-gui/frontend/src/components/series/SeriesKit.tsx:399` | `current_changed_since_refinement` error kind (an edit conflict: leave alone) |
| `pypsa-gui/frontend/src/components/GuidedTour.tsx:419` | tour belongs to another project (UI state: leave alone) |

Cache keys (adequacy, time aggregation, previews) are not shown to a person and stay out of the graph (GK B plan GO,
ruling (c)); a test holds that no cache key reads a graph node.

## Slices

Each slice is one PR in GK B's area, about 1,500 changed lines or fewer (PR-SIZE-1). UI parts need the UX reviewer's
OK as well.

| Issue | Slice | Status |
|---|---|---|
| 01 | Design plan: module, types, the two queries, the slice list, plan GO from GK B | done (#1302) |
| 02 | Money path: graph core plus the asset card, Economics costs table and Guided ledger; absorbs TCU-07 | ready-for-agent |
| 03 | "Why this number": the assistant's read tool over `why(node)` | needs-triage, blocked by 02 |
| 04 | Runs and results freshness | needs-triage, blocked by 02 |
| 05 | Studies, reports and the investment case | needs-triage, blocked by 04 |
| 06 | Campus and site-scene checks | needs-triage, blocked by 02 and #1295 |
| 07 | Memory nodes (`recalled_from`) | done |
| 08 | `commercial.provenance` cells (GK B ruling (b)) | in progress |

## Acceptance (whole feature)

- One function answers staleness for every number a person sees on a migrated surface; no migrated surface keeps its
  own comparison.
- The out-of-date wording comes from the verdict, so the asset card, the Economics table and the Guided ledger say
  the same thing for the same cost (TCU-07 closed).
- An unreadable current value is stale, never fresh (fail closed), on every migrated surface.
- The assistant can answer "why this number" for a money value with each step's value, unit, stamp and freshness.
- The graph writes nothing; `provenance.py` remains the only writer of `asset_params`.

## Links

- TCU-05: `.scratch/typed-cost-units/issues/05-dc-priced-cost-goes-stale-when-the-ratio-changes.md`
- TCU-07: `.scratch/typed-cost-units/issues/07-guided-ledger-does-not-show-out-of-date-cost.md` (absorbed by issue 02,
  TCU07-TRIAGE-1)
- Memory: `.scratch/assistant-memory/spec.md` (MEMORY-1)
- Harness contract: `pypsa-gui/backend/harness/README.md`

Plan GO from GK B, 2026-11-09, with conditions P1–P8: `issues/01-design-plan.md`, `## Comments`.
