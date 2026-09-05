# Phase 12f — a non-finite LP bound is refused, where "bound" means finite-default (plan v6)

**Status:** proposed. v1–v5 rejected — **sixteen blockers**, every one
reproduced. v5's *approach* (refuse, do not repair) was accepted by its review;
its **scope** was wrong, and wrong in a way that would have stopped every
network in the repo from solving. v6 narrows the scope and corrects a
mis-diagnosis of my own.

## 0. A correction: the ramp-limit finding was not a defect

v4's review recorded, and I accepted, that one NaN hour in `ramp_limit_up`
"removes the ramp constraint for the whole horizon" — measured
`[0, 100, 100, 100]` against `[0, 10, 20, 30]`. **That is PyPSA behaving as
designed.** `Generator.ramp_limit_up`'s class default *is* `NaN`, and
`pypsa/optimization/constraints.py:1046,1136` masks the row on purpose:
`no_up_limit = limit_up.isnull() & limit_start.isnull()`,
`mask_up = mask & ~no_up_limit`. A null ramp limit is the documented way to say
*this unit has no ramp limit*. What the measurement actually shows is narrower:
a **partially** specified dynamic column is resolved as "no limit", which is
ambiguous input rather than a deleted constraint.

So the defect is **not** "any non-finite value in any LP bound". It is:

> **a non-finite value in a bound whose class default is FINITE** — where NaN
> has no meaning, and the masked row is a silent loss of a real constraint.

Those are exactly five attributes: `p_max_pu`, `p_min_pu`, `s_max_pu`,
`e_max_pu`, `e_min_pu`. The measurements that survive all five reviews:

| carrier of the non-finite value | dispatch of a 100 MW unit |
|---|---|
| dynamic `p_max_pu = [0.5, NaN, 1.0]`, load 500 | `[50, **500**, 100]` |
| dynamic `p_min_pu = [0, NaN, 0.9]`, cheap slack | `[0, **−900**, 90]` |
| **static** `p_max_pu = NaN`, no dynamic column | `[**500, 500, 500**]` |

## 1. Why the scope matters — measured

`ramp_limit_*` is not an edge case: **NaN is its normal state.** The golden
network (`tests/golden/fixture.py`) carries **8** non-finite static bound
cells, *all* of them `ramp_limit_up`/`down` on `gas`, `solar`,
`diesel_backup`, `electrolyzer` — and **zero** on the five finite-default
attributes. Across the suite the review measured **68 of 68** solving networks
carrying a NaN `ramp_limit_*`, and **0** hits on the five.

So v5's J4c ("preflight errors on a NaN `ramp_limit_up`") would have blocked
every network in this repository, while v6's rule is **silent on all 68** —
which is the property a new ERROR must have before it can ship.

## 2. What is written when a bound is cleared

**[K1] A cleared bound is written the way PyPSA's own `None` coercion writes
it** — verified: `n.add("Generator", …, p_max_pu=None, ramp_limit_up=None)`
gives `p_max_pu = 1.0`, `p_min_pu = 0.0`, `ramp_limit_up = NaN`.

`PATCH /network/_bulk` already does this for part of the surface
(`routers/network.py:2024-2029`): `*_max` and `lifetime` → `inf`, `e_sum_min`
→ `−inf`, *"likewise their PyPSA default, so the resulting network is valid"*.
It then falls through to `NaN` for everything else. K1 extends the rule to the
five finite-default bounds — `p_max_pu → 1.0`, `p_min_pu → 0.0` (Generator,
Link) and **−1.0** (StorageUnit), `s_max_pu → 1.0`, `e_max_pu → 1.0`,
`e_min_pu → 0.0` — and **leaves `ramp_limit_*` writing NaN**, because there
that is correct.

v5 said "NaN is never written for a bound". That was wrong, and it is the
sentence this section replaces. (`Process` has no `p_min_pu` — checked — so it
is not in the list.)

## 3. Refusing, and where the check has to run

