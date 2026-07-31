# Asset Detail — per-asset results evaluation — design

**Date:** 2026-07-31
**Branch:** `feature/local-app-impl`
**Relates to:** every existing `pages/results/*` tab (this is the transpose of all of them)

## Goal

Today the Results panel slices results **by metric**: ten tabs, each aggregating
across every asset in the network. There is no way to ask the opposite question —
*"tell me everything about Gas 1"*.

This adds an eleventh Results tab that slices **by asset**. The user searches for
one asset, picks a result category, ticks the metrics that interest them, and gets
them as numbers, optionally as charts, and exports both. Categories and metrics
that cannot apply to the selected asset grey out with a reason.

The same backend endpoint serves the UI and three new chatbot tools, so the agent
can answer the question in chat, open the tab already configured, and produce the
export — and its numbers can never disagree with the screen.

---

## Why now

The data already exists. `routers/results.py` is ~3 700 lines serving 28 endpoints,
almost all of which return `{index, columns, data}` matrices spanning every asset
of a class. Slicing one column out of one of those is trivial; what is missing is
the *inverse index* — given an asset, which of the 28 endpoints have anything to
say about it, and which do not.

That inverse index is the whole feature. Everything else is presentation.

## Constraints

**The results shell already owns cross-cutting state.**
[`Results.tsx`](../../../pypsa-gui/frontend/src/pages/Results.tsx) holds the horizon
filter (`fromIso`/`toIso`), the multi-period strip (`selectedPeriod`), the solve
status header and the A|B compare rail, and passes the first two down through
`ResultsFilterProvider`. Anything living outside that shell has to re-implement
them. This is the decisive argument for the new view being a *sub-tab* rather than
its own sidebar panel.

**Every `/results/*` endpoint 204s unless dispatch is `fresh`.**
`_dispatch_ready(n)` requires `dispatch_status(n) == "fresh"`, which compares each
`_t` table's shape and column set against the current component index. A network
mutated after solving reports `stale` and every result endpoint goes dark. The new
endpoint must behave identically — but the tab still has to be useful before a
solve, so its *identity and parameters* path cannot share that gate.

**Two asset families are deliberately invisible.**
`PyPSAService` marks solver-internal rows in a per-context transient registry:
VOLL slack generators (`__voll_<bus>`, one per bus) and vintage clones
(`<name>@<year>`, one per investment period for an extendable asset).
`routers/network.py::_get_component` filters both out of every asset list. The
picker must match that, or users will select `__voll_N1` and believe it is a
generator.

**The Results tab strip is already full.** Ten tabs plus a Compare button.
An eleventh needs the strip to scroll.

**Multi-period changes result shapes.** `n.statistics()` puts the period in the
*columns* MultiIndex, not the row index; `n.snapshots` becomes a MultiIndex whose
level-1 carries the base year regardless of which period is selected. Both traps
are already documented in CLAUDE.md and already handled by
`services/period_utils.py` (`period_years_map`, `years_for_period`,
`snapshot_weights`, `is_period_only`). Reuse them; do not re-derive.

---

## Decisions

### D1. A backend metric registry is the single source of truth

One declarative table in `services/asset_results/registry.py` defines every metric
exactly once:

```python
Metric(
    id="curtailment",
    label="Curtailment",
    unit="MW",
    kind="series",                 # "series" | "scalar"
    category="dispatch",
    classes=("Generator",),
    origin="derived",              # "output" | "input" | "derived"
    formula="p_nom_opt × p_max_pu − p",
    requires=(Requires.DISPATCH, Requires.P_MAX_PU),
    compute=_curtailment,
)
```

The endpoint returns each metric's `label`, `unit`, `origin`, `formula` and
resolved `status` alongside the data. The frontend renders what it is given and
holds **no metric knowledge of its own**. Adding a metric is one Python edit plus
one test; nothing in TypeScript changes.

