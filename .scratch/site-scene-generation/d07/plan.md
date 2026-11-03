<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# D07: native result quantity registry (draft)

Status: **proposed**. Not accepted. Needs GK A review and then the owner's acceptance through the coordinator (owner decision SC03-D07-1: "D07 first, then build"). No projection code is written until it is accepted.

- Base: master `8616af5778` (PR #1053 merge). Spec revision: `.scratch/site-scene-generation/` on the same commit.
- Gates: SC-03 (V22–V28, V57, V87–V88) and the SC-07 results unit.
- Oracle: [evidence/oracle.py](evidence/oracle.py) and its output in [evidence/oracle-output.txt](evidence/oracle-output.txt). Environment: PyPSA 1.1.2, pandas 2.3.3, HiGHS through `n.optimize`. Exit 0.

## 1. What exists on master (reused, not rebuilt)

| Need | Native seam on master |
|---|---|
| Per-asset metrics, units, preconditions | `services/asset_results/registry.py` (`Metric`, `REQ_DISPATCH`, `REQ_PORT2..4`) and `compute.py` |
| Dispatch frames (stored LP first) | `routers/results.py::_result_df(n, accessor, attr, source="lopf")`. `physical_quantities.py` receives this through injection. |
| Results describe the current inputs | `workflow_tools.solve_freshness(ctx)["results_current"]` → `harness.calculations.solve_matches_inputs`. It requires the solver status `completed`/`optimal`, and both solve input fingerprints (`n.meta["assistant_solve_input_fingerprint"]` and the exact one, stamped by `solver_service` on every successful solve) must equal the live inputs. |
| Run identity | `dispatch_status.dispatch_digest(n, result_df)` |
| Bus units, product buses, tonne factor | `process_products.bus_unit`, `carrier_catalog.bus_tonne_factor` (`1` on a `t` bus, `1/mwh_per_t` on an MWh bus, else `None`) |
| Energy weights | `compare.support._build_snapshot_weights(n, "generators")`: `snapshot_weightings.generators` × `investment_period_weightings.years`. This is the basis that `process_view.ENERGY_BASIS` uses. |
| Unavailable shape | `services/site_scene/contract.quantity(value, unit, reason)` (contracts.md §6, ADR-0001) |
| Feature → target, groups, shares | `services/site_scene/associations.py`: D06 uids, `groups` (members, acyclic), explicit `weight`s that total 1 |
| Shared timeline | frontend `resultsSnapshotIdx` (read-only in site3d, pinned by `site3d/timeline.pin.test.ts`); `services/snapshot_index.py` |

Geometry is not a model input. The input fingerprint hashes the snapshots, the weightings, the LP-relevant solver config and the `Input` columns. It does not hash `n.meta`, and it does not hash scenes or sites.json. A scene edit, or a D06 uid mint, therefore keeps a valid run. An electrical input edit makes the run stale through the same guard that the Results tab and Check answers use.

## 2. Contract

### 2.1 Route (a D02 amendment)

`GET /api/projects/{name}/sites/{site_id}/scene/results?snapshot=<iso>[&period=<p>]&run=<digest?>&window_from=&window_to=`

- It is read-only. It never solves, simulates, writes, mints or marks the network dirty. It reads under the network lock.
- It answers one snapshot, plus optional window totals. Playback asks once per displayed snapshot, or per chunk (see §5). There is no per-tick reconstruction.
- Chat disposition: listed in `not_via_chat` until the SC-07 results unit adds a read tool.
- Error kinds reuse the existing ones: `not_found`/`forbidden` (project ACL), and `invalid_input` for a malformed snapshot. Every other condition is a 200 with unavailable quantities (§2.6), so the scene still renders.

### 2.2 Response

```json
{"project": "demo", "site_id": "s1", "scene_revision": 7,
 "run": {"digest": "…32 hex…", "current": true, "reason": null},
 "snapshot": {"index": 3, "timestamp": "2030-01-01T03:00:00", "period": null,
              "weight_generators_h": 1.0, "period_years": 1.0},
 "features": {"bldg-ammonia": {
     "targets": [{"class": "Link", "uid": "…", "name": "haber", "allocation": null,
                  "ports": [{"port": 0, "bus": "h2", "carrier": "H2",
                             "rate": {"value": 5.0, "available": true, "reason": null, "unit": "MW"},
                             "sign": "withdrawal_positive"}, …]}],
     "group": null}},
 "groups": {"g-plant": {"boundary": [ … §2.5 … ], "internal_buses": ["h2"], "unavailable_total": { … }}},
 "window": null}
```

Every numerical leaf is a `quantity()` dict: `{value, available, reason, unit}`. Only `available: true` carries a finite number. `-0.0` becomes `0.0`.

### 2.3 Native selectors, ports and signs

All values are read through `_result_df(n, acc, attr, "lopf")` at the exact native snapshot label (a MultiIndex `(period, timestep)` key on a multi-period network). Values are never interpolated or resampled.

| Bound class | Selector (native) | Port(s) | Native sign kept | Rate unit |
|---|---|---|---|---|
| Generator | `generators_t.p` | bus | injection positive | bus rate unit |
| Load | `loads_t.p` | bus | withdrawal positive | bus rate unit |
| StorageUnit | `storage_units_t.p` (net), `state_of_charge` | bus | injection positive. SoC is a state in MWh, not a rate. | MW; MWh state |
| Store | `stores_t.p`, `stores_t.e` | bus | injection positive. `e` is a state in the bus unit. | bus rate unit; bus energy unit |
| Line / Transformer | `lines_t.p0/p1`, `transformers_t.p0/p1` | bus0, bus1 | withdrawal positive at each end | MW |
| Link (incl. `eh_process`) | `links_t.p{i}` for `i` with `bus{i}` set (`REQ_PORT2..4` rule) | bus0…bus4 | withdrawal positive at each port: `p0 > 0` draws feed, `p_i < 0` delivers product | the port bus's rate unit |

- Each port also carries `role`: `input` when the native efficiency sign makes it a withdrawal (`i=0`, or `efficiency{i} < 0`), and `output` otherwise. The role comes from the parameters, never from the name.
- A port whose `bus{i}` is unset is **absent** from the list, not 0. PyPSA reports `p3 = 0.0` for the electrolyser, which has no bus3. The oracle shows this.
- Signs are never flipped silently. A display "delivery" is a labelled derived quantity (`−p1`, as in `link_output`) with `formula`.

### 2.4 Units and conversion

- **Rate unit of a bus:** `t/h` when `bus_unit(n, bus) == "t"`; `MW` when it is `MWh`, or when the bus is unset or PyPSA's default on a non-product bus. Any other declared unit is `unit_unknown`, and the bus's values are unavailable.
- **Energy unit:** the bus unit (`MWh` or `t`). MWh on a product bus stays the native MWh, which is LHV by the templates' convention. A basis is never relabelled.
- **Tonnes from MWh:** only through `bus_tonne_factor(n, bus)` (`1/mwh_per_t`). When that returns `None` the result is `{value: null, available: false, reason: "conversion_factor_missing", unit: "t/h"}`.
- MW↔t/h is never converted, and nothing is ever converted between carriers. No template or illustrative factor is ever used.

### 2.5 Time and weight semantics

- **Instantaneous** (`rate`): the native value at the snapshot, in MW or t/h. It is never multiplied by a weight, and never repeated from a horizon or summary figure. The process summary (`/results/process_view`, running hours) is never an hourly source.
- **State** (`level`): Store `e` and StorageUnit `state_of_charge`, unweighted.
- **Snapshot energy** (optional, labelled `energy_in_snapshot`): `rate × snapshot_weightings.generators × period years` for that one snapshot. On a representative snapshot it carries `weight_generators_h` so that 10 h × 10 MW = 100 MWh is visible as a weighted figure, not as an hour.
- **Window total** (`window`): `Σ rate × w_generators × years` over native snapshots in `[window_from, window_to]`. Its unit is MWh or t, it is labelled `weighted_total`, and `basis: "generators"` is named. This is the same rule as `process_view`.
- **Cadence:** native timestamps only. Nothing assumes 8760 or a one-hour step. A 15-minute snapshot with weight 0.25 h gives rate 10 MW and 2.5 MWh. A snapshot not in the index is `snapshot_not_in_run` (no nearest-neighbour). Visual interpolation between frames happens only in the renderer and is never returned as data.
- **Timezone:** labels are returned exactly as stored (naive, network-local). The UI displays them as the shared timeline does.
- **Multi-period:** the `period` is echoed and `period_years` applied only to weighted figures. Vintage rows (`<name>@<year>`) are not summed into a base binding in SC-03: `vintage_rows_unsupported` (see the open choice B).

### 2.6 Freshness and binding checks (order; the first failure names the reason)

| # | Check | Reason when it fails | Scope |
|---|---|---|---|
| 1 | The project is the open context | `project_not_open` | all quantities |
| 2 | The solver status is `completed`/`optimal`. A cancelled, failed or infeasible solve, or a running one, fails this. | `run_not_solved` / `run_failed` / `run_cancelled` / `run_running` | all |
| 3 | `solve_matches_inputs(ctx)` | `results_not_current` (inputs edited since the solve, or no recorded fingerprint) | all |
| 4 | `run` given and ≠ the current `dispatch_digest` | `run_changed` | all |
| 5 | The snapshot exists in the native index / window inside the horizon | `snapshot_not_in_run` / `window_out_of_range` | all |
| 6 | The feature has an association with a uid resolving to a live component (D06) | `unbound_target` | that target |
| 7 | The component has a column in the frame (`dispatch_status` axis) | `series_missing` | that target |
| 8 | The value is finite | `non_finite_value` | that leaf |
| 9 | Unit and conversion (§2.4) | `unit_unknown` / `conversion_factor_missing` | that leaf |

The full condition is "results describe current inputs". AC power flow has the same status as everywhere else: after a PF, check 3 fails (`solve_matches_inputs` requires `ac_pf_results is None`). See the open choice C.

### 2.7 Group boundary rule (the supported one)

A group's member targets are the union of the targets bound to its member features (recursively, over the acyclic group tree).

1. For each bus that any member port touches, it is **internal** if every native component with a port on that bus is a member target. Internal buses are listed in `internal_buses` and excluded. This removes double-counted internal transfers.
2. Otherwise it is a **boundary** bus. `net_import = Σ member port withdrawals on that bus` (native signs: Link `p_i`, Load `p`, minus Generator/Store/StorageUnit `p`). Transfers between members on a boundary bus cancel algebraically.
3. Boundary flows are reported per bus, with carrier and rate unit. A group total across buses is given only per `(carrier, unit)`. Different units or carriers are never summed: such a request gives `{value:null, available:false, reason:"mixed_units"}`.
4. **Unsupported, so unavailable:**
   - a member target with an explicit share below 1 whose other shares lie outside the group: `group_partial_allocation`;
   - a Line or Transformer as a member: `group_branch_member`;
   - any member check in §2.6 failing: `member_unavailable`, with the member named.
   
   No plausible number is invented.

### 2.8 Allocation (V15/V16)

An explicit share gives `allocated = weight × native value`, labelled `"allocation": {"weight": 0.4, "native_total": {…}}`. The shares of one target conserve the native value. An allocated figure is never presented as measured building demand. A target that is bound without a share to several features shows the native value once, on a `shared` marker, and has no per-building split.

### 2.9 Reason codes (closed list)

`project_not_open, run_not_solved, run_failed, run_cancelled, run_running, results_not_current, run_changed, snapshot_not_in_run, window_out_of_range, unbound_target, series_missing, non_finite_value, unit_unknown, conversion_factor_missing, vintage_rows_unsupported, mixed_units, group_partial_allocation, group_branch_member, member_unavailable`. Each code is pinned by a test, and each one reaches the UI as "Unavailable" with words, never as `0.00`.

## 3. Known-hour oracle (domain fixture F03/F04 hybrid)

Fixture `evidence/oracle.py`:
- buses: `elec` (AC), `heat`, `h2`, `nh3` (unit MWh, `mwh_per_t = 5.2`) and `n2` (unit t);
- Generators: `grid` (10 EUR/MWh), `boiler` (heat, 50) and `n2_supply` (t/h);
- Loads: `office` [2,3,4,3], `heat_demand` [5,5,5,5] and `nh3_offtake` [8,8,0,4];
- `electrolyser` (`eh_process`): elec→h2 η 0.5, bus2 heat η2 0.1;
- `haber` (`eh_process`): h2→nh3 η 0.8, bus2 elec η2 −0.05, bus3 n2 η3 −0.2 t/MWh_H2.

The dispatch is unique (fixed loads, no storage). Each value is derived by hand first; the solve reproduced every one exactly (`oracle-output.txt`).

**Known hour: snapshot 3** (`2030-01-01T03:00`, the hourly variant):

| Quantity | Hand derivation | Native (solved) | Unit |
|---|---|---|---|
| `haber` p0 (H2 in) | 4 / 0.8 | 5.0 | MW |
| `haber` p1 (NH3 out) | −4 | −4.0 | MW |
| `haber` p1 in tonnes | −4 × (1/5.2) | −0.769230769… | t/h |
| `haber` p2 (elec in) | 0.05 × 5 | 0.25 | MW |
| `haber` p3 (N2 in) | 0.2 × 5 | 1.0 | **t/h** (never MW) |
| `electrolyser` p0 | 5 / 0.5 | 10.0 | MW |
| `electrolyser` p1 / p2 | −5 / −0.1×10 | −5.0 / −1.0 | MW |
| `electrolyser` p3 | no bus3 | (0.0 in PyPSA) → **absent** | — |
| `grid` p | 3 + 10 + 0.25 | 13.25 | MW |
| group {electrolyser, haber}: `h2` | only members touch it | internal, excluded | — |
| group boundary `elec` net_import | 10 + 0.25 | 10.25 | MW |
| group boundary `heat` net_import | −1 | −1.0 | MW |
| group boundary `n2` net_import | +1 | 1.0 | t/h |
| group boundary `nh3` net_import | −4 | −4.0 | MW (−0.769… t/h) |
| group total "all carriers" | — | null, `mixed_units` | — |
| snapshot 2: `haber` p1 | offtake 0 | 0.0 → `{value:0, available:true}` | MW |

**Weights** (the same rate, different energy): the `electrolyser` p0 window total over the 4 snapshots is 50 MWh hourly (w = 1 h), 12.5 MWh at 15 minutes (w = 0.25 h) and 500 MWh representative (w = 10 h). The rate at snapshot 3 is 10 MW in all three.

**Negative variants** (expected shapes, to pin as tests):
- drop `mwh_per_t` and read `haber` p1 in t/h: `conversion_factor_missing`;
- edit `office` p_set after the solve: every leaf is `results_not_current`;
- a cancelled solve: `run_cancelled`;
- `snapshot=2030-01-01T03:30`: `snapshot_not_in_run`;
- `office` shared 0.4/0.6 with one building in the group: `group_partial_allocation`;
- another project's name: `project_not_open`.

## 4. Alternatives considered

1. **Backend adapter route (selected).** One native registry, testable at the HTTP seam against the oracle. The units, conversions and the group rule live beside `process_view` and `bus_tonne_factor`. The frontend only renders, so the PR stays out of PR #1040's/#1050's `frontend/src` files until UI wiring.
2. **Project in the frontend from the existing chunked `/results/*` series** (`site3d/useSiteResults.ts`). This reuses the cache, but it would reimplement bus units, tonne factors, the group boundary and the reason codes in TypeScript, a second source of truth (contracts.md: "Actual unit/quantity keys are pinned in SC-03's native adapter contract"). It is also unit-blind today: the registry labels every Link port `MW`.
3. **Extend `asset_results/registry.py` per feature.** Its `Link p{i}` unit is a fixed `"MW"`, so it would need the bus-unit rule anyway. It is a per-asset, whole-series API, not a per-snapshot feature API. Selected: reuse its compute functions and preconditions, without changing its unit fields. Changing them is separate scope (it touches the Results tab).

## 5. Planned tests (Step 2, written first)

`pypsa-gui/backend/tests/test_scene_results.py` builds the oracle fixture through real native services, with a real HiGHS solve (no mocked algebra). It covers:
- V22/V23/V88: the table in §3, exactly;
- V24/V87: the weights variants, and no rate × weight in `rate`;
- V25: internal `h2`, the boundary rows and `mixed_units`;
- V26: each check in §2.6;
- V27: the multi-period and representative fixtures;
- V57: the exact JSON for null and zero;
- V28: a scene geometry edit keeps `current`, and the timeline read does not change `scene_revision` or `dispatch_digest`.

The frontend overlay consumes the response after PR #1040 lands, behind the existing Eye toggle and the shared timeline.

## 6. Open choices for the owner (sent as options)

- **A. Route:** a backend adapter (recommended) or a frontend projection.
- **B. Vintage rows:** unavailable (`vintage_rows_unsupported`) in SC-03 (recommended), or a sum of the `@year` rows active in the snapshot's period, using the capacity rule.
- **C. After an AC PF:** follow the shared guard and show unavailable (recommended, the same as every result surface), or read the stored LP with a D07-only freshness rule.

## 7. Skipped and risks

- Process summary figures (running hours, starts) are not shown per hour, by design.
- The route amends D02's table. GK B owns D02 and may want to see it.
- No chat tool: the SC-07 results unit adds one.
- On a large network, a per-snapshot request costs one frame read per bound target. The chunked prefetch (one request per chunk of snapshots) is left to Step 2, under the D12 budget.