**[K2] Non-finite in a dynamic write → 422.** Five routes and one shared
parser, not "six routes": `PUT /timeseries` (`routers/network.py:3153`),
`POST /timeseries/upload` (`:3183`), the three `upload_profile` routes
(`:3704`, `:3984`, `:4097`) which all call `_parse_upload` (`:3006`), plus
`POST /import/excel` (`routers/io.py:236`). The validator lives **in the
handler body**, not in a FastAPI `Depends()`, because the chat tools call the
handlers directly (`services/chat_tools.py:739, 864, 878, 898`) and a
dependency would be bypassed. It refuses a non-finite cell in **any** dynamic
column, not only bounds — `upload_load_profile` writes `p_set`, which is not a
bound and is still corrupt as NaN.

**[K3] A netCDF import still lands.** It is someone else's file. Note,
measured: a **static** NaN bound does not survive netCDF at all — a single
asset round-trips to `1.0`, and `{g1: NaN, g2: 0.4}` comes back
`{g1: 1.0, g2: 0.4}` — so it self-heals across a project save/load
(`routers/projects.py:1532`). Only a **dynamic** NaN survives, which is what
K5's fixture must use.

**[K4] Preflight errors on a non-finite value in the five, static or dynamic —
and runs where it can see them.** v5 put the check only at
`validate_for_run` (`solver_service.py:813`). That is **before**
`_reapply_user_ts_to_network` (`:906`) and `_normalise_dynamic_indexes`
(`:925`), both of which *manufacture* NaN from legal input. So the check runs
in both places: at `:813` for what the user's network already carries, and
again after `:925` for what the solve path created — the second is what
actually protects the LP.

**[K5] The manufactured tail is a coverage error, not corruption.** Measured,
with no invalid write anywhere: a finite 3-hour profile in `_user_ts`, then a
horizon extended to 5 hours, gives `p_max_pu = [0.5, 0.6, 0.7, NaN, NaN]`
(`routers/network.py:2787`). "Upload a representative week, then extend the
horizon" is a routine workflow. The message must say so — *your profile covers
3 of 5 snapshots; the uncovered hours have no availability* — and name the
count. It must **not** call a legal upload corrupt. No repair: silently
extending the user's week across a year is a modelling decision they did not
make, and this codebase's rule is that missing evidence is named, never
guessed.

**[K6] The margin lever must learn the new code.** `routers/results.py:4664`
re-raises exactly one preflight code as an up-front 422
(`reserve_margin_unpriceable_assets`). Without adding K4's code, every iterate
returns an unrecognised `validation_failed`, `_margin_out_of_reach` does not
confirm it, and the loop ends `budget_exhausted` advising "raise max_solves" —
the exact pathology the comment at `:4658` says that 422 exists to prevent.
v5's "no change to the margin lever" was false.

**[K7] Nothing else in the solve path changes.** No repair anywhere, no change
to the adequacy engines, `/copt`, `series_is_informative`, or the margin
lever's *numbers*. `tests/test_adequacy_profiled_units.py:145` stays green.

## 4. What moves for a user

- **Every network in this repo today: nothing.** Measured: 0 hits on the five
  across 68 solving networks and the golden fixture.
- Writing a non-finite series: **422** instead of silent corruption.
- Clearing `p_max_pu` in the bulk toolbar: writes `1.0`, not NaN — a fix to
  that affordance. Clearing `ramp_limit_up` still writes NaN, which is correct.
- A network carrying a non-finite value in one of the five **stops solving**
  and is told which asset. It solved before, with a 100 MW unit generating
  500 MW.
- Extending the horizon past an uploaded profile now **names the uncovered
  hours** instead of silently unbounding them.
- `GET → PUT` round trip on such a network fails (GET renders non-finite as
  `null`, `routers/network.py:3143`). Accepted; the round trip was preserving
  corruption.

## 5. ★ tests