A metric may also declare `source_override`. `v_mag_pu`, `v_ang` and every `q`
attribute exist only in the AC PF snapshot — `/results/voltages` already defaults
to `source="ac_pf"` for exactly this reason. Those metrics set
`source_override="ac_pf"` so they read the right snapshot whatever the panel's
toggle says, and resolve to `blocked` ("AC power flow has not been run") when that
snapshot is absent. Without the override, ticking a voltage while the toggle sits
on `lopf` would silently serve nothing.

This is the load-bearing decision of the whole design, which is why D17 sequences
the build to validate it against one component class before it is replicated
across the other seven.

### D2. Eight categories, reusing the existing tab vocabulary

`summary` · `capacity` · `dispatch` · `storage` · `loadflow` · `prices` ·
`economics` · `emissions`.

Six of the eight names already appear on the Results tab strip, so users learn one
vocabulary. Curtailment folds into `dispatch` (it is a generator metric), lost
load folds into `dispatch` for Loads, and storage cycling folds into `storage`.
`summary` is new and applies to every class.

Applicability matrix (● full · ○ partial, AC-PF- or carrier-dependent · ⊘ n⁄a):

| | summary | capacity | dispatch | storage | loadflow | prices | economics | emissions |
|---|---|---|---|---|---|---|---|---|
| Bus | ● | ⊘ | ⊘ | ⊘ | ● | ● | ○ | ⊘ |
| Generator | ● | ● | ● | ⊘ | ○ | ● | ● | ● |
| Load | ● | ⊘ | ● | ⊘ | ○ | ● | ● | ⊘ |
| Line | ● | ● | ⊘ | ⊘ | ● | ● | ● | ⊘ |
| Transformer | ● | ● | ⊘ | ⊘ | ● | ● | ● | ⊘ |
| Link | ● | ● | ● | ⊘ | ● | ● | ● | ○ |
| StorageUnit | ● | ● | ● | ● | ○ | ● | ● | ⊘ |
| Store | ● | ● | ● | ● | ⊘ | ● | ● | ○ |

`ShuntImpedance` and `GlobalConstraint` are out of scope: the former is
`p`/`q`-only and effectively unused in this GUI's networks, the latter is not an
asset (its `mu` is already surfaced in the Emissions tab as the CO₂ price).

### D3. Metric inventory

Derived from the installed PyPSA's `component_attrs/*.csv` where `status=Output`,
plus the interpretive inputs admitted by D4.

**summary** — all classes. Identity (name, class, carrier, bus/bus0/bus1), the
static parameters that matter for that class, and solve provenance. No series.
This is the only category that works on an unsolved network (D13).

**capacity** — Gen, Line, Transformer, Link, StorageUnit, Store. Scalars only:
`p_nom`/`s_nom`/`e_nom` vs `*_nom_opt`, the expansion delta, the configured
bounds, annualised CAPEX, and — multi-period only — the per-vintage split
recovered from the `<name>@<year>` rows (D12).

**dispatch**
- *Generator* — series `p`, `p_max_pu`, available (`p_nom_opt × p_max_pu`),
  `curtailment`, `capacity_factor`, `status`, `start_up`, `shut_down`;
  scalars energy, full-load hours, mean CF, curtailed energy and %, peak/min/mean,
  zero-output hours, hours at `p_nom`, max ramp up/down, start and shutdown counts,
  committed hours.
- *Load* — series `p` (served), `p_set` (requested), `unserved`;
  scalars energy, peak, load factor, unserved energy, unserved hours.
- *Link* — series `p0`, `p1`, throughput, realised efficiency;
  scalars energy in/out, losses, utilisation, forward/reverse split, starts.
- *StorageUnit* — series `p`, `p_dispatch`, `p_store`, `spill`;
  scalars discharged, charged, spilled, realised round-trip efficiency.
- *Store* — series `p`; scalars in/out energy.

