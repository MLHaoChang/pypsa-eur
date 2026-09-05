# Phase 12g — a NaN in ANY finite-default LP input is refused, not read as zero (plan v1)

**Status:** plan v1, for adversarial review before a line is written.
**Predecessor:** Phase 12f (six plans, reviewed as shipped code, fixes reviewed in turn).
**Chosen from the backlog** as "the storage energy-balance NaN" — `StorageUnit.inflow`,
`state_of_charge_initial`, `Store.e_initial` — which plan v6 of 12f measured and
deferred. **Measuring the premise widened it**, the way 12f's own premise widened
when someone finally asked what the LP does.

## 0. The premise, measured

12f's rule was "NaN in a bound whose class default is finite masks the constraint
row". The deferral said three storage *constants* do the same to an energy-balance
row. Before planning three attributes, every finite-default numeric **input**
attribute of the seven LP components was surveyed — static NaN on one asset, and
for the varying ones a single NaN hour — on a four-hour fixture with a gas unit,
an expensive slack, a line, a storage unit, a store with charger/discharger
links, and two loads (`scratchpad/p12g/survey.py`; PyPSA 1.3.0, HiGHS). The
second table is the same survey on a variant where the storage unit must charge
(link capacity cut to 20 MW), because "the objective did not move" on one
fixture is not evidence that a value is inert — `efficiency_store` reads inert on
the first fixture and costs **+110 277** on the second.

### Fixture A (baseline objective 77 687.7)

| attribute | NaN placed | class default | measured effect |
|---|---|---|---|
| `Generator.p_nom` | static | 0 | objective 77,688 → 8,606 |
| `Generator.sign` | static | 1 | objective 77,688 → 859,146 |
| `Generator.marginal_cost` | static | 0 | objective 77,688 → 69,688 |
| `Generator.marginal_cost` | dynamic[1]=NaN | 0 | objective 77,688 → 75,688 |
| `Generator.up_time_before` | static | 1 | objective 77,688 → 310,177 |
| `StorageUnit.p_nom` | static | 0 | **unbounded** |
| `StorageUnit.sign` | static | 1 | objective 77,688 → 122,638 |
| `StorageUnit.marginal_cost` | static | 0 | objective 77,688 → 77,683 |
| `StorageUnit.marginal_cost` | dynamic[1]=NaN | 0 | objective 77,688 → 77,683 |
| `StorageUnit.state_of_charge_initial` | static | 0 | objective 77,688 → 6,307 |
| `StorageUnit.efficiency_dispatch` | static | 1 | objective 77,688 → 5,097 |
| `StorageUnit.efficiency_dispatch` | dynamic[1]=NaN | 1 | objective 77,688 → 7,695 |
| `StorageUnit.standing_loss` | static | 0 | objective 77,688 → 117,500 |
| `StorageUnit.standing_loss` | dynamic[1]=NaN | 0 | objective 77,688 → 85,746 |
| `StorageUnit.inflow` | static | 0 | objective 77,688 → 5,097 |
| `StorageUnit.inflow` | dynamic[1]=NaN | 0 | objective 77,688 → 5,839 |
| `Store.e_nom` | static | 0 | objective 77,688 → 5,917 |
| `Store.e_initial` | static | 0 | objective 77,688 → 6,866 |
| `Store.sign` | static | 1 | objective 77,688 → 181,839 |
| `Store.standing_loss` | static | 0 | objective 77,688 → 140,934 |
| `Store.standing_loss` | dynamic[1]=NaN | 0 | objective 77,688 → 136,311 |
| `Link.efficiency` | static | 1 | objective 77,688 → 86,935 |
| `Link.p_nom` | static | 0 | objective 77,688 → 57,691 |
| `Link.marginal_cost` | static | 0 | objective 77,688 → 77,687 |
| `Link.delay` | static | 0 | objective 77,688 → 86,935 |
| `Line.x` | static | 0 | **build fails** — LinAlgError:SVD did not converge |
| `Load.p_set` | dynamic[1]=NaN | 0 | objective 77,688 → 606 |
| `Load.sign` | static | -1 | objective 77,688 → 606 |