| ★ | claim | fixture / path | bite |
|---|---|---|---|
| **K1a** | `_bulk` `p_max_pu: null` → **1.0**; network solves bounded | HTTP | keep the `float("nan")` fallthrough → 500 MW |
| **K1b** | `StorageUnit.p_min_pu: null` → **−1.0** | HTTP | one constant for every bound |
| **K1c** | `ramp_limit_up: null` → **NaN**, and the network still solves | HTTP | write 0.0 or 1.0 there |
| **K1d** | `discount_rate: null` stays NaN (`tests/golden/fixture.py:52` leaves it NaN deliberately) | HTTP | apply K1 to every column |
| **K2a** | `PUT /timeseries` with a `null` cell → 422 naming column and row | HTTP | drop the validator |
| **K2b** | the same for `Infinity` | HTTP | check `isnan` only |
| **K2c** | each write path refuses — a table of (route, payload builder), because the shapes differ (JSON body vs multipart vs CSV-of-asset-columns) | HTTP ×6 | guard `PUT` only; the others stay green |
| **K2d** | a finite series still writes | HTTP | refuse everything |
| **K3a** | a netCDF carrying a **dynamic** NaN bound imports 200 | HTTP | refuse the import |
| **K4a** | preflight errors on a NaN **static** `p_max_pu`, naming the asset | in-process | scan the dynamic frame only |
| **K4b** | preflight errors on a NaN **dynamic** `p_max_pu`, naming the hours | in-process | scan the static frame only |
| **K4c** | preflight is **silent** on the golden network and on a NaN `ramp_limit_*` | golden fixture | include NaN-default attributes → 8 errors on the golden network |
| **K4d** | the post-normalisation check catches a NaN the solve path manufactured | short profile + extended horizon | run the check only at `:813` |
| **K5a** | the coverage message names 3-of-5, not "corrupt" | as K4d | reuse the corruption wording |
| **K6a** | the margin lever 422s up front on the new code, and does not reach `budget_exhausted` | route | leave the code out of the list |
| **K7a** | the engines are untouched: `test_a_NaN_hour_is_availability_zero_at_attachment` and the `/copt` payload byte-identical | `_m1_network` | apply any repair from v2–v4 |

**K4c and K7a are the two regressions that five rejected designs would have
broken** — the first blocks every network in the repo, the second loosens the
firm-capacity standard. Both are named so neither can return by accident.

## 6. Out of scope

- The flat→MultiIndex broadcast (`routers/network.py:2725`) and the
  reapply-tail *repair*. K4/K5 make both visible and named; correcting the data
  is the user's.
- Any change to the engines, `/copt`, or `series_is_informative`.
- The static-CF per-asset flag; the `validate` route's TOCTOU.

## 7. Live area — S29

| id | check |
|---|---|
| S29.1 | `PUT /timeseries` with one `null` → 422, body names the column |
| S29.2 | `PATCH /_bulk` `p_max_pu: null` → 200; `GET` reads `1.0`; solve bounded |
| S29.3 | a dynamic NaN bound, imported by netCDF, is refused at solve and the failure card names the asset |
| S29.4 | the same network corrected solves, and the 100 MW unit dispatches ≤ 100 |
| S29.5 | upload a 3-hour profile, extend to 5 snapshots, solve → the coverage error names 3 of 5 |

S29.3's construction is netCDF-with-a-dynamic-NaN, the only route that still
produces a corrupt network once K1 and K2 land. Bitten live by removing the
validator (S29.1) and the fallthrough (S29.2).

---

## v6 REVIEW — ACCEPT WITH CHANGES. Nine amendments, all measured.

The reviewer enumerated every finite-default Input attribute in PyPSA 1.3.0 and
counted **masked LP rows directly** (`labels == -1` per constraint) on a
13-asset fixture across Generator/Link/StorageUnit/Store/Line/Transformer,
fixed and extendable. The five are confirmed, in both frames — and `Line`/
`Transformer` `s_max_pu` masks **both** the `-lower` and the `-upper` row, not
just the upper. It then hooked `create_model` across **427 LP builds** in the
backend suite. The amendments below are binding on implementation.