**storage** — StorageUnit and Store only. Series `state_of_charge` / `e`, SOC %
(`soc / (p_nom_opt × max_hours)` and `e / e_nom_opt`), `spill`; scalars equivalent
full cycles, max/min/mean SOC, hours at full, hours at empty, throughput.

**loadflow**
- *Line, Transformer, Link* — series `p0`, `p1`, loading % (`|p0| / *_nom_opt`),
  losses (`p0 + p1`), `q0`, `q1`; scalars max loading, hours above 90 %, congested
  hours, total losses and loss %, max flow, direction split.
- *Bus* — series `v_mag_pu`, `v_ang`, `p`, `q`; scalars min/max voltage, hours
  outside 0.95–1.05 pu, net import/export.
- *Generator, Load, StorageUnit, Store* — `q` only, hence ○.

**prices**
- *Bus* — series `marginal_price`; scalars mean/min/max, standard deviation, hours
  above a configurable threshold.
- *Generator, StorageUnit, Store* — series `mu_upper`, `mu_lower`, plus the
  class-specific duals (`mu_ramp_limit_up/down`, `mu_p_set`,
  `mu_state_of_charge_set`, `mu_energy_balance`), and the LMP at the asset's own
  bus; scalars binding hours, max μ, mean μ when binding, capture price
  (`Σ p·λ·w / Σ p·w`) and capture rate against the time-weighted mean price.
- *Line, Transformer, Link* — series `mu_lower`, `mu_upper`, price spread
  (`λ_bus1 − λ_bus0`); scalars binding hours, max μ, mean μ when binding,
  congestion rent.
- *Load* — LMP at its bus.

**economics** — reuses the arithmetic and the weighting convention already in
`get_asset_economics` (`snapshot_weightings.objective × investment_period_weightings.years`
for cost terms, the `generators` weight column for energy denominators).
Scalars only: revenue, VOM, FOM, annualised fixed cost, net profit, LCOE/LCOS,
discharge/charge spread, congestion rent, cost of supply, unserved-energy cost.

**emissions** — Generator: series CO₂ rate
(`p / efficiency × carrier.co2_emissions`); scalars total tonnes, intensity
t/MWh, CO₂ cost at the global-constraint shadow price, share of the network total.
Link is ○ (only when `bus0`'s carrier carries `co2_emissions`); Store is ○ (only
for CO₂ stores, where `e` is stored tonnes).

### D4. Scope is outputs plus interpretive inputs

Strictly-output-only would drop curtailment (needs `p_max_pu`), unserved load
(needs `p_set`) and every economic metric (needs `marginal_cost` /
`capital_cost`). Those are exactly the metrics that make a per-asset view worth
building.

So each metric declares an `origin` of `output`, `input` or `derived`, and the UI
styles the three distinctly — inputs muted, derived carrying their `formula` in a
tooltip. The user is never left guessing whether a number came from the solver.

This is a display distinction only. It does not duplicate `PropertiesPanel`: the
summary category shows a curated read-only subset, and editing stays where it is.

### D5. Three applicability states, with remedies

Every metric resolves to one of:

| status | meaning | treatment |
|---|---|---|
| `ok` | computable now | ticked or tickable |
| `blocked` | applies to this class, but a precondition is unmet | greyed, carries `reason` and an actionable `remedy` |
| `na` | cannot ever apply to this class | greyed, carries `reason`, inert |

The distinction matters because the five real causes are not equivalent:

| cause | status | reason | remedy |
|---|---|---|---|
| wrong component class | `na` | "not a branch component" | — |
| `committable=false` | `blocked` | "unit commitment not enabled on Gas 1" | open Properties → committable |
| AC PF not run | `blocked` | "AC power flow has not been run" | run AC PF stage |
| duals not captured | `blocked` | "LP duals not captured in this solve" | re-run simulation |
| network unsolved or stale | `blocked` | from `dispatch_status` | run / re-run simulation |

