# Phase 12f — a non-finite LP bound is refused, not repaired (plan v5)

**Status:** proposed. v1–v4 were all rejected — **fourteen blockers, every one
reproduced, every one mine**. The diagnosis survived all four reviews
unchanged; every rejection was of a *repair*. v5 stops repairing.

## 0. The defect (unchanged through four reviews)

A non-finite value in an LP bound does not clamp anything — linopy **masks
that constraint row out of the problem**. Measured, PyPSA 1.3.0 / HiGHS:

| what carries the non-finite value | dispatch of a 100 MW unit |
|---|---|
| dynamic `p_max_pu = [0.5, NaN, 1.0]`, load 500 | `[50, **500**, 100]` |
| dynamic `p_min_pu = [0, NaN, 0.9]`, cheap slack | `[0, **−900**, 90]` — runs as a load |
| **static** `p_max_pu = NaN`, no dynamic column at all | `[**500, 500, 500**]` — every hour |
| dynamic `ramp_limit_up = [0.1, NaN, 0.1, 0.1]` | `[0, **100, 100, 100**]` vs `[0, 10, 20, 30]` — the ramp limit is gone for the **whole horizon** |

`routers/network.py:2705` records the belief that hides it — *"PyPSA's
default-fallback handles those at solve time"*. It does not.

## 1. Why v5 refuses instead of repairing

Every rejected version failed on the same question — **what value replaces the
missing one?** — and each answered it wrongly in a new way:

| version | answer | why it failed |
|---|---|---|
| v1 | refuse via `unpriceable` | that code is `_err`; it stops networks solving |
| v2 | `0` | binding for `StorageUnit.p_min_pu` (default −1), `Store.e_max_pu`, `Line.s_max_pu`; makes a must-run unit **infeasible** |
| v3 | the class default | the table has no asset dimension — credits **2.5×** an asset's own static bound; and via the read paths it moved the margin derate **+40.5 %**, *more optimistic than the truth* |
| v4 | the asset's static value | correct for the dynamic frame, but the **static frame carries the same defect** and is the repair's own source, so a NaN static makes it a no-op |

Refusing needs no answer. It cannot make a network infeasible, cannot loosen
any standard, cannot credit anything at 1.0, and it fixes the static frame,
the dynamic frame and the ramp limits with one rule.

## 2. The one place a value *is* well defined — and the route already knows it

`PATCH /network/_bulk` already implements "null means unset" per column
(`routers/network.py:2020-2029`): `*_max` and `lifetime` → `inf`,
`e_sum_min` → `−inf`, *"likewise their PyPSA default, so the resulting network
is valid"*. It then falls through to `float("nan")` for everything else —
including every bound, where NaN is **not** a valid default and is exactly
what deletes the constraint row.

**[J1] Extend that existing rule to the bounds.** At the **static** level
"unset" genuinely means the class default, because the static column *is* the
per-asset value and its absence is what `n.components[c].defaults` describes.
`p_max_pu → 1.0`, `p_min_pu → 0.0` (Generator/Link/Process) and **−1.0**
(StorageUnit), `s_max_pu → 1.0`, `e_max_pu → 1.0`, `e_min_pu → 0.0`,
`ramp_limit_* →` the class default. NaN is never written for a bound.

> This is **not** v3's error. v3 used the class default to fill a *dynamic*
> hour on an asset that had its own static value — hence the 2.5×
> over-credit. Here the value being set *is* the static one, and the class
> default is definitionally what "no value" means for it. The frontend's
> deliberate null ("the user typing nothing applies an intentional unset",
> `BottomPanel.tsx:120`) keeps working, and starts meaning something valid.

## 3. Everywhere else: refuse

**[J2] A time series with a non-finite cell is corrupt, not unset.** The six
dynamic write paths — `PUT /timeseries` (`routers/network.py:3153`),
`POST /timeseries/upload` (`:3211`), the three `upload_profile` routes
(`:3704`, `:3984`, `:4097`) and the generic CSV/Excel upload (`:3014`) —
refuse with **422**, naming the column and the first offending row labels.
One shared validator, called by all six, because a guard repeated six times is
a guard the seventh route forgets — this codebase's own lesson from the
study-swap guard.

**[J3] A netCDF import is not refused.** It is someone else's file and
rejecting it outright would be hostile. It lands, and J4 names it.

