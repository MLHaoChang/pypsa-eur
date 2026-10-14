# Plan: M3, Results opens on one summary

**Date:** 2026-10-14. **Owner decision:** picked 14 Oct (relayed by the coordinator), after M5: "Results opens on
one summary of cost, emissions, adequacy and key capacities, before the detailed tabs." **Constraints from the
coordinator:** frontend only, reading existing result endpoints (a new route or chat tool is asked first); stay out
of `AssistantDock` and `ChatPanel` (the companion stream edits them).
**Source:** UX assessment `2026-10-05-ux-ui-overall-assessment.md`, IA-5 and M3 ("answer-first page; open Summary
after a solve"). The assessment's regrouping of the other tabs into System / Reliability / Investment is **not** in
this slice: the owner asked for the summary only.

## 1. What is wrong today

Results opens on Dispatch (`Results.tsx` `loadInitialTab`), or on the last tab used. No page answers "what did this
solve cost, what did it emit, was demand met, what got built": the numbers live in four tabs (Capacity Expansion,
Emissions, Lost load / Adequacy, Economics), and the objective is not shown on Results at all.

## 2. Target

A first tab, **Summary** (`summary`), with four cards and a status line. Each card links to its detailed tab.

| Card | Shows | Endpoint (existing) | Engine source |
|---|---|---|---|
| Status line | condition, solve time, objective | `simulationApi.getStatus` (`nk(p,'simulationStatus')`) | `routers/simulation.py` `_compute_run_objective` (`n.objective + n.objective_constant`) |
| **Cost** | total system cost (annualised, €/yr), split capital vs operating, of which new build | `resultsApi.getCostBreakdown` (`nk(p,'results','cost_breakdown')`) | `services/results/cost_breakdown.py` `compute_cost_breakdown` (`n.statistics()` CAPEX + FOM + OPEX; `capex_expansion`) |
| **Emissions** | total tCO₂; each active CO₂ cap: used / cap, binding, shadow price €/tCO₂ | `resultsApi.getEmissions` (`nk(p,'results','emissions')`) | `services/results/emissions.py` `compute_emissions` (caps are `primary_energy` constraints on `co2_emissions`; price is `mu`) |
| **Adequacy** | unserved energy (MWh), its cost at VoLL, loss-of-load hours; the reserve margin met / not met when one was set; the enforced reliability target when one was set | `resultsApi.getLostLoad()` (unwindowed key `nk(p,'results','lost_load')`, shared with the Overview tab), `getReserveMargin`, `getAdequacy` | `services/solver/assumptions.py` `_capture_and_remove_slacks` + `services/adequacy/metrics.py`; `services/adequacy/report.py` |
| **Key capacities** | the largest carriers by capacity (MW), each with existing and new build | `resultsApi.getStatistics` (`nk(p,'results_statistics')`, the only live endpoint with installed and optimal capacity per carrier) | PyPSA `n.statistics()` Installed Capacity / Optimal Capacity (`services/results/statistics.py`) |

Rules:
- **No value is invented.** Every number is a field an endpoint already returns, or one subtraction of two: new
  build per carrier = max(0, Optimal Capacity − Installed Capacity) (`n.statistics()`; the same rule
  `capex_expansion` uses, since an extendable asset can be optimised below its starting size), and the
  new-build capital is `capex_expansion` itself. Both are named in the card's tooltip.
- **The statistics rows** arrive with unnamed index columns from PyPSA 1.1 (`level_0` = component, `level_1` =
  carrier); a multi-period table has "<metric> | <period>" columns. Capacities are Generator and StorageUnit (MW)
  and Store (MWh); branches and loads stay on their tabs. With no period selected, a multi-period run shows the
  last period's fleet (the end of the horizon).
- **Absent is not zero.** Lost load 204 (VoLL = 0) reads "not modelled (no VoLL set)", not "0 MWh"; a 200 with
  `total_mwh: 0` is a measured zero. No reserve margin, no target, no cap: the row is omitted, not shown as met.
- **LOLE / EUE** come only from a study (screening or Monte Carlo); the Summary links to Adequacy for them rather
  than starting a computation (`/results/copt` computes on every GET).
- **Multi-period:** the shared period selector applies (`useResultsFilter().selectedPeriod`); null shows the horizon
  totals the endpoints already sum, a period picks its `by_period` entry.
- **Shed-load cost** is shown on the Adequacy card, not added to the total cost (cost breakdown excludes it), and
  the card says so.
- Labels follow `pypsa-gui/CONTEXT.md` ("Capital cost (annualised)", never CAPEX).

## 3. Files

| File | Change |
|---|---|
| new `pages/results/SummaryTab.tsx` (+ test) | the four cards and the status line |
| `pages/Results.tsx` | `summary` first in `TABS`, `VALID_TABS`, the compare-tab map (→ `overview`); the opening rule (§5) |
| `harness/catalogue.py` | `"summary"` added to `RESULTS_TAB_ENUM`, so the assistant can open it (the inventory pins Results' tabs to that enum). **The one chat-schema change; no tool, no route.** The inventory is regenerated with `--write`. |

Not touched: `AssistantDock`, `ChatPanel`, any backend route or service.

## 4. Tests (gates)

- Each card renders from mocked payloads, with the engine-source tooltip; 204s read "not modelled" / omit the row.
- New build = optimal − installed per carrier from a statistics payload, single- and multi-period columns.
- The period selector switches every card to that period.
- Card links open their tabs; Results opens on Summary per §5; the assistant's `results_tab: summary` opens it.
- Inventory and control-matrix checks; the release-note fragment.

## 5. Open points (to the owner, via the coordinator)

1. When Results opens on Summary.
2. Whether Guided mode shows it.
3. Whether "since last solve" deltas are in this slice.

## 6. As built (14 Oct, first cut, recommended answers assumed)

- `pages/results/summary.ts` (pure views: cost, emissions, capacities, the reserve-margin verdict) and
  `SummaryTab.tsx`; the status line also shows the LP objective, with a tooltip on how it differs from the
  reported cost.
- Opening rule: a finished solve is told apart by condition, objective and solve time (`results:solve-seen`);
  a solve not seen yet opens Summary unless a tab was asked for explicitly. Default tab for a new browser:
  Summary.
- Guided shows Summary first, before Adequacy and FMEA.
- Per period, the cost card has no new-build line (`cost_breakdown.by_period` has no `capex_expansion`), and
  unserved energy stays the horizon total (labelled so); shed hours follow the period.