Categories resolve the same way: a category is `na` when every metric in it is
`na`, `blocked` when none is `ok`, else `ok`. That is what greys out "Load flow"
for Gas 1 — your original example.

The remedy is an `{action, label}` pair the frontend maps to an existing UI
affordance; the backend never navigates. `action` is drawn from a closed set —
`run_simulation`, `run_ac_pf`, `open_properties` — deliberately not named `tool`,
which in this codebase means a chatbot tool.

### D6. The checklist has two zones

Metrics come in two shapes and must not be mixed in one flat list, because
ticking a scalar and ticking a series do visibly different things.

- **Summary values** (`kind="scalar"`) render as KPI cards above the data area.
- **Time series** (`kind="series"`) render as table columns, and as chart series
  when the chart is shown.

Both are selectable. Export writes scalars to a `Summary` sheet and series to a
per-category data sheet (D10).

### D7. One chart per unit, sharing the X axis

Selected series are grouped by `unit`. One unit produces one chart — the common
case, since `p`, `available` and `curtailment` are all MW. Three units produce
three charts stacked vertically, sharing the time axis and the zoom brush.

No dual axes: they invite false visual correlation, and the single-unit case
(which is most of them) is unaffected either way.

### D8. Three view modes

A segmented control switching chronological / duration curve / monthly, reusing
`durationCurvePoints` and `aggregateSeasonalRows` from
[`shared.tsx`](../../../pypsa-gui/frontend/src/pages/results/shared.tsx). The
table follows the chart mode, so the export shape follows too:

| mode | table columns |
|---|---|
| chronological | `snapshot`, `period`, one column per series |
| duration | `rank`, `pct_of_hours`, one column per series (each sorted independently) |
| monthly | `month`, and per series `mean`, `max`, `energy` |

Duration mode sorts each series independently, so a row is *not* a snapshot. The
column header says so and the exported `About` sheet repeats it.

### D9. Layout — picker left, results right

```
┌──────────────┬─────────────────────────────────────────────────┐
│ 🔎 gas       │ Gas 1  ·  Generator · carrier gas · bus N1      │
│──────────────│─────────────────────────────────────────────────│
│ ▾ Generators │ [Summary][Capacity][Dispatch] ~~Storage~~       │
│   ● Gas 1    │ ~~Load flow~~ [Prices][Economics][Emissions]     │
│   ○ Gas 2    │─────────────────────────────────────────────────│
│   ○ Wind 1   │ SUMMARY VALUES  ☑ Energy ☑ FLH ☑ CF □ Starts ⊘  │
│ ▸ Lines      │ TIME SERIES     ☑ p  ☑ curtailment  □ μ upper   │
│ ▸ Links      │─────────────────────────────────────────────────│
│ ▸ Storage    │ ( Chronological )( Duration )( Monthly )        │
│ ▸ Buses      │  ┌ chart ─────────────────┐  [SVG][PNG]         │
│ ▸ Loads      │  └────────────────────────┘                     │
│ ▸ Stores     │  snapshot │ p (MW) │ curtailment   [XLSX ▾][CSV]│
└──────────────┴─────────────────────────────────────────────────┘
```

The picker groups by component class, filters on substring of the name, and is
virtualised with `@tanstack/react-virtual` (already a dependency) so a 5 000-asset
network stays responsive.

### D10. Two exports, both provenance-stamped

A split button:

- **Export configured view** — exactly what is ticked in the open category,
  honouring the horizon filter, the selected period, the source toggle and the
  view mode.
- **Full asset report** — one sheet per applicable category with every available
  metric, regardless of the checklist.

Both write an `About` sheet first: project, solve timestamp, objective value,
`source` (`lopf`/`ac_pf`), horizon filter bounds, selected period, view mode,
PyPSA and app versions, generation time, and — for the full report — the list of
categories omitted with their reasons.