### Fixture B — the storage unit must charge (baseline 113 131.3)

| attribute | NaN placed | class default | measured effect |
|---|---|---|---|
| `StorageUnit.p_nom` | static | 0 | **unbounded** |
| `StorageUnit.sign` | static | 1 | objective 113,131 → 268,359 |
| `StorageUnit.marginal_cost` | static | 0 | objective 113,131 → 113,116 |
| `StorageUnit.marginal_cost` | dynamic[1]=NaN | 0 | objective 113,131 → 113,121 |
| `StorageUnit.state_of_charge_initial` | static | 0 | objective 113,131 → 67,925 |
| `StorageUnit.efficiency_store` | static | 1 | objective 113,131 → 223,408 |
| `StorageUnit.efficiency_dispatch` | static | 1 | objective 113,131 → 66,777 |
| `StorageUnit.efficiency_dispatch` | dynamic[1]=NaN | 1 | objective 113,131 → 69,258 |
| `StorageUnit.standing_loss` | static | 0 | objective 113,131 → 267,913 |
| `StorageUnit.standing_loss` | dynamic[1]=NaN | 0 | objective 113,131 → 212,775 |
| `StorageUnit.inflow` | static | 0 | objective 113,131 → 66,777 |
| `StorageUnit.inflow` | dynamic[1]=NaN | 0 | objective 113,131 → 67,341 |
| `Generator.p_nom` | static | 0 | objective 113,131 → 8,795 |
| `Generator.sign` | static | 1 | objective 113,131 → 878,126 |
| `Generator.marginal_cost` | static | 0 | objective 113,131 → 105,131 |
| `Generator.marginal_cost` | dynamic[1]=NaN | 0 | objective 113,131 → 111,131 |
| `Generator.up_time_before` | static | 1 | objective 113,131 → 337,389 |
| `Link.p_nom` | static | 0 | objective 113,131 → 57,691 |
| `Line.x` | static | 0 | **build fails** — LinAlgError:SVD did not converge |

**What the tables say.** On these two fixtures a NaN silently changes the plan
in **23** distinct attributes, across every LP component class, and crashes the
build in one (`Line.x`: `LinAlgError`). The mechanisms differ — a NaN cost drops
the cost term (the unit dispatches as free), a NaN efficiency or standing loss
masks the conversion or energy-balance row, a NaN `inflow`/`state_of_charge_initial`/
`e_initial` masks the balance's right-hand side (the deferred three — a store
becomes a free source), a NaN `sign` inverts nothing and breaks everything, a NaN
`up_time_before` or `delay` engages a constraint that should not exist — but the
*shape* is one: **linopy and xarray propagate NaN by dropping the term or the row,
never by defaulting it.** Attributes that read inert here (`capital_cost` on a
non-extendable asset, `length`, `build_year`, `weight`, `q_set`, `spill_cost`,
`marginal_cost_quadratic`, `stand_by_cost`, `maintenance_*`, `min_up_time` on a
non-committable unit …) are inert *on these fixtures*, which is not a claim that
they are inert.

### PyPSA's own reading of "unset" — `None` and NaN both become the class default

`n.add("Generator", …, marginal_cost=None, efficiency=None, sign=None, p_nom=None,
up_time_before=None, capital_cost=None)` writes `0.0, 1.0, 1.0, 0.0, 1, 0.0`;
the same for `inflow`, `state_of_charge_initial`, `efficiency_dispatch`,
`standing_loss`, `max_hours`, `e_initial`, `e_nom`, `Link.efficiency`, `delay`,
`Line.x/r/s_nom`. And an **explicit `np.nan`** does the same: `marginal_cost=np.nan`
→ `0.0`, `efficiency=np.nan` → `1.0`. So for every attribute whose class default
is finite, NaN is **never** PyPSA's spelling of "unset" — its spelling of unset
*is the finite default*. A NaN cell in one of these can only be manufactured by a
frame write that bypasses `n.add`: `PATCH /_bulk`'s null branch (12f fixed it for
five columns and left the other 113 writing NaN), a partial or mismatched
time-series index, a netCDF or CSV import that carried one, or a reindex.

