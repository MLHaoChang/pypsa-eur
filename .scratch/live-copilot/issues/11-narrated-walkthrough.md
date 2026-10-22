<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-11 (VS-2) — narrated walk-through and "show me something else"

Status: done (#410, 14eab4b9)
Type: task
Baseline: master `d006f4beaa302b97432c65757762ae180222731a`
Dependencies: LC-09; LC-14 navigation fixes for snapshot and asset steps

## Problem

The local tour shows a fixed blurb per tab and walks only Load flow for current
results, only in Expert (`frontend/src/components/liveWalk.ts:8-23`,
`:126-131`). The prompt tells the model to open each tab and "say what that
tab shows" (`harness/prompts/assistant_stance.md:22-25`) but gives it no
read per tab, so it can describe the tab and not the data.

## Scope

1. **One read per tab.** A table (in the prompt and in a test) mapping each
   results tab to its read: summary → `get_project_results_summary`;
   capex → `get_results(cost_breakdown)`; dispatch → `get_results(generators)`
   and storage kinds; loadflow → `get_loadflow_summary`; prices →
   `get_results(prices)`/`price_drivers`; economics → `asset_economics`;
   emissions, curtailment, lostload → their kinds; adequacy and fmea →
   `get_adequacy_results`; storage → `storage`; investment →
   `explain_investment`; overview → multi-period kinds.
2. **Walk.** For each step: read, open, say two or three key points from the
   read, then stop and offer next. The user's "next", "stop", "go back" or a
   manual navigation controls it. A tab with no data is skipped and named.
3. **Something else.** List the sections not yet shown that have data, one
   line each, and open the one picked.
4. **Grounding.** Every number in a step comes from that step's read (the
   pilot gate's check). Tabs without a source-bound ACK say "opened", not
   "showing", until LC-03 adds their adapter.
5. **Guided.** Uses Guided's tabs and labels; no mode switch.

## Done when

OJ-02 and OJ-03 pass in Tier A; the per-tab read table has a test that fails
when a tab is added without a read.