Formats: `.xlsx` (backend, pandas + openpyxl, the same writer path the existing
`export_to_excel` chat tool uses), `.csv` (frontend `downloadCSV`, already RFC
4180 and formula-injection-safe), `.svg` (frontend `downloadSVG`) and `.png`.

PNG rasterises the SVG that `downloadSVG` already produces through a canvas at 2×
scale. No new dependency, and it inherits the white background rect that helper
already injects. Charts contain no external images, so the canvas cannot be
tainted.

### D11. Placement — eleventh Results tab, plus deep links

The view is a sub-tab of the Results panel so it inherits the horizon filter,
period strip, status header and compare rail. The tab strip gains
`overflow-x-auto` and edge fades to absorb the eleventh entry.

Four entry points all funnel through one action — set `uiStore.selectedComponent`
and `requestResultsTab('asset')`:

- `PropertiesPanel` — a "View results" button on the selected asset
- `BottomPanel` asset table — a row action
- `MapCanvas` / `TopologyCanvas` node — a context-menu item
- the chatbot's `ui_open_asset_detail` tool

`RESULTS_TAB_ENUM` in `chat_tools_schema.py` and `RESULTS_TO_COMPARE_TAB` in
`Results.tsx` both gain the new id `asset`. `CompareView` is scenario-vs-scenario
and has no per-asset equivalent, so `RESULTS_TO_COMPARE_TAB.asset` is the constant
`'overview'` — opening the rail from this tab lands on the scenario overview
rather than guessing a metric. Mapping it to the open category would be worse: the
category ids and `CompareTab` ids only partially overlap, so half the categories
would need a fallback anyway.

### D12. Solver-internal rows stay hidden, their information does not

The picker calls the same transient-filtered path every other asset list uses, so
`__voll_*` and `<name>@<year>` never appear.

Their numbers are surfaced under the asset they belong to:

- **Vintage clones** → `capacity` shows `p_nom_opt` with a per-vintage breakdown
  (`2026: 120 MW`, `2030: 80 MW`), read the same way `get_vintage_results` reads
  it. Multi-period only.
- **VOLL slack** → `dispatch` for a Load shows `unserved` (series) and unserved
  energy / unserved hours (scalars), sourced from `__voll_<bus>` for that load's
  bus. The Bus category shows the same at nodal level. Tooltips name the
  mechanism so the number is traceable.

### D13. Unsolved and stale behave like every other results surface

`dispatch_status(n)` gates the result categories exactly as `_dispatch_ready` does
elsewhere: `none` and `stale` both block them, with the reason and a
run/re-run remedy.

`summary` is exempt — it reads only static columns, so it stays live and the tab
remains useful for inspecting an asset before a solve. This is the one place the
new endpoint deliberately diverges from `_dispatch_ready`, and it does so by
serving a *different* category, never by serving stale results.

Stale results are never shown. An exported workbook outlives its banner, and this
tab is not going to be the one place in the app that emits numbers describing a
topology the user no longer has.

### D14. Endpoint shape

```
GET /api/results/asset/{component_class}/{name}
      ?category=dispatch
      &metrics=p,curtailment,capacity_factor
      &source=lopf
      &from=<iso>&to=<iso>&period=<int>
      &mode=chronological|duration|monthly
```

```jsonc
{
  "asset": { "class": "Generator", "name": "Gas 1", "carrier": "gas",
             "bus": "N1", "params": { "p_nom": 200.0, "efficiency": 0.42 } },
  "solve": { "status": "fresh", "source": "lopf",
             "objective": 1.23e9, "solved_at": "2026-07-31T09:12:04" },
  "categories": [
    { "id": "dispatch", "label": "Dispatch", "status": "ok" },
    { "id": "loadflow", "label": "Load flow", "status": "na",
      "reason": "Generator is not a branch component" }
  ],
  "metrics": [
    { "id": "p", "label": "Active power", "unit": "MW", "kind": "series",
      "origin": "output", "status": "ok" },
    { "id": "status", "label": "Committed", "unit": "", "kind": "series",
      "origin": "output", "status": "blocked",
      "reason": "unit commitment is not enabled on Gas 1",
      "remedy": { "action": "open_properties", "label": "Enable committable" } }
  ],
  "index":   ["2026-01-01T00:00:00", "..."],
  "periods": [2026, 2026, "..."],
  "series":  { "p": [120.0, 135.2], "curtailment": [0.0, 0.0] },
  "scalars": { "energy_mwh": 512000.0, "full_load_hours": 4210.0 },
  "by_period": [ { "period": 2026, "energy_mwh": 256000.0 } ]
}
```