### Where a NaN survives, and where it heals

| path | static NaN | dynamic NaN |
|---|---|---|
| `n.add(attr=None)` / `n.add(attr=NaN)` | class default | — |
| netCDF round trip (project persistence) | **heals** to the class default (measured `marginal_cost`, `state_of_charge_initial`) | **survives** (`inflow` `[1, NaN, 1]`) |
| `_user_ts` re-injection on every solve | — | survives, re-applied |
| `PATCH /_bulk` null | NaN today for every column outside 12f's five (`marginal_cost`, `state_of_charge_initial`, `inflow`, `standing_loss`, `e_initial`, `sign`, …) | — |
| `PUT /timeseries`, uploads, chat tool | — | **refused** since the 12f review for every attribute with a finite default (R4's rule) — nothing to add, one pin to write |
| properties panel | never sends null for a numeric field (`numField` falls back to the current value or 0) — not a source | — |

### What the preflight refuses today, measured on Fixture A

| attribute | static NaN | dynamic NaN |
|---|---|---|
| `p_nom` / `s_nom` / `e_nom` (non-extendable) | `*_p_nom_invalid` etc. | — |
| `efficiency`, `efficiency_store`, `efficiency_dispatch`, `Link.efficiency` | `*_efficiency_invalid` | **silent** |
| `max_hours` | `storage_max_hours_invalid` | — |
| `Line.x` | `line_x_invalid` | — |
| `Load.p_set` | (`load_p_set_nan`) | `load_p_set_nan` |
| the five bounds | `nonfinite_bound` (12f) | `nonfinite_bound*` (12f) |
| `sign`, `marginal_cost`, `up_time_before`, `state_of_charge_initial`, `standing_loss`, `inflow`, `e_initial`, `delay`, `Load.sign` | **silent** | **silent** (`marginal_cost`, `standing_loss`, `inflow`) |

`storage_soc_oob` is gated on `_is_finite(soc)`, so a NaN initial state passes the
one check that exists for it. The existing checks are value checks that happen
to catch NaN on the way; none of them covers a dynamic column.

### The census (the ramp-limit lesson)

12f's earlier scope would have blocked every network in the repository because
`ramp_limit_*` is NaN by design. So before the rule below is trusted, every
network that reaches `optimize()` during the full backend suite, plus the golden
fixture, is censused for NaN cells in *any* of the 118 finite-default input
attributes (`scratchpad/nan12g.py`, a pytest plugin hooking `OptimizationAccessor.__call__`).
**Result:** *(appended below when the run completes — the plan is not accepted
without it.)*

## 1. The rule

> A non-finite value in a numeric **input** attribute of an LP component whose
> PyPSA class default is **finite** is an ERROR at preflight and a 422 at every
> write boundary, and clearing such a field writes the class default.

That is R4's rule from the 12f review, already in force for time-series writes,
made the rule everywhere. The scope is read from PyPSA's own component metadata
(`n.components[c].defaults`: `status` starts with `Input`, numeric type, finite
default) so it cannot drift from the installed PyPSA and needs no edit when PyPSA
adds an attribute. On 1.3.0 it is 118 attributes over Generator (24), Link (24),
Line (14), Transformer (18), StorageUnit (20), Store (15), Load (3) — 12f's five
among them. Booleans and strings are not numeric and are out. Every attribute
whose default is NaN or ±inf is out by construction: `ramp_limit_*`,
`Generator.p_set`/`Link.p_set`, `state_of_charge_set`, `p_init`, `*_nom_max`,
`lifetime`, `e_sum_min/max`, `overnight_cost`, `discount_rate`. Every custom GUI
column (`outage_rate_value`, `mttr_hours`, `curtailment_cost`, the adequacy
columns) is not in the table and is untouched — K1d's guarantee stands.

Not a repair, for the reasons 12f's six plans learned: there is no value to
substitute, because the finite default is *exactly* the value the user did not
enter, and writing it silently is the defect (a free store, a zero-cost unit) in
a quieter form.

## 2. Mechanisms

**J1 — preflight generalised.** `_nonfinite_bound_hits` becomes
`_nonfinite_input_hits(n)`, walking the finite-default input set per component
from the metadata (fallback to 12f's five if the table is unreadable, never to
nothing — R3's rule). Same static/dynamic branches, same horizon-coverage
judgement (R10), same shadow and ghost rules. `_check_nonfinite_bounds` becomes
`_check_nonfinite_inputs` (old name kept as an alias for the solver's three
checkpoints and the loops' guards, which then cover the whole set for free) and
emits **one code per consequence category**, because "a 100 MW unit can dispatch
500 MW" is the wrong sentence for a missing cost:

| category | attributes | code | what the message says |
|---|---|---|---|
| bound | the five | `nonfinite_bound` / `_partial_coverage` (unchanged) | the constraint row is dropped; the asset is unbounded there |
| cost | `marginal_cost`, `marginal_cost_quadratic`, `marginal_cost_storage`, `spill_cost`, `stand_by_cost`, `start_up_cost`, `shut_down_cost`, `capital_cost`, `fom_cost` | `nonfinite_cost` | the cost term is dropped; the asset is dispatched or built as free |
| conversion | `efficiency`, `efficiency_store`, `efficiency_dispatch`, `standing_loss` | `nonfinite_efficiency` | the conversion / loss term is dropped from the energy balance |
| storage constant | `inflow`, `state_of_charge_initial`, `e_initial` | `nonfinite_storage_constant` | the balance's right-hand side is dropped; the store becomes a free source (measured 113 131 → 66 777) |
| physics / topology | `sign`, `x`, `r`, `g`, `b`, `num_parallel`, `tap_ratio`, `tap_side`, `tap_position`, `phase_shift*`, `length`, `terrain_factor`, `delay`, `*_nom`, `*_nom_min`, `*_nom_mod`, `max_hours`, `weight`, `build_year`, `maintenance_*`, `min_up/down_time`, `up/down_time_before`, `q_set`, `p_set` | `nonfinite_input` | PyPSA does not default this value at solve time; it is read as missing and the plan is built without it |

A **dynamic** NaN in any category keeps the coverage/corruption split of 12f
(`…_partial_coverage` when the column covers part of the horizon), because the
representative-week workflow applies to an inflow series exactly as it does to
an availability series.

**Double reporting is measured out, not reasoned out.** Where an existing value
check already refuses NaN (`*_nom_invalid`, `*_efficiency_invalid`,
`storage_max_hours_invalid`, `line_x_invalid`, `load_p_set_nan`), `_check_lopf`
drops the generic issue for the same `(component, name)` whose message names the
same attribute, so the user sees the specific sentence once. A parametrised test
over **all 118 attributes** asserts that a static NaN — and for every varying
one, a dynamic NaN — is refused by *exactly one* error naming the asset. That
test is the phase's anti-gap witness, and it is derived from the metadata, so a
PyPSA that adds an attribute fails it rather than slipping past.

**J2 — `_bulk` generalised.** The null branch writes the class default for every
finite-default PyPSA column (12f did five), through the same
`_finite_bound_default` (renamed `_finite_input_default`, metadata first,
12f's mapping as the fallback); the `_max`/`lifetime`/`e_sum_min` rules keep
priority (their defaults are ±inf, so they are not in the set anyway); custom
columns still clear to NaN. The non-finite-literal refusal (R5) widens to the
same set.

**J3 — time series.** Nothing to build: R4's `_attribute_default_is_finite`
already refuses NaN in `storage_units/inflow`, `efficiency_dispatch`,
`standing_loss`, `marginal_cost`, … One pin per category through
`set_timeseries`, and the chat tool.

**J4 — create/update schemas.** A `Finite` annotated type
(`Annotated[float, Field(allow_inf_nan=False)]`) on every schema field that is
a finite-default PyPSA numeric attribute — 12f did five; the mapping above lists
76 across the seven Create models. The `_NoneToPosInf` /
`_NoneToNegInf` fields are ±inf by design and stay. A test derives the expected
field set from PyPSA's table and asserts the annotation set equals it.

**J5 — solver checkpoints.** Unchanged code, widened coverage through the alias;
`ValidationRefused` and its handler carry the new codes as they carry the old.

**J6 — imports.** netCDF heals static NaN and keeps dynamic; CSV import keeps
both. Both are K3's stance: the preflight is the safety net, and it now has no
gaps in the input set.

## 3. Tests (every ★ with its named bite)

- ★ J1a–J1d: one static case per category through `validate_for_run` (not the
  helper), asserting code, severity and the asset named. Bites: drop the category
  from the walk; emit the bound message for a cost.
- ★ J1e: the anti-gap test over all finite-default inputs, static and dynamic —
  exactly one error each. Bite: remove the metadata walk (only the five remain).
- ★ J1f: the golden network and every adequacy fixture stay **silent**. Bite:
  include NaN-default attributes (`ramp_limit_*`) — the golden fixture's eight
  cells fire.
- ★ J1g: no double report — NaN `max_hours`, `efficiency_store`, `p_nom`, `x`
  each produce one error. Bite: drop the dedupe.
- ★ J1h: dynamic `inflow` covering 2 of 3 snapshots → `nonfinite_storage_constant_partial_coverage`; the store's energy balance measured on the fixture (`run_simulation` end to end, background branch as R9). Bite: delete the storage-constant category.
- ★ J2a–J2c: `_bulk` clears `state_of_charge_initial` → 0.0, `marginal_cost` → 0.0, `sign` → 1.0; `discount_rate` still NaN (K1d); a NaN literal in `inflow` → 422. Bites: restore the five-only set.
- ★ J4a: schemas refuse `Infinity` in `inflow`, `standing_loss`, `marginal_cost`; the annotation set equals the metadata-derived set. Bite: drop one annotation.
- ★ J3a: `PUT /timeseries/storage_units/inflow` null → 422 (pin of R4 for this phase's attributes).

## 4. Live — S30

| id | check |
|---|---|
| S30.1 | `PATCH /_bulk` clearing `state_of_charge_initial` → 200 and reads back **0.0**, not null |
| S30.2 | clearing `marginal_cost` → 200 and reads back **0.0** |
| S30.3 | `_bulk` NaN literal in `inflow` → **422**, value untouched |
| S30.4 | `POST /storage_units` with `inflow: Infinity` → **422**, nothing created |
| S30.5 | a 2-of-3-row `inflow` profile uploaded, then Run → refused `validation_failed` naming the storage unit and `inflow`; **bitten live** by restoring the five-only walk: the solve runs `optimal` with the balance masked |
| S30.6 | `PUT /timeseries/storage_units/inflow` with a null → 422 (pin) |

## 5. Out of scope, recorded

- Bus (`v_nom` has `bus_v_nom_invalid`), Carrier (`carrier_co2_nan`),
  GlobalConstraint, SubNetwork, Shunt — not LP dispatch inputs in this GUI's
  sense; a follow-up if the census says otherwise.
- The K5 coverage discriminator (`_user_ts` vs reindex) — unchanged.
- A WARNING tier for attributes that are inert on every measured fixture: not
  built, because "inert on the fixtures I tried" is the reasoning this program
  has been wrong with before. If the census finds real networks carrying such
  cells, that becomes the decision for the review.
