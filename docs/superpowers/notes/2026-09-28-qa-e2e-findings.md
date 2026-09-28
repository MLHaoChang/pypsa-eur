# Independent QA end-to-end review — findings register

Date: 2026-09-28. Scope: `pypsa-gui/backend` (+ one frontend touch point), reviewed
against `master` at `9f83f37`. Every finding below was re-verified by hand in the
code on that commit before being written down; none are reviewer claims taken on
trust. Sites are quoted with the symbol name rather than a bare line number so the
register survives the next refactor.

The point of this file is durability: the findings were produced in a session whose
scratchpad was later wiped by a container restart. Anything not committed does not
exist.

## Status legend

- **OPEN** — reproduced, not yet fixed.
- **FIXED** — fix landed; the commit that closed it is named.

---

## Critical

### QA-N1 — cascade bus delete orphans user timeseries and vintage bounds

- Site: `services/network_buses.py::apply_delete_bus_cascade`
- Contrast: `services/network_crud.py::_delete_component`

`_delete_component` deletes a component and then performs two cleanups:
`vintage_service.delete_bounds_for_asset(n, component_class, name)` and
`_user_ts_delete_asset(attr, name)`. Its own comment states why the second one
matters: "without this they accumulate forever in project saves and a future
component reusing the same name inherits the deleted asset's profile."

`apply_delete_bus_cascade` removes the bus and every attached Line, Link,
Transformer, Generator, Load, StorageUnit and Store with a bare `n.remove(cls, comp)`
and performs **neither** cleanup. So the cascade path — the one a user reaches by
deleting a node on the map, i.e. the common path — leaves both the `_user_ts`
entries and the per-period vintage bounds behind for every component it removed.

Consequences, in order of severity:

1. A later component created with the same name silently inherits the deleted
   asset's uploaded profile. Wrong dispatch, no warning, no trace in the change log.
2. Stale `vintage_bounds` entries reach the solver, which tries to expand bounds for
   an asset that no longer exists.
3. Project saves grow monotonically with orphaned profile data.

Fix shape: factor the two cleanups into one helper and call it from both paths, for
every component the cascade removes (not just the bus).

**Status: OPEN**

### QA-N2 — spatial clustering drops `n.meta`

- Site: `routers/clustering.py`, after `get_clustering_from_busmap`:
  `new_n = clustering.n` then `PyPSAService.set_network(new_n)`.

`clustering.n` is a freshly constructed network. `n.meta` is where the GUI keeps
`vintage_bounds` and `vintage_results`. Nothing carries `meta` across, so clustering
silently discards the user's entire per-period bounds configuration and any stored
vintage results. The comment beside the assignment reassures about coordinates
("aggregatebuses averages them") which makes the omission easy to miss on review.

Fix shape: carry `meta` across explicitly, and decide per key whether it survives
aggregation — asset-keyed `vintage_bounds` entries whose asset no longer exists after
clustering must be dropped or remapped rather than carried blindly, otherwise QA-N1's
stale-bounds failure mode is reintroduced through the back door. Whatever is dropped
must be reported in the audit log entry the handler already writes.

**Status: OPEN**

---

## Serious

### QA-A1 — `mixture_hourly`'s "silent unit" test is not exact-zero (regression, self-inflicted)

- Site: `services/adequacy/copt.py`, the `silent` set in `mixture_hourly`.

```python
silent = {i for i in range(len(mixed))
          if np.isfinite(avail[i]).all() and float(avail[i].max()) <= 0.0}
```

The correctness proof written directly above this code requires the availability
vector to be identically zero (`a ≡ 0`): a unit that contributes nothing in every
hour can be frozen in the up state without changing the convolution, because both of
its states contribute the same (zero) capacity. `max() <= 0.0` is a strictly weaker
test — it also matches an **all-negative** availability vector, which is *not*
capacity-neutral. Such a unit gets frozen at `s = 1`, so its negative contribution is
applied unconditionally instead of being applied with probability `1 - FOR`. The
resulting COPT is wrong, and wrong in the optimistic direction is not guaranteed.

This was introduced by my own change and is live on `master`. I reproduced it against
a brute-force enumeration of the full state space.