`categories` always carries **all eight**, each with its resolved status, because
the tab strip needs to know what to grey out before the user clicks. `metrics`
carries the full list for the *requested* category only — including `blocked` and
`na` entries — because that list *is* the checklist. `series` and `scalars` carry
only what was requested and resolved `ok`.

`by_period` is a **list of row objects each carrying a `period` key**, matching
the shape `get_asset_economics` already emits. One convention for per-period
roll-ups, not two.

Export is a sibling: `GET …/export.xlsx` with the same query parameters plus
`scope=view|full`, streaming a workbook.

Non-finite floats become `null` through the existing `safe_values` / `ts_payload`
helpers in `services/serialization.py`. Skipping that is how
`/results/storage` once returned a 21-byte plain-text 500 from
`JSONResponse.render`; the trap is documented in CLAUDE.md and applies here.

### D15. Module layout

A new package rather than another 700 lines in `routers/results.py`, which is
already ~3 700 lines:

```
backend/services/asset_results/
  __init__.py       public surface: resolve(), compute(), to_workbook()
  registry.py       the Metric table — the single source of truth (D1)
  applicability.py  class × category × precondition → ok | blocked | na
  compute.py        per-metric computation, one function per metric
  export.py         pandas/openpyxl workbook builder
backend/routers/asset_results.py    two endpoints, thin
```

`main.py` gains one `include_router` line. This is the "new backend service =
3 files" pattern from CLAUDE.md, with the service split internally because the
registry and the compute functions have genuinely different reasons to change.

Frontend:

```
frontend/src/pages/results/asset/
  AssetDetail.tsx     shell: picker + header + category strip
  AssetPicker.tsx     virtualised, class-grouped, searchable
  MetricChecklist.tsx two zones, three states, remedy actions
  AssetCharts.tsx     unit-grouped chart stack, shared X
  AssetTable.tsx      virtualised, mode-aware
  exportPng.ts        SVG → canvas → PNG
  types.ts            response types, generated by hand from D14
```

Each file stays under ~350 lines. `Dispatch.tsx` at 3 612 lines is the cautionary
example in this directory.

### D16. Chatbot — three tools

| tool | tier | purpose |
|---|---|---|
| `get_asset_results` | read | query the same endpoint the UI uses |
| `ui_open_asset_detail` | read | emit a `ui_event` that opens the tab pre-configured |
| `export_asset_results` | write | produce the xlsx/csv/png into the project's `uploads/` as `kind="agent_export"`, surfacing a download chip |

`write` is not in `DESTRUCTIVE_TIERS`, so none of the three prompts for
confirmation — verified in `chat_service.py`.

**`get_asset_results` defaults to statistics, not raw arrays.** An hourly year ×
ten metrics is ~87 000 numbers; handing that to the model would consume a large
share of the context window on a single question. The default response carries
scalars plus per-series statistics — min, max, mean, sum, p50, p95, peak
timestamp, zero-hours, and a ~48-point downsample for shape — which answers almost
every question anyone actually asks. `resolution="raw"` returns real arrays,
capped, with `truncated` and `n_total` set and a note pointing at the export tool.

The tool description must state the cap and the truncation semantics explicitly.
Two CLAUDE.md lessons apply directly: never return placeholder strings where the
model expects a real identifier (return the resolved metric ids, not `"default"`),
and every schema field absent from `required` needs a matching Python default.