1. **`Process` DOES have `p_min_pu`/`p_max_pu`.** §2's "checked" sentence is
   wrong. K1's mapping stays keyed by `(component_class, column)` — `_bulk`
   cannot reach Process (`routers/network.py:286`) — but **K4 walks by
   attribute name over `n.iterate_components()`**, never an enumerated
   component list, so Process and any future component are covered.
2. **Measured and deferred, named so "exactly five" is not read as "nothing
   else".** `StorageUnit.inflow`, `StorageUnit.state_of_charge_initial` and
   `Store.e_initial` have finite defaults and each masks a storage
   **energy-balance** row — objective 40 000 → 10 000 measured, a 200 MWh
   battery becoming a free 300 MWh source. They are constraint *constants*,
   not bounds, so they are out of scope here and recorded as the follow-on.
   Also checked and already blocking ERRORs, so K4 must **not** duplicate
   them: `p_nom_min`/`s_nom_min`/`e_nom_min` (`validation_service.py:389`),
   `max_hours` (`:1304`), `Load.p_set` (`:308`).
3. **The silence claim is restated.** Not "0 hits / 68 networks" but **"0 hits
   among networks that reach `validate_for_run`"**. Two builds in the suite do
   carry a dynamic NaN `p_max_pu` — `tests/test_adequacy_reserve_margin.py:1712`
   and `:1737` — and survive only because their helper calls
   `n.optimize.create_model()` directly (`:88`), bypassing preflight. A ★ pins
   that the check lives in **preflight, not the LP-build path**, so they stay
   green.
4. **Four check points, and only the first may bare-return.**
   `_apply_modelling_assumptions` (`:987`) step 4 (`:5239`) promotes
   flat→MultiIndex, which is why `_normalise_dynamic_indexes` runs **again at
   `:1141`** and manufactures NaN there too; myopic needs a fourth at `:6427`.
   `restore_modelling` is bound at `:1004` and the `finally` at `:1604` does
   **not** call it — so a bare `return` at `:1141`/`:6427` would leave the
   network carrying vintage clones, VOLL slacks and recomputed `capital_cost`.
   Those points **raise** (or call `restore_modelling()`); only `:925` returns.
5. **K2 is five routes and THREE guard points**, not six: the `set_timeseries`
   body, the `upload_timeseries` body, and `_apply_profile_upload`
   (`routers/network.py:3663`) for all three profile routes — `_parse_upload`
   is the wrong seam, it does not know the component or attribute.
   **`POST /import/excel` is dropped**: measured, it cannot write a non-finite
   bound at all, because `n.add` coerces both `None` and literal NaN to the
   class default. A guard there is dead code.
6. **A seventh write path must be guarded**: `services/chat_tools.py:2214`
   (`apply_demand_from_excel`) writes `loads_t.p_set` and `_user_ts` directly,
   bypassing every handler. Its only numeric gate is `pd.to_numeric(...,
   errors="raise")`, which passes NaN through — verified.
7. **`POST /import/csv` (`routers/io.py:217`) belongs beside netCDF in K3** —
   it carries a dynamic NaN bound in intact. K4 is its safety net.
8. **K5's discriminator must be named**: coverage-vs-corruption is decided by
   `_user_ts`' finite coverage for that `(component, attribute, column)`
   against `len(n.snapshots)`, reached by the lazy import
   `solver_service.py:905` already uses. Without it K5a is unwritable.
9. **K4d is re-fixtured and K6 corrected.** `POST /snapshots` already reapplies
   (`routers/network.py:1352`), so the short-profile-plus-horizon NaN is
   present *before* `:813` and does not separate the check points; the fixture
   that does is a **flat `_t` frame against MultiIndex snapshots** — finite at
   `:813`, `[0.5,0.6,0.7] → [nan]×6` after `:925`. And `routers/results.py:4664`
   calls `_check_reserve_margin`, a single sub-validator — so the new check
   must be **called** there, not merely added to the code filter at `:4665`.

