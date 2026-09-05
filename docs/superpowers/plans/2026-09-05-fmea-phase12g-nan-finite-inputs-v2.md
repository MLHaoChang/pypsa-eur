# Phase 12g — a NaN in ANY finite-default LP input is refused, not read as zero (plan v2 — v1 plus its review, accepted with changes)

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
**Result (full tree, 43 failed / 2923 passed as the baseline, 23 min):**

| | |
|---|---|
| networks reaching `optimize()` | **392** |
| networks with a NaN in any finite-default input, static or dynamic | **0** |
| golden fixture (direct scan) | **clean** |

So the blanket rule refuses nothing that solves today, anywhere in the suite —
the opposite of the ramp-limit outcome, and for the reason §0 gives: every
fixture is built through `n.add`, which never writes NaN into these columns, and
netCDF persistence heals a static one. The cells this phase refuses are the ones
only a bypassing write can create.

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

---

## v1 REVIEW — ACCEPT WITH CHANGES (twelve findings; seven required, all verified)

Reviewed adversarially before a line was written; every finding below was
reproduced against PyPSA 1.3.0 / linopy 0.9.1 / pandas 3.0.5 before it was
recorded, and the seven required amendments are incorporated into the build.

| # | severity | finding | verified | amendment |
|---|---|---|---|---|
| 1 | MAJOR | the margin loop's guard filters on a literal tuple `("reserve_margin_unpriceable_assets", "nonfinite_bound", "nonfinite_bound_partial_coverage")` (`routers/results.py:4691`), so every new category would slip past it and the K6 pathology returns: the loop starts, every iterate refuses, `budget_exhausted` advises "raise max_solves". | yes — the tuple, read | filter on `code.startswith("nonfinite_")`; a K6-style ★ parametrised over one code per category through the margin route |
| 2 | MAJOR | the component scope was ambiguous ("seven / 118" in §1, "every component" in J1, and the walk iterates all 16 component objects); the census scanned the seven only. And **`GlobalConstraint.constant` is exactly this phase's defect**: finite default 0.0, `Input (optional)`, no check, and a NaN there **silently deletes a CO2 cap** — measured cap 100 → objective 11 000, gas 100 MWh; cap NaN → 3 000, gas 300 MWh. | yes — reproduced | the walk's component set is pinned explicitly: the seven **plus GlobalConstraint**; Bus/Carrier/Shunt/LineType stay out (checked elsewhere or PF-only); the census is **re-run over the implemented set** before the plan's "0 hits" is claimed |
| 3 | MAJOR | three mechanism sentences were false: `standing_loss` NaN masks no row and NaNs no coefficient — the previous-hour SoC term is dropped, so the store **forgets its charge every hour** (objective *rises*, 77 688 → 117 500; not "lossless"); `up_time_before` NaN on a non-committable unit with a ramp limit is read as `initially_up = NaN > 0 = False`, so a first-hour ramp row is **added** (+232 488); `Link.delay` NaN drops the receiving-end nodal term — the link consumes and delivers nothing (identical to `efficiency` NaN). "Read as missing" was wrong for all three. | yes — `create_model()` inspected; SoC `[100, -0, 100, -0]` | per-attribute consequence sentences: `standing_loss` "the carry-over of stored energy between hours is dropped"; `up_time_before`/`down_time_before` "read as 'the unit was off', which adds a start-up ramp constraint"; `delay` "the receiving end is dropped from the balance — the link consumes and delivers nothing" |
| 4 | MAJOR | the dedupe "message names the attribute" is substring matching over one-letter names: `line_x_invalid`'s message contains `b` (in "be"), `r` ("reactance"), `g` ("got"); `x` matches "extendable"/"max". A line with NaN `x` and NaN `b` would lose its `b` error. | yes — the messages, read | **no text matching**: the generic walk *skips* the `(component, attribute)` pairs a specific check owns (`efficiency`, `efficiency_store/dispatch`, `max_hours`, `x`, `Load.p_set` unconditionally; `*_nom` on a non-extendable row), so there is nothing to dedupe; a ★ places NaN in `x` **and** `b` on one line and asserts two errors |
| 5 | MINOR | `Transformer.phase_shift_max` has default **0.0** and is in the set, but `_bulk`'s `endswith("_max")` rule keeps priority and writes `inf`, which the solve then refuses (`ConsistencyError: transformers have an optimisable phase shift`). J2's "the `_max` targets are not in the set anyway" was false. | yes — default read | in the null branch the finite-default metadata takes priority over the suffix rules; ★ `phase_shift_max: null → 0.0` |
| 6 | MINOR | the set is network-dependent: a multi-port link adds `efficiency2`/`delay2` (finite, `Input (optional)`) to `Link.defaults` — measured 61 → 66 rows — and NaN there is inert on a two-port link; `link_efficiency_invalid` already validates only rows whose `bus{i}` is populated. | yes — reproduced | skip `efficiency{i}`/`delay{i}` on rows whose `bus{i}` is empty; J1e derives its expected count from the fixture's metadata rather than asserting 118 |
| 7 | MINOR | J4's "76 fields" is **67**: 58 float, 9 `int` (`build_year` ×6, `min_up_time`, `min_down_time`, `tap_side`); pydantic `int` already refuses NaN/inf/`"Infinity"` (measured 422), and a float `Finite` on an int field would rewrite `2020` as `2020.0`. | yes — 58 / 9 counted | `Finite` on the 58 float fields; the ints stay `int`; the set-equality test compares the float subset and asserts the ints refuse non-finite as they stand |
| 8 | MINOR | J1f's bite tests one of three filter clauses: dropping the `Input`-status clause or the numeric-type clause leaves the golden fixture **clean** (Output columns are finite pre-solve; bools cast to 0/1). | reviewer's probe | J1f also asserts the metadata-derived count per component, not silence alone |
| 9 | MINOR | under pandas 3.0.5 every `_bulk` write to an int64 column upcasts it to float64 — `= 0`, `= np.int64(0)`, `= 0.0` all do — so J2's `build_year → 0` would sit as `0.0` in a float column until the next reload heals it. Pre-existing for the NaN branch. | reviewer's probe | after the locked write, restore `int64` when the metadata type is `int` and the column holds no NaN; pinned |
| 10 | MINOR | attributes the LP never reads (`q_set`, `weight`, `length`, `terrain_factor`, `maintenance_*`, `build_year` on a flat network, `p_nom` on an **extendable** asset — measured inert, `Generator-ext-*` rows intact) would get a consequence sentence ("read as missing and the plan is built without it") that is false. Also: no external network exists in the tree to census, and PyPSA-Eur's own scripts `fillna` `build_year`/`lifetime`/`max_hours` but only `combine_first` `efficiency`, so an imported PyPSA-Eur network can carry a NaN `efficiency`/`length` and stops solving under the K3 stance. | reviewer's probe | the catch-all category gets a **neutral** sentence ("PyPSA gives this attribute a finite default and cannot represent 'unset' here; enter a value or clear the field to restore {default}"); the PyPSA-Eur consequence is recorded under "what moves for a user" |
| 11 | NIT | performance is fine — 8 760 h, 900 assets, six dynamic frames: today 21 ms; 118-attribute per-column prototype 49 ms; whole-frame vectorised 14 ms; with one misaligned 300-column frame 117 ms vs 21 ms. | reviewer's probe | align each dynamic frame **once** and vectorise `isfinite` over the block |
| 12 | NIT | `ValidationRefused`'s message says "LP bound(s)"; with 118 attributes the word is "input(s)". | yes | reworded |

