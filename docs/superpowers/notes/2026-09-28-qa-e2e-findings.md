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

**Status: FIXED** — `cabe756`. `services.network_crud.purge_component_side_data`,
called by both paths. Guard: `tests/test_bus_cascade_side_data.py`, with the
plain-delete path as a control.

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
clustering must be dropped rather than carried blindly, otherwise QA-N1's
stale-bounds failure mode is reintroduced through the back door. Whatever is dropped
must be reported in the audit log entry the handler already writes.

Measured, because the obvious guess is wrong: on PyPSA 1.1.2 clustering re-buses
one-port components under their ORIGINAL names, so a generator's bounds must
SURVIVE the run. It is the branches that vanish — three lines collapse to one
renamed branch — so the prune is not a formality on either side.

**Status: FIXED** — `cabe756`'s sibling commit; `vintage_service.prune_orphaned_entries`
plus the carry in `routers/clustering.py`, guarded by
`tests/test_clustering_meta_carry.py` (the first clustering tests in the repo).
Both halves of the fix are bite-tested independently: removing the carry reddens
three, removing only the prune reddens two.

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

Fix shape: make the `silent` test exact under the existing finiteness guard **and**
clip the occurrence profile, so both the proof's premise and the data path that
violates it are repaired. Fixing only one of the two leaves either a correctness hole
or a silent behaviour change.

**Status: FIXED** — `fbae12e`. Guard: `tests/test_adequacy_copt_negative_availability.py`,
whose reference is a brute-force enumeration of the full state space with no freezing.

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

**Status: FIXED** — pairing is done before anything is mutated, so a bad list no
longer leaves the snapshots rebuilt. Guard: `tests/test_weightings_integrity.py`,
which submits periods descending (ascending input cannot distinguish the two) and
keeps the ascending case as a control.

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

**Status: FIXED**, three parts.

1. `handle_save_lineage` now takes resolved `source_dir` / `target_dir`. The caller
   (`_carry_sidecars_on_move`) already resolved the source through the registry for
   the `uploads/` half of the same hook; that resolution is now done ONCE and shared,
   so the two sidecars cannot drift apart about where the source project is.
2. `handle_snapshot_lineage` resolves the active project through `get_persist_path`,
   i.e. the context's own `storage_dir`, which it was handed all along.
3. `_create_scenario_db` copies `chat.jsonl` and its rotation backup. `chat.jsonl`
   stays OUT of `_BUNDLE_FILES` on purpose — it is a per-conversation thread, not
   part of the exportable bundle — so the copy is explicit.

`_project_chat_paths` survives as the documented fallback for local mode and the
legacy layout, where the flat path IS the project directory.

One test-only note worth keeping: the seam tests stubbed the hook with a narrow
signature, so the new keyword arguments raised a `TypeError` that the caller's
best-effort `except Exception` swallowed — and the call then looked as though it had
never happened. The stubs take `**kwargs` now.

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

**Status: FIXED**. Nothing moves on disk on that branch, so `project_dir(project)`
still resolves to the same directory and the rebind only rewrites the cached NAME —
that asymmetry with the move branch is the point. Guard: a web-mode sibling of the
existing local-mode rebind test in `tests/test_storage_layout.py`, which also pins
that the directory must NOT move.

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

Fix shape: state the identity with its actual scope, name branch capex as the
documented difference, point at the compare module as the place that does include it,
and drop the single measured figure — it was true of one network, not of the
invariant.

**Status: FIXED**. The docstring now leads with the endpoint's scope (Generator,
StorageUnit, Store, Link — no `lines` key, never `Transformer`) because two of its
three reconciliations turn on it, and marks the first as holding only where there is
no branch capex. The withdrawn figure is not re-measured: a number from one network
is not an invariant.

Measured while fixing, on a two-bus network with one priced line: Σ `fixed_cost_eur`
9,132.42 against Σ `capex_meur` × 1e6 of 9,771.69, the difference 639.27 being
exactly the `ac` carrier bucket. Guard:
`tests/test_asset_economics_reconciliation.py`, which pins the identity in both
directions — exact without branch capex, and off by exactly the branch capex with it
— plus a check that the corrected sentence cannot be edited back out silently. Both
arithmetic tests pass against the old code, which is the point: the arithmetic was
always right and only the sentence describing it was wrong.

---

## Moderate