**Placement:** the check lives inside `_check_lopf`, and the later points are
gated on `config.mode == "lopf"`, matching the LOPF-only rationale at
`validation_service.py:1854`.

**One deliberate tension, recorded:** §0 says a NaN `ramp_limit_*` is legal, and
K1c writes one via `_bulk` — but K2 refuses a non-finite cell in *any* dynamic
column, so the same value cannot be written through `PUT /timeseries`. Measured
risk: 0 dynamic ramp columns across 427 builds. Deliberate.

**Also verified sound, no action:** K1's coercion mapping is exact for all five
across all six components (`n.add(attr=None)` and `n.add(attr=NaN)` both give
the class default); K3's netCDF asymmetry (static NaN self-heals to 1.0,
dynamic survives) and the same for `user_ts.json`; K1d's two premises
(`tests/golden/fixture.py:53` leaves `discount_rate` NaN deliberately — v6
cited `:52`, off by one — and `_bulk` accepts it); the failure card at
`failure_taxonomy.py:31` already renders a named per-asset validation error;
and no persistence hazard — `_backup_network_ts_to_user_ts`
(`routers/network.py:2503`) only overwrites an absent or all-NaN entry, so a
manufactured tail cannot overwrite a user's short profile.

**Implementation caveat for K1a:** a static write is inert when the asset
carries a dynamic column (PyPSA prefers `_t`), and `_bulk` does not clear it —
so K1a's fixture must use an asset with **no profile**, or it passes for the
wrong reason.

---

## SHIPPED-CODE REVIEW — eight findings, all verified, all closed

The built phase was reviewed adversarially as shipped code (the program's
standing discipline). Every finding below was reproduced against the code
before it was recorded; the reviewer's evidence is quoted where it is the
measurement that decided the fix.