### D17. Build order — vertical slice, Generator first

| phase | contents | ~share |
|---|---|---|
| **1** | Generator end-to-end: registry, applicability, compute, both endpoints, full UI, all three view modes, all four export formats, all three chat tools, all four deep links | 60 % |
| **2** | Line, Transformer, Link, Bus — adds the `loadflow` depth, congestion rent and LMP metrics | 25 % |
| **3** | StorageUnit, Store, Load — adds `storage`, spill, cycles and unserved energy | 15 % |

Phase 1 hits every architectural risk and ships something genuinely usable
("evaluate any generator"). Phases 2 and 3 are then mostly registry entries plus
their compute functions, which is the test of whether D1's shape was right.

### D18. Selection memory

The tick-set is persisted per `(component class, category)` in `localStorage`
under `assetDetail:metrics:<Class>:<category>`. Switching Gas 1 → Wind 1 keeps the
selection; switching to a Line loads the Line's own set, or a curated default.

On load, any remembered metric whose resolved status is not `ok` is dropped from
the selection — silently, because its reason is already visible in the checklist.
If the current category is `na` for the newly-selected asset, the strip falls back
to the first `ok` category, `summary` at worst.

---

## Testing

**Backend (pytest, `backend/tests/`)**

- `test_asset_results_registry.py` — every metric's `classes` are real component
  classes; every `category` is one of the eight; ids unique; every `compute` is
  callable; every series metric declares a unit.
- `test_asset_results_applicability.py` — the full 8 × 8 matrix from D2 asserted
  cell by cell; each of the five `blocked` causes reached by constructing the
  matching network state; `na` never carries a remedy.
- `test_asset_results_endpoint.py` — solved fixture: metric values match a direct
  read of the `_t` frame; horizon filter and period narrow the index; `source`
  toggles between the LOPF and AC PF snapshots; NaN and Inf serialise to `null`;
  unsolved → summary live and every other category blocked; stale → same.
- `test_asset_results_hidden_rows.py` — `__voll_*` and `<name>@<year>` absent from
  the asset list; vintage `p_nom_opt` present under the parent's capacity; VOLL
  dispatch present as the load's unserved energy.
- `test_asset_results_export.py` — workbook opens, `About` sheet carries every
  provenance field, `scope=full` omits `na` categories and lists them, duration
  mode writes rank/percentile columns.
- `test_chat_asset_results.py` — the three tools dispatch; schema `required`
  matches every Python signature; the default response is statistics and stays
  under a fixed size budget; `resolution="raw"` sets `truncated` past the cap.

Reconciliation is worth asserting explicitly: a generator's `economics` scalars
must equal its row in `/results/asset_economics`, and its `dispatch` energy must
equal the weighted sum the Dispatch tab shows. Two sources of truth for one number
is how this codebase has been bitten before.

**Frontend (vitest, colocated)**

- `MetricChecklist.test.tsx` — three states render distinctly; `blocked` shows its
  remedy and `na` does not; ticking a blocked metric is a no-op.
- `AssetCharts.test.tsx` — one unit yields one chart, three units yield three;
  the X domain is shared.
- `exportPng.test.ts` — produces a PNG blob whose magic bytes are `89 50 4e 47`;
  returns false rather than throwing when the chart has not mounted.
- `AssetDetail.test.tsx` — deep link preselects the asset; switching class swaps
  the remembered tick-set and drops metrics that became unavailable.

---

## What this does not do

- **No multi-asset comparison.** One asset at a time. The data layer is keyed
  `series[metricId]` for one asset, and a future compare mode would key it by
  asset — an additive change, not a rewrite, but not in scope here.
- **No saved named views.** Per-class memory only (D18).
- **No editing.** Read-only throughout; `PropertiesPanel` keeps the write path.
- **No `ShuntImpedance` or `GlobalConstraint`.**
- **No stale-result viewing** (D13).