(QA-N5 below was filed here and belongs in Serious — see its entry. It is left in
place so the register reads as it was written, with the correction attached.)

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
legitimate. Validate `attribute` against the component's time-varying attributes,
which IS refused: an attribute with no time-varying table can never be applied, so it
is a typo rather than a partial upload.

**Status: FIXED** — `columns` in the response is now the count applied rather than
the count submitted, and `unmatched_columns` names the rest. Guard:
`tests/test_timeseries_upload_unmatched.py`.

### QA-N5 — every GUI-only attribute is dropped on a from-scratch network

Filed as "bus `country` is dropped". Measuring it turned it into the most expensive
finding in this register.

- Sites: `models/schemas.py` (the `*Create` models) versus
  `services/network_crud.py::_drop_unknown_extras`.

`_drop_unknown_extras` keeps a key only if PyPSA's catalog reports it as an Input
attribute **or** it is already a column on the frame. Neither arm knows about the
attributes the GUI adds on purpose — and the Create models' own comments say they
exist: "Custom GUI columns, same pattern as `curtailment_cost`: stored on the
component DataFrame, no PyPSA meaning, netCDF round-trip for free."

Measured on the pinned PyPSA (1.1.2), the full casualty list on a fresh network:

| Component | Declared, dropped |
|---|---|
| Bus | `country` |
| Carrier | `unit` |
| Generator | `outage_rate_value`, `outage_rate_basis`, `mttr_hours`, `p_max_pu_includes_outages`, `curtailment_cost`, `unit` |
| Line, Store, StorageUnit | `outage_rate_value`, `outage_rate_basis`, `mttr_hours` |
| Link | the three above plus `bus2`, `bus3`, `bus4`, `efficiency2`, `efficiency3` |
| Transformer | `v_nom_0`, `v_nom_1` |

The adequacy trio is what makes this serious rather than cosmetic. A generator
created through the API with an explicit outage rate reads back as having none, so
the entire occurrence chain — COPT, Monte Carlo, the FMEA worksheet — falls through
to the per-carrier default library, silently, with nothing anywhere to show that the
user's number ever arrived. This is the integration's own primary input being
discarded at the front door.

It is invisible on networks imported from PyPSA-Eur, which arrive with the columns
already present and so pass on the second arm. Only the from-scratch path bites —
which is the path a new user takes, and the path every test fixture takes.

Fix shape: a third arm — the fields the component's `*Create` model declares. A
declared field is by definition something this API intends to persist, so the model
is the right authority, and the whitelist stays a whitelist: an undeclared,
uncatalogued key is still dropped.

**Status: FIXED** — `_declared_attributes`, cached, keyed by a
`_CREATE_MODEL_NAMES` map. Guard:
`tests/test_create_model_attributes_survive.py`, which pins the specific casualties
AND states the general invariant per component class, so a field added to a Create
model tomorrow is covered without anyone remembering the file exists. The control
pins that an undeclared key is still dropped — widening the filter to the declared
surface must not widen it to an arbitrary one.

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

**Status: FIXED** — one `_weight_value` parser at all five entry points (both
broadcasts and the per-period path on the investment-period PATCH, the broadcast and
per-row paths on the snapshot PATCH, and the CSV upload). Zero stays legal: a
zero-weight snapshot is how a user excludes an hour without deleting it, and the
tests pin that boundary. The per-period path was also made two-pass, matching the
rest of the module — it wrote row by row, so a rejection at row N left rows 0..N-1
applied.

### QA-S1 — lock-holder email reaches the LLM provider — NOT A FINDING

Carried into the review's list from the 2026-08-27 finding without being re-checked
against current `master`. It was fixed on 2026-09-12 and the finding document says so:
`chat_service._error_result_content` now builds the model-facing `is_error` content
from the typed `error_kind` plus only a dict detail's `message`, dropping `lock`,
`holder_email` and anything unrecognised, and
`tests/test_chat_error_content_seam.py` guards every branch.

Recorded here rather than deleted, because "a stale entry on the list" is the failure
mode a findings register exists to make visible.

**Status: ALREADY FIXED (2026-09-12) — withdrawn**

---

## Not reviewed

Two areas were in scope for the end-to-end pass and never got a reviewer:

- **Solver lifecycle** — queueing, cancellation, the myopic path, result publication.
- **Chat / LLM tooling** — the tool-dispatch surface in `chat_tools.py` and its schema.

Both should get the same treatment before the integration is called complete.