Adjacent, same file, same fix: the occurrence-bearing branch passes
`profile=_occurrence_profile(p_max_pu_t, g, snapshots)` **unclipped**, while the
must-take branch a few dozen lines below does `.clip(lower=0.0)`. That asymmetry is
how a negative availability reaches `mixture_hourly` in the first place. Clipping the
occurrence profile at zero closes the gap the F6 review left open.

Fix shape: make the `silent` test exact (`np.all(avail[i] == 0.0)` under the existing
finiteness guard) **and** clip the occurrence profile, so both the proof's premise and
the data path that violates it are repaired. Fixing only one of the two leaves either
a correctness hole or a silent behaviour change.

**Status: OPEN**

### QA-N3 — investment-period weightings are assigned positionally against a sorted index

- Site: `routers/network_time_axis.py::set_investment_periods`

```python
new_periods = sorted({int(p) for p in body.periods})
...
n.investment_period_weightings["objective"] = body.objective_weightings
n.investment_period_weightings["years"] = body.years_weightings
```

`periods` is sorted and de-duplicated; the two weighting lists are then assigned
**positionally** in the caller's submitted order. Whenever the caller submits periods
out of ascending order — `periods=[2040, 2030]`, `objective_weightings=[0.5, 1.0]` —
the weights land on the wrong periods, silently. Duplicate period values shift every
subsequent weight by one for the same reason.

There is also no length check: a list shorter or longer than the period count either
raises a raw pandas error surfaced as a 500, or broadcasts, depending on length.

Fix shape: pair each weight with its submitted period *before* sorting and assign by
label (`df.at[period, col]`), the way `update_investment_period_weightings` already
does; reject a length mismatch with a 400 naming both counts.

**Status: OPEN**

### QA-P1 — chat lineage resolves to the flat legacy directory

- Site: `services/chat_service.py::_project_chat_paths` (flat `PROJECTS_DIR / name`)
- Contrast: `services/chat_service.py::get_persist_path` (uses `ctx.storage_dir`)

`get_persist_path` resolves a project's storage through the request context's
`storage_dir`, which is what tenancy mode requires. `_project_chat_paths` instead
builds a flat `PROJECTS_DIR / project_name` path. Every caller that copies chat
history across a project boundary goes through `_project_chat_paths`: snapshot and
Save-As both do. In tenancy mode the lineage therefore resolves to a directory that
is not the tenant's, so the copy finds nothing and the new project starts with the
chat history silently gone.

Related, same area: `_create_scenario_db` carries no chat history at all, so a
scenario created from a project starts empty even outside tenancy mode.

Fix shape: resolve through the same context-aware helper `get_persist_path` uses, and
decide explicitly whether `_create_scenario_db` should inherit chat (my reading: yes,
matching snapshot).

**Status: OPEN**

### QA-P2 — `rename_project` skips context rebinding on its early-return path

- Site: `services/project_registry.py::rename_project`, versus the
  `_rebind_resident_contexts(project)` call further down the module.

`rename_project` has an early return that bypasses `_rebind_resident_contexts`. In
web mode the resident context keeps the pre-rename `loaded_project`, so the next save
is attributed to a project name that no longer exists and the save fails with a 409.
The module's own docstring already warns that the caches must be rebound after a
rename and enumerates them.

Fix shape: rebind on every path that changes the stored name, including the early
return; the docstring's fourth-cache warning (`solve_queue`) applies here too.

**Status: OPEN**

### QA-E1 — `asset_economics` documents a reconciliation that is false

- Site: `services/results/asset_economics.py`, the "What these numbers reconcile
  with" docstring block.

The docstring asserts, as a measured fact and with a specific figure, that
`Σ fixed_cost_eur == Σ economics_by_carrier.capex_meur × 1e6` **EXACTLY**, and tells
the reader "that is the reconciliation to quote". The identity holds only on a
network with no branch capex. `services/compare/economics.py` deliberately walks
`Line` and `Transformer` capex, which `asset_economics` does not include, so on any
network with expandable transmission the two sides differ by exactly the branch
capex — and the docstring invites a user to quote the mismatch as a consistency proof.