| # | severity | finding | verified | resolution |
|---|---|---|---|---|
| 1 | MAJOR | `_reject_nonfinite_timeseries` refused NaN in **every** dynamic column, so `Generator.p_set`, `Link.p_set`, `StorageUnit.state_of_charge_set` and `ramp_limit_*` — whose class default **is** NaN, meaning "not fixed at this hour" — became unwritable through `PUT /timeseries`. Measured: `p_set = [NaN, 20, NaN]` solves `optimal` with dispatch `[50, 20, 50]`. | yes — defaults table read: `Generator.p_set` NaN, `Load.p_set` 0.0 | the validator now follows the **same rule as the preflight**: refuse only where the class default is finite (`_attribute_default_is_finite`, resolved through PyPSA's component registry, which accepts list and class names). `Load.p_set` (default 0.0) is still refused — K2e holds. Unknown attribute → refused, as before. **This supersedes v6's "deliberate tension"** paragraph: the tension was recorded for ramp columns only, and its measured risk (0 dynamic ramp columns in 427 builds) did not cover `p_set`. |
| 2 | MAJOR | a static NaN was flagged even when a finite dynamic column shadows it — PyPSA reads `_t` first, so the LP never sees that cell. Every project in which pre-12f `_bulk` cleared a profiled asset's bound carries this shape. | yes — `hits == [('Generator','p_max_pu','g',1,1)]` while `get_switchable_as_dense` reads `[0.5, 0.6, 0.7]` | the static branch skips any asset whose name is a column of the attribute's dynamic frame; the dynamic branch still judges the profile itself (R1 asserts both halves). |
| 3 | MAJOR (tests) | the wiring — the one line in `_check_lopf` and the three solver checkpoints — had **no witness**: every K4 case called the helper directly, K6 went through the loops, which also call it directly. Deleting all four left 21/21 green. | yes — `grep run_simulation\|validate_for_run\|_check_lopf tests/test_nonfinite_bounds.py` matched a docstring only | R8 goes through `validate_for_run`; R9 drives `run_simulation` end to end; R9b pins the raising checkpoints' handler contract. See the R9 note below — writing it corrected a claim in the PR body. |
| 4 | MINOR | the "modern" component walk `[n.components[c] for c in n.components]` iterates the `Components` **objects**, which are unhashable, so it raised `TypeError` on every call and fell back to the deprecated `iterate_components` — with its DeprecatedWarning, the opposite of what commit `1f2f2bf`'s message claimed. On a PyPSA that removes the fallback, the nested `except` returned `[]`: the safety check would have vanished silently. | yes — `primary RAISES TypeError unhashable type: 'Buses'`; `warnings: ['DeprecatedWarning']` | `list(n.components.values())`; the fallback stays, and when neither works the walk **raises**. R3 asserts no `iterate_components` warning and that a non-network raises. The commit message's claim is withdrawn here. |
| 5 | MINOR | `_bulk` refused non-numeric strings but `float("nan")`, `float("inf")`, `"Infinity"`, `"nan"` all passed `float()` and were **written**; `POST /generators` with `p_max_pu="inf"` wrote `inf` into the static frame. The preflight caught both at solve — the 200-then-refused UX this phase removed for `PUT /timeseries`. | yes — `json.loads('{"a": NaN}')` → `nan`; measured writes | `_bulk` refuses a non-finite value in the five with 422 after `float()`; the five `_pu` fields on `LinkCreate`, `GeneratorCreate`, `StoreCreate` carry `Field(allow_inf_nan=False)` (pydantic 2.13). `discount_rate` NaN still writes (R5b — the rule must not widen). Live: S29.5, S29.6. |
| 6 | MINOR | a duplicated column label in a PUT body made `df[col]` a DataFrame and the row lookup raised `IndexError` — a 500 where the user is owed a 422. Only the JSON PUT can send one. | yes — `IndexError: too many indices for array` | `df.columns.is_unique` guard → 422 naming the duplicates (R7). |
| 7 | NIT | the two raising checkpoints raised a bare `ValueError`; the generic handler dumped a `TRACEBACK:` block and set `condition` to the whole sentence. | yes — `condition = str(exc)` at the generic handler | `ValidationRefused(where, issues)` with its own handler branch: restores the modelling transforms, logs each issue exactly as preflight does (`[VALIDATION] ERROR: Generator 'g' — …`), sets `condition = "validation_failed"`, no traceback (R9b). The first checkpoint's log tag was `error:` and is now `ERROR:` to match preflight. |
| 8 | NIT | a dynamic column named for no asset ("ghost") was reported as one; PyPSA warns and ignores it (measured `optimal`, dispatch unchanged), so the NaN masks nothing and the UI's "View" jump has nowhere to go. | yes | the dynamic branch skips names absent from the static index (R2). |

**Verified clean by the review, no action:** `n_bad` semantics and the
coverage message; the `_bulk` default mapping against the real table on PyPSA
1.3.0 for all six components; preflight silent on ramp NaN, negative Link
`p_min_pu`, StorageUnit rows, empty network, no-snapshot network, empty
dynamic frame, MultiIndex snapshots; `inf` in the five is never legitimate;
the validator's object/bool/empty/string frames; every reject inside a lock
releases through the context manager, and `_user_ts` is untouched on refusal;
every solve entry (sweep, frontier, queue, `/run`, both loops) passes through
`run_simulation`; `_delete_component` and the reapply manufacture no ghost
NaN.

**The R9 note.** Writing the end-to-end witness found that on the
**foreground** network the flat-frame-against-MultiIndex fixture never reaches
the first checkpoint: `_reapply_user_ts_to_network` runs there even with
`_user_ts` empty, and it broadcasts the flat frame over the periods correctly.
The NaN reaches the LP on a network the reapply **skips** — a background
project solve, or an adequacy transient — and R9 is fixtured as the former.
The PR body's sentence "finite at preflight and non-finite by the time the LP
is built" is true of exactly those paths and is qualified there now. And no
legal input reaches the **second** checkpoint with a NaN the first did not
already catch: the cfg-only period promotion in `_apply_modelling_assumptions`
broadcasts correctly (measured `[0.5, 0.6, 0.7, 0.5, 0.6, 0.7]`), so R9b
injects at the check's second call and says so. The second and myopic
checkpoints stay: they are the guard for the vintage-expansion `n.add()` path
that step 7 performs, which nothing has yet shown to manufacture NaN, and a
guard that costs one scan is cheaper than the plan it would miss.

**Tests:** 29 added (R1–R9b, parametrised; R6b was added after the live round below), 50 in the file. Every ★ was
bitten against the named variant and demonstrated RED; the bite log is
recorded below once the round completes.

**Bite log (every ★ of R1–R9b, applied to the shipped files and restored by
saved copy + sha256):**

| bite | variant | result |
|---|---|---|
| B1/R1 | drop the `dyn_names` skip in the static branch | BIT |
| B2/R2 | drop the `static_names` skip in the dynamic branch | BIT |
| B3a/R3 | restore the object-iterating walk | BIT — `iterate_components` DeprecatedWarning emitted |
| B3b/R3 | restore the nested `return hits` | BIT — the non-network yields `[]` instead of raising |
| B4/R4 | drop the `_attribute_default_is_finite` gate | BIT — all three cases read 422 |
| B5/R5 | drop the `isfinite` check after `float(value)` in `_bulk` | BIT — all four literals written |
| B6/R6 | `allow_inf_nan=True` on the five fields | BIT — 12 of 12 cases |
| B7/R7 | drop the `is_unique` guard | BIT — IndexError escapes |
| B8/R8 | delete `out += _check_nonfinite_bounds(n)` from `_check_lopf` | BIT |
| B9/R9 (first form) | delete the first checkpoint | **DID NOT BITE** — the second checkpoint refused the same NaN after the modelling step and reported identically. Recorded, and R9 replaced: it now asserts the refusal is logged as `Validation failed: 1 error(s). Aborting.` with no `after modelling assumptions` line, i.e. before the modelling step, which is what the first checkpoint is for. |
| B9/R9 (second form) | delete the first checkpoint | BIT — refusal reported "after modelling assumptions" |
| B9c/R9 | delete both checkpoints | BIT — the solve proceeds against the masked bound, `('ok', 'optimal')` |
| B9b/R9b | raise a bare `ValueError` at the second checkpoint | BIT — traceback back, condition is the sentence |

The one non-biting bite is a harness lesson worth keeping: a test that a
guard exists is not a test of *which* guard fired when a backstop stands
behind it, and the property the first checkpoint owns is "before modelling",
not "refused".

**Found live, not by the review — a ninth item.** S29.6 on the fixed code read
**500**, not 422: the create schema refused `Infinity` correctly, but pydantic
echoes the offending `input` in its error list and starlette's JSON encoder
raised `Out of range float values are not JSON compliant` while writing the
422 body. The app now owns the `RequestValidationError` handler (`main.py`),
rendering a non-finite input as its repr — the same body FastAPI's default
handler builds otherwise. R6b pins it through the authenticated TestClient;
its first version failed on the *fixed* code too, because httpx encodes
`json=` with `allow_nan=False` and refused to **send** the literal — the app
was never reached. It sends a raw body now, which is what a scripted client
does. Bite (verified): remove the handler — the encoder's ValueError
surfaces. Live: S29 **6/6** on the fix; the live bite (`_bulk` check off,
`allow_inf_nan=True`) flips S29.5 to `200 / null` and S29.6 to `201 /
created`, the pre-fix symptoms, and restore is by hash.

**Seen in the server log, pre-existing, backlog:** `--- Logging error ---
AttributeError: 'BufferedLogQueue' object has no attribute 'put_nowait'`
after every solve — the `QueueHandler` in `run_simulation` enqueues with
`put_nowait`, which `routers/simulation.py`'s `BufferedLogQueue` does not
define. Present in ten server logs from earlier sessions, so not this round's;
harmless to the solve (the logging module swallows it) but noisy. Not fixed
here: out of this phase's scope.