**[J4] Preflight names what is already there — as an ERROR.** Any non-finite
LP bound in the network, static or dynamic, is named with asset, attribute and
hour count. Error, not warning: the LP will otherwise build a plan the network
cannot deliver, and this codebase already reserves ERROR for exactly that
("no plan built from your candidate set reaches this margin"). This is the
safety net for imports, for projects written before this phase, and for any
seventh write path.

**[J5] Nothing in the solve path changes.** No repair in
`_normalise_dynamic_indexes`, no change to preflight's frame, no change to the
adequacy engines, `/copt`, the margin lever, or `series_is_informative`. The
entire surface that produced fourteen blockers is not touched. In particular
`tests/test_adequacy_profiled_units.py:145` — which pins `prof[2] == 0.0` and
passes today — is unaffected, because the engines' rule is unchanged.

## 4. What moves for a user, stated plainly

- **A finite network: nothing.** No solve-path code changes at all.
- **Writing a non-finite series: 422** instead of a silent corruption.
- **Clearing a bound in the bulk toolbar:** now sets the PyPSA default instead
  of NaN, so the network stays valid. This is a fix to that affordance.
- **An already-corrupt network** (netCDF import, or written before this phase)
  **stops solving** and is told exactly which asset and attribute. It solved
  before — and produced a plan with a 100 MW unit generating 500 MW. Blocking
  it is the point, and it is why J4 is an error.
- **`GET /timeseries` → `PUT` round trip on such a network now fails**, because
  GET renders non-finite as `null` (`routers/network.py:3143`). Accepted: the
  round trip was preserving corruption. Named in the 422 so the user knows.

## 5. ★ tests

| ★ | claim | path | bite |
|---|---|---|---|
| **J1a** | `PATCH /_bulk` with `p_max_pu: null` writes **1.0**, and the network still solves bounded | HTTP | keep the `float("nan")` fallthrough → 500 MW |
| **J1b** | `StorageUnit.p_min_pu: null` writes **−1.0**, not 0.0 or NaN | HTTP | use one constant for every bound |
| **J1c** | a non-bound column's `null` still means missing (the `*_max`→`inf` rule is untouched) | HTTP | apply J1 to every column |
| **J2a** | `PUT /timeseries` with a `null` cell → **422** naming the column and row | HTTP | drop the validator |
| **J2b** | the same for `Infinity` | HTTP | check `isnan` only |
| **J2c** | each of the six write paths refuses — parametrised over all six | HTTP | guard `PUT` only; the other five stay green |
| **J2d** | a finite series still writes | HTTP | refuse everything |
| **J3a** | a netCDF carrying a NaN bound imports 200 | HTTP | refuse the import |
| **J4a** | preflight errors on a NaN **static** bound, naming the asset | in-process | check the dynamic frame only |
| **J4b** | preflight errors on a NaN **dynamic** bound, naming the hours | in-process | check the static frame only |
| **J4c** | preflight errors on a NaN `ramp_limit_up`, which v4 wrongly excluded | in-process | keep v4's bound list |
| **J4d** | silent on a finite network | in-process | error unconditionally |
| **J5a** | the solve path is untouched: `test_a_NaN_hour_is_availability_zero_at_attachment` and the `/copt` payload byte-identical | `_m1_network` | apply any of v2–v4's repairs |

**J5a is the regression that four rejected designs would each have broken**,
kept as a named test so none of them can return by accident.

## 6. Out of scope, stated

- The flat→MultiIndex broadcast bug (`routers/network.py:2725`), which
  silently NaNs a stale frame on a multi-period network. A separate defect;
  J4 turns its consequence from a silent mis-solve into a named error, which
  is the most this phase should claim.
- Any repair of an existing non-finite value. J4 tells the user; correcting
  the data is theirs.
- The static-CF per-asset flag; the `validate` route's TOCTOU.

## 7. Live area

**S29**, with a numbered assertion table like S27/S28:

| id | check |
|---|---|
| S29.1 | `PUT /timeseries` with one `null` → 422, body names the column |
| S29.2 | `PATCH /_bulk` with `p_max_pu: null` → 200, then `GET` reads `1.0` |
| S29.3 | a network carrying a NaN bound is refused at solve, and the failure card names the asset |
| S29.4 | the same network, corrected, solves and the 100 MW unit dispatches **≤ 100** |

Bitten live by removing the validator (S29.1) and by restoring the
`float("nan")` fallthrough (S29.2).
