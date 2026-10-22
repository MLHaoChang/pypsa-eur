<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-16 — parametrising several assets in one request

Status: ready-for-agent inside LC-04, after the storage/line slice (OJ-05/06)
Progress: Edit cards for bulk_update_components (one class) and solver config exist; no mixed-class card, relative/set changes, or other settings cards.
Type: task
Baseline: master `ae95a94a866730c0fe67fad37e238dc281786bd6`
Dependencies: LC-04 storage/line slice; LC-10 resolution ladder
Catalogue: [feature catalogue](../feature-catalogue.md); journeys OJ-21, OJ-22, OJ-23, OJ-26

## Problem

The owner wants to "update parameters of any asset … including several at
once". Three tools can write several assets, each with a different limit:

| Tool | Reach | Preview | Card today |
| --- | --- | --- | --- |
| `bulk_update_components` | one class, same `updates` to N names | none | Guided only (write tier) |
| `preview_project_changes` → `apply_project_changes` | 1–20 typed changes, 6 classes, an allowlist of ~27 attributes (`harness/catalogue.py:1890`) | old→new with units | apply is execution tier, carded in both modes |
| `update_component` per asset | any class and field | none | Guided only |

So "set L1 to 500 MVA, L2 to 450 MVA and the battery capital cost to 150
€/kW" has no single old→new card in Expert, relative changes ("+20 % on all
solar") have no defined arithmetic owner, and fields outside the preview
allowlist (Transformer, Bus, `standing_loss`, `lifetime`…) fall back to
uncarded writes, contrary to owner decision Q3.

## Scope

1. **One card for a mixed set.** A multi-asset edit across classes and fields
   produces one preview listing every `entity · field · old → new · unit`
   row, one approval and one atomic apply, in both modes. Reuse
   `preview_project_changes`; extend its class/attribute allowlist from the
   LC-01A schema (`get_component_schema`) rather than adding a second
   preview path. Fields the native schema marks read-only or computed refuse.
2. **Relative and set-based changes.** "+20 %", "halve", "all solar
   generators", "every line from bus 2": the server resolves the set and
   computes the new values; the model never does the arithmetic. The card
   shows the resolved set and its size.
3. **Atomic or explicit partial.** One invalid member refuses the whole
   batch, naming it (OJ-23). No partial apply without a new card.
4. **Undo.** After apply, offer `undo_last`; the undo is one step for the
   whole batch. Say results are now stale (LC-12 stale label).
5. **Retire uncarded multi-writes from chat.** In chat turns
   `bulk_update_components` and repeated `update_component` route through
   the preview card (Q3); the HTTP routes stay unchanged for the UI.
6. **Settings are parameters too.** `update_solver_config`, `update_meta`,
   `set_investment_period_weightings` and the economics basis are write tier,
   so Expert applies them uncarded today. Chat edits to them get the same
   old→new card (OJ-44).

## Not in scope

Topology changes (create/delete, LC-06 D02); time-series scaling (LC-06 D03);
sensitivities (LC-17).

## Done when

OJ-21, OJ-22, OJ-23 and OJ-26 pass in Tier A with native read-back of every
row; LC-D02 and LC-D09 batch cases pass; pilot gate unchanged.

## Rules folded in from #567 HV-I (data-to-model: equipment tables)

Source: draft PR #567 `.scratch/high-value-ai-study/` (commit e8d53410), research only; owner decision AI-567 (2026-10-21). Adopt the listed cases into this ticket's own numbering; each must name its token/packet limit.
- New: map an equipment table (CSV/XLSX) onto several assets with cell-level lineage, preview, then apply through LC-04 edit cards with native readback; manual mapping when no model is available.
- Load profiles keep the existing upload path (`upload_load_profile`, `profile_gen/load_upload.py`, pending profile store), not the Library.
- Deferred: PDF documents, tariff and PPA extraction.
- Cases: #567 I-series.
