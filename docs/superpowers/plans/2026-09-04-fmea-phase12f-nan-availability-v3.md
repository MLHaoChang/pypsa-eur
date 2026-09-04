# Phase 12f — a missing `_pu` hour must mean what an absent one means (plan v3)

**Status:** proposed. v1 and v2 were both **rejected**; every finding of both
reviews was reproduced before being accepted, and all five blockers were mine.
v3 keeps v2's diagnosis (§0–§2 there, re-verified by the second review) and
replaces its design entirely.

## 0. The defect, unchanged and re-confirmed

A non-finite hour in a varying `_pu` column does not clamp a bound — linopy
**masks that hour's constraint row out of the problem**, so the asset is
unconstrained for that hour. Measured on PyPSA 1.3.0 / HiGHS:

| network | dispatch |
|---|---|
| `p_max_pu=[0.5,NaN,1.0]`, `p_nom=100`, load 500 | `[50, **500**, 100]` — 5× nameplate |
| `p_min_pu=[0,NaN,0.9]`, dear unit, cheap slack | `[0, **−900**, 90]` — the unit runs as a load |

The code states the false belief that makes this invisible
(`routers/network.py:2705`: *"PyPSA's default-fallback handles those at solve
time"*). It does not. Everything else in this phase follows from that one
sentence being wrong.

## 1. Why v2's fix was rejected — the three blockers, all reproduced

**Filling `0` is wrong for half the columns it would touch, and can make a
solvable network infeasible.** `_pu` is not one kind of number:

| component | attribute | PyPSA default | filling `0` means |
|---|---|---|---|
| Generator / Link | `p_min_pu` | 0.0 | neutral |
| Generator / Link | `p_max_pu` | 1.0 | unavailable |
| **StorageUnit** | **`p_min_pu`** | **−1.0** | **cannot charge** — binding |
| **Store** | **`e_max_pu`** | 1.0 | **energy forced to 0** |
| **Line / Transformer** | **`s_max_pu`** | 1.0 | **branch islanded** |
| **Bus** | **`v_mag_pu`** | 1.0 | **it is an `Output`** |

Measured: a must-run generator (`p_min_pu = 0.5`) with one NaN `p_max_pu` hour
solves `ok/optimal` today and becomes **`warning/infeasible`** under the fill,
because the fill makes `p_min_pu > p_max_pu`. v2's own tests F1b and F1c
specified that behaviour as the acceptance criterion.

**The chosen repair point manufactures the NaN it would then fill.**
`_normalise_dynamic_indexes` reindexes a stale flat frame onto MultiIndex
snapshots to **all-NaN** — the helper's docstring claims PyPSA aligns by level
and it does not — so a *complete, finite* user series becomes all-NaN and the
fill then reads it as "unavailable". Measured end to end: `ok/optimal` today,
`warning/infeasible` under v2. That falsifies v2's "nothing moves for complete
finite series" on its own.

**`_pu` is the wrong selector.** `buses_t.v_mag_pu` is a varying **Output**,
and `_normalise_dynamic_indexes` runs on the AC-PF path
(`ac_pf_service.py:345`) immediately before the result snapshot, so a suffix
rule would zero reported bus voltages and persist them.

## 2. The design: a missing value behaves exactly as an absent one

This is the whole change, and it is deliberately not a new modelling opinion.
PyPSA already defines what an *absent* value means — the component default. A
*present but non-finite* value is the same absence, written down. So:

**[G1] Repair to the attribute's own component default, never to a constant.**
`p_max_pu → 1.0`, `p_min_pu → 0.0` for Generator/Link but **−1.0** for
StorageUnit, `s_max_pu → 1.0`, `e_max_pu → 1.0`, `e_min_pu → 0.0` — read from
`n.components[c].defaults`, not hard-coded. This cannot make anything
infeasible relative to "the value was never supplied", which is what the user
actually gave us, and it restores the semantics `routers/network.py:2705`
already believes are in force.

> This does credit an unknown `p_max_pu` hour at 1.0, and this codebase's rule
> is that **nothing in a derating factor may default to 1.0**. That rule is
> about *crediting an asset in a standard*, and it is not weakened here: G4
> keeps the adequacy surfaces conservative and G3 makes the repair visible.
> The choice is between the LP's documented default and an unbounded row —
> not between 1.0 and something safer.

**[G2] An allow-list, from the metadata, never a name suffix.** The repair
touches exactly the `(component, attribute)` pairs where
`n.components[c].defaults` says `varying == True` **and**
`status == "Input (optional)"`. `Bus.v_mag_pu` is excluded by construction
because it is an `Output`, not by a special case.

**[G3] Non-destructive, and it returns a record.** The repair produces a
repaired frame plus `{(component, attribute, column): non_finite_hours}`. It
does **not** mutate the live network: v2's version did, so `GET /timeseries`
would afterwards return the repaired values where the user had `null`, and the
disclosure would be silent on every later run (a netCDF import does not
restore, `routers/io.py:203`). The record is what preflight and the disclosure
consume — after a destructive fill there is nothing left to name, which is why
v2's F2 and F3 contradicted each other.

**[G4] One helper, called by the solve path *and* the read paths.** The margin
lever (`routers/results.py:4652`, `:4739`) and `/copt` (`:5282`) call
`reserve_margin_facts` / the engines directly on the live network and never
enter `run_simulation`. If only the solve path repairs, the lever's ceiling is
computed on one rule and every LP it then runs on another — the exact "two
implementations would be two standards" hazard `reserve_margin_facts`' own
docstring names. So the repair is a pure helper both sides call, not a step
inside the solve.

**[G5] Repair before the reindex, and fix the reindex separately.** The fill
must run **before** `_normalise_dynamic_indexes`' `if df.index.equals(snap):
continue` short-circuit (`solver_service.py:4985`), or the commonest real case
— a PUT on the exact snapshot index carrying `null` — is skipped entirely. And
a reindex-manufactured NaN is **not** user data: the flat→MultiIndex broadcast
is fixed first (by level-1 timestep match, as `_reapply_user_ts_to_network`
already does at `routers/network.py:2702`), so the repair never sees a NaN it
created.

**[G6] The engines keep their own rule, now on repaired input.** `NaN → 0`
(`copt.py:501`) and `series_is_informative`'s drop (`copt.py:113`) are left
alone. After G1+G4 no non-finite value reaches them, so the two engine rules
become unreachable rather than wrong — pinned by a test, so a future path that
bypasses the helper is caught instead of silently mis-scoring.

## 3. What moves for a user

- **Complete, finite series on a matching index: nothing.** Enumerated, not
  asserted — see G7 below.
- **A network with non-finite hours:** the LP stops producing plans it cannot
  deliver (no more 5× nameplate, no generator running as a load). Numbers move
  toward, not away from, feasibility.
- **A network whose stored frame is flat while snapshots are MultiIndex:** it
  stops silently becoming all-NaN. This is a **fix to a live data-loss path**,
  not a side effect.
- **Persisted margin payloads:** `FINGERPRINT_VERSION` is *not* bumped. v2
  proposed it; the second review showed the bump reports **every** persisted
  payload as `stale_report`, including on the finite networks §3 promises
  nothing for. The narrow case it would protect — a NaN-bearing, not-yet-
  re-solved network keeping a matching v2 fingerprint — is instead covered by
  G3's record, which is what the payload should carry.

## 4. ★ tests — with the fixture each needs, because six have routed around one

Every fixture states whether its frame is **index-matched** or **short**,
because `:4985` skips the matched case, and whether its column is **varying**
or **constant on its finite values**, because `solver_service.py:3576`
classifies on finite values only.

| ★ | claim | fixture | bite |
|---|---|---|---|
| **G1a** | `p_max_pu=[0.5,NaN,1.0]`, `p_nom=100`, load 500 → NaN hour dispatches **100**, not 500 | index-matched | repair to `0` instead of the default |
| **G1b** | must-run `p_min_pu=0.5` + NaN `p_max_pu` stays `ok/optimal` | index-matched | repair to `0` → `infeasible` (the v2 bug, pinned) |
| **G1c** | `StorageUnit.p_min_pu=[-1,NaN,-1]` still charges; objective unchanged | index-matched | repair to `0` → 99× cost move |
| **G1d** | `Line.s_max_pu` and `Store.e_max_pu` NaN hours stay feasible | index-matched | repair to `0` |
| **G2a** | `buses_t.v_mag_pu` is **untouched** after an AC-PF run | post-`n.pf()` | select on the `_pu` suffix |
| **G3a** | the live `generators_t.p_max_pu` still reads `null` at `GET /timeseries` after a solve | via HTTP | mutate in place |
| **G3b** | the record names the asset and the hour count | — | return the frame only |
| **G4a** | the margin lever's ceiling and the LP's derate agree on a NaN-bearing network | — | repair in `run_simulation` only |
| **G5a** | an index-**matched** frame carrying `null` is repaired | index-matched | place the fill after the `equals` short-circuit |
| **G5b** | a flat frame on MultiIndex snapshots broadcasts instead of becoming all-NaN | short/flat | leave the reindex as it is |
| **G6a** | no non-finite value reaches `copt`'s membership walk or `reserve_margin_facts`, asserted by instrumenting **generators_t.p_max_pu specifically** | — | bypass the helper for generators (not for links — that leaves it green) |
| **G7** | the anchor, below | enumerated | fill with `0` instead of the default |

**[G7] The anchor is enumerated, or it is not a gate.** v1 cited an "18
fixtures × 2 modes" harness that does not exist; v2 replaced it with "every
fixture with complete finite series", which is equally unenumerated. G7 names
the set explicitly — the networks built by `tests/golden/fixture.py`,
`tests/test_adequacy_reserve_margin.py`, `tests/test_adequacy_profiled_units.py`
and `tests/test_adequacy_report.py` — and asserts derates, terms and the
`/copt` and `/mc` payloads byte-identical across it. Its bite changes a
**finite**-network number (fill with `0` rather than the default), so an
identity-only variant cannot pass it, which is what killed v2's F7.

## 5. Out of scope, stated

- Rejecting non-finite input at the six write paths (`PUT /timeseries`, four
  uploads, netCDF). `GET /timeseries` renders every non-finite cell as `null`
  (`routers/network.py:3143`), so a rejection needs a companion read-surface
  change; G1 makes the consequence bounded without it. Recorded as the
  follow-on.
- Changing the engines' `NaN → 0` rule or `series_is_informative` (G6).
- The static-CF per-asset flag; the `validate` route's TOCTOU.

## 6. A live area is needed and named

Every entry point in §2 of v2 is HTTP, and nothing in `tests/` drives
HTTP → NaN → solve. This phase adds **S29**: upload a profile containing a
`null` over the API, solve, and assert the served dispatch respects nameplate
and the disclosure names the asset — bitten live by removing the repair.
