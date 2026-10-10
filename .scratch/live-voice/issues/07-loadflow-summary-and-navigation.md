<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Fast critical-line evidence and precise assistant navigation

Status: ready-for-agent
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