Fix shape: state the identity with its actual scope (generation and storage assets
only), name branch capex as the documented difference, point at the compare module as
the place that does include it, and drop the single measured figure — it was true of
one network, not of the invariant.

**Status: OPEN**

---

## Moderate

### QA-N4 — generic timeseries upload accepts columns that name no asset

- Site: `routers/network_time_axis.py::upload_timeseries` (`POST /timeseries/upload`).

The handler validates the CSV's index, the `period` argument and finiteness, then
writes **every** column straight into `_user_ts` keyed
`(component, attribute, col)`. Nothing checks that `col` names a row that exists on
`getattr(n, component)`, and nothing checks that `attribute` is a valid time-varying
attribute for that component. The response reports `columns: len(df.columns)` — a
success count — so a typo in a column header is indistinguishable from a correct
upload.

The stored entry is not inert. `_user_ts` is persisted in project saves and
re-injected by `_reapply_user_ts_to_network` on every solve, so a mistyped column
accumulates forever and, if an asset with that name is later created, that asset
silently inherits the ghost profile. That is QA-N1's failure mode reached by a
different route.

Fix shape: partition the columns into matched and unmatched before taking the lock;
store only the matched ones, and return the unmatched names in the response (plus the
change-log line) so the UI can warn. Do not fail the upload — partial uploads are
legitimate. Validate `attribute` against the component's time-varying attributes for
the same reason.

**Status: OPEN**

### QA-N5 — bus `country` is advertised by the schema and dropped by the filter

- Sites: `models/schemas.py::BusCreate` (declares `country: str = ""`) versus
  `services/network_crud.py::_drop_unknown_extras`.

`_drop_unknown_extras` keeps a key only if PyPSA's catalog reports it as an Input
attribute **or** it is already a column on the frame. On PyPSA 1.1.2 — the version CI
pins — the `Bus` catalog is
`name, v_nom, type, x, y, carrier, unit, location, v_mag_pu_set, v_mag_pu_min,
v_mag_pu_max, control, generator, sub_network, …` with no `country`, and a fresh
`n.buses` has no `country` column. So on any network the GUI builds from scratch,
`country` fails both arms and is stripped — while `BusCreate` declares it, the API
docs advertise it, and the response reports success.

It is invisible on networks imported from PyPSA-Eur, because those arrive with a
`country` column already present and the second arm then lets it through. That is why
it survives casual testing: it only bites the from-scratch path.

Fix shape: the second arm exists precisely for attributes the Create models declare
but PyPSA does not catalog, so extend it — union the catalog with the declared fields
of the component's Create model, rather than special-casing `country` alone.

**Status: OPEN**

### QA-N6 — snapshot and investment-period weightings accept negative and non-finite values

- Sites: `routers/network_time_axis.py::update_investment_period_weightings`
  (`float(vals[col])` with no range check; same for `all_years` / `all_objective`),
  and the snapshot-weightings equivalents.

`float("-5")`, `float("inf")` and `float("nan")` all pass validation. A negative
weight inverts the sign of that period's contribution to the objective; a non-finite
one poisons every downstream sum and surfaces as `NaN` costs with no indication of
where they came from.

Fix shape: reject non-finite values and negative values with a 400 naming the column
and the offending value, at every entry point that writes a weighting.

**Status: OPEN**

### QA-S1 — lock-holder email reaches the LLM provider

Originally raised 2026-08-27; still open. When a project lock is held, the holder's
email address is included in the payload sent to the LLM provider. The secrets
redactor does not catch it, and correctly so — it is a secrets redactor by design,
not a PII redactor. The fix belongs where the payload is assembled: the lock-holder
identity should be reduced to a non-identifying form ("held by another user", or an
opaque handle) before it is ever put in a prompt.

**Status: OPEN**

---

## Not reviewed

Two areas were in scope for the end-to-end pass and never got a reviewer:

- **Solver lifecycle** — queueing, cancellation, the myopic path, result publication.
- **Chat / LLM tooling** — the tool-dispatch surface in `chat_tools.py` and its schema.

Both should get the same treatment before the integration is called complete.
