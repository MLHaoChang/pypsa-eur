<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Fast critical-line evidence and precise assistant navigation

Status: claimed
Type: task

This is one illustrative domain fixture under the mandatory
[whole-application coverage contract](../application-coverage.md). Completing it
does not establish coverage of other result tabs, tools or parameter editors.

Independent of a voice provider. Read [the adopted architecture](../../../docs/superpowers/assessments/2026-10-10-hosted-assistant-architecture-decision.md)
and [spec](../spec.md). Preserve harness catalogue/dispatcher/route contracts.

Create one bounded, authorized load-flow aggregation service and expose a
read-tier `get_loadflow_summary` or equivalent `get_run_evidence` section. Current
results should work before historical artifacts; historical support reuses issue
06, returning unavailable until the referenced artifacts exist. Return exact
project/run/source/window, completion/freshness/convergence, top critical assets,
units, ranking convention, ratings/configured limits and missing evidence.

Reuse available result/compare services while distinguishing active-flow proxies
from AC apparent-power assessment. Test both terminals/ratings, optimized rating,
time-varying `s_max_pu`, incomplete data, zero/missing rating, weighted snapshots,
infeasible/unsolved/stale results and uncomputed AC/voltage evidence. Never present
an active-power ranking as proof of full AC thermal compliance.

Extend typed UI navigation to open `results_tab='loadflow'`, select the same
source/window and highlight/scroll to the requested line. Use asset-detail tools
for drill-down. Respect current mode/view availability and give a visible route
when the requested tab is hidden. The bottom Lines editing table is not a
substitute for a line-results view. Navigation acknowledgment includes matching
project/run/source/section/generation; late actions cannot switch a new project.

Acceptance: typed chat asks for current load-flow summary, top critical lines,
one line's parameters, a time-window follow-up and opening its result view. Assert
software-computed values and exact UI references, bounded payload/cache invalidation,
ACL, stale generation and tool parity. The same flow must work from every supported
harness provider before connecting voice. UI and assistant share the metric source.

## Comments

2026-10-11: First slice landed: read-tier `get_loadflow_summary` over the CURRENT results
(`services/loadflow_summary.py`, catalogue + `DISPATCHERS` + `TOOL_ROUTES`, results toolset;
tests `tests/test_loadflow_summary.py`, 43 passed). It aggregates the Results-tab sources
(`routers.results._result_df` LOPF/AC PF snapshot, `dispatch_status` freshness, `s_nom_opt`/`s_nom`
rating, `period_utils.snapshot_weights`, parity-checked against Compare's loading summary). Returns
exact project/source/window, freshness/solve state, ranked lines and transformers by peak
utilisation = flow / (rating * s_max_pu) over both terminals (time-varying `s_max_pu`, optimized
rating, weighted hours at/over limit), skipped branches (zero/missing rating, no flow data),
`missing_evidence`, and `claims.full_ac_thermal_compliance=false`. LOPF is labelled an
active-power proxy; `source='ac_pf'` uses apparent power only where reactive flows exist and
excludes unconverged snapshots. Unsolved/stale/running/infeasible/failed results and a missing AC
PF give typed `status: unavailable` payloads (`unavailable_kind`), never earlier numbers.
`run_id` (historical) resolves through the issue 06 store and answers
`historical_loadflow_unavailable` (`depends_on: issue-06 run evidence`) until a `loadflow` section
exists. Payload is bounded under the 4000-char per-result cap and carries an `evidence_token` for
cache invalidation. Inventory regenerated (224 tools).

Still open: the typed UI navigation (open `results_tab='loadflow'`, same source/window,
highlight/scroll to a line, ACK with project/run/source/section/generation, hidden-tab route),
the live-chat acceptance journey on every harness provider, and a shared client/server metric
source (the tab still computes loading in the browser from the same inputs and convention).