**Verified sound by the review:** the 118 enumerates exactly as stated (24/24/14/18/20/15/3); the storage-constant (row masked), efficiency (term dropped), cost (term dropped) and `sign` (asset dropped from the nodal balance) mechanisms; `n.add(attr=None/NaN)` → class default across the set; netCDF heals static NaN and keeps dynamic; the anti-gap test costs 1.33 s for 154 `validate_for_run` calls and **no attribute is refused by two existing checks today** (120 of 154 cells are silent today); `inflow` coverage transfers (a 3-of-4 frame masks that hour's balance row); **S30.5 reproduced end to end on shipped code** — a 2-of-3 `inflow` PUT is accepted, preflight is silent, the foreground reapply reindexes to `[0, 0, NaN]`, `run_simulation` returns `optimal` with the balance masked (objective 4 679 → 3 679, dispatch from an empty store) and nothing else refuses it; R4's pin holds for `storage_units/inflow`; `snapshot_weightings` NaN and `Carrier.co2_emissions` NaN already have checks; no test posts `inf` into a Create schema; the frontend switches on no `nonfinite_*` code.

**What moves for a user (recorded, per finding 10):** a project imported from a PyPSA-Eur netCDF whose dynamic frames carry a NaN `efficiency` or whose static frame carries a NaN `length` (netCDF heals the static one on the GUI's own round trip, but a *first* import reads what the file holds) is refused at preflight with the attribute named, where before it solved with the term dropped. That is the phase's intent, and the message says what to do.

**Codes (final):** `nonfinite_bound`, `nonfinite_cost`, `nonfinite_efficiency`, `nonfinite_storage_constant` (incl. `GlobalConstraint.constant`), `nonfinite_input`, each with a `_partial_coverage` twin for a dynamic column that covers part of the horizon. The consequence sentence is per attribute within a category, from one table.
