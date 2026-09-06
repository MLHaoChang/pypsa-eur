# Phase 12f — a non-finite availability hour deletes an LP constraint (plan v2)

**Status:** proposed. v1 was **rejected**; every finding was reproduced before
being accepted. v1's §0 measured the LP wrongly and its §2 design reused a
refusal that aborts the solve. What follows replaces both, and the phase is
now about a bigger and simpler defect than v1 described.

## 0. What v1 got wrong, and the finding that replaces it

v1 said the LP treats a NaN `p_max_pu` as `1.0` and built its argument on a
derate of `0.4750`. **That number does not exist.** linopy does not clamp the
bound — it *masks the constraint row out of the problem*:

```
Constraint `Generator-fix-p-upper` — 1 masked entries:
[00:00, g]: +1 Generator-p ≤ 50.0
[01:00, g]: None            ← no row at all
[02:00, g]: +1 Generator-p ≤ 100.0
```

Raise the load and the "nameplate" reading collapses. Measured, PyPSA 1.3.0 /
HiGHS:

| network | result |
|---|---|
| `p_max_pu=[0.5,NaN,1.0]`, `p_nom=100`, load **500** | `[50, **500**, 100]` — **5× nameplate** |
| `p_min_pu=[0,NaN,0.9]`, dear unit, cheap slack | `[0, **−900**, 90]` — **the generator runs as a load** |

So the headline is not a reporting disagreement. **A non-finite hour in a
`_pu` column silently deletes that hour's constraint, and the LP then builds a
plan that is physically impossible.** Every downstream disagreement below is a
symptom of that, and v1's "ten times worse" arithmetic — distances measured
against the fabricated `0.4750` — is withdrawn entirely.

One consequence v1 had backwards: against an **unbounded** LP, `NaN → 0` is
the *conservative* reading, not the outlying one. v1 rejected it with a
measurement that was not measuring anything.

The false belief is written down in the code, which is a better citation than
any fixture — `routers/network.py:2705`: *"reindex (fills missing snapshots
with NaN; **PyPSA's default-fallback handles those at solve time**)"*. It does
not.

## 1. Four rules, not three — and the worst case is the one v1 never priced

| consumer | a non-finite hour is |
|---|---|
| **the LP** (`Generator-fix-p-upper`, masked) | **unbounded** |
| the engines, when the column still counts as informative (`copt.py:501`) | **0** |
| the engines, when it does **not** (`series_is_informative`, `copt.py:113`) | the profile is **dropped** → **nameplate** |
| the margin's derate, gross and net (`solver_service.py:3545`, `report.py:264`) | **skipped** from the mean |

`series_is_informative` returns False when the finite values are all 1 **or
when there are none**, so the fourth rule is not a corner: it is exactly the
total-non-coverage case. Measured:

| column | margin derate | engines |
|---|---|---|
| all-NaN | **0.0000** | profile dropped → **nameplate** |
| `[1.0, NaN, 1.0, NaN]` | 0.5000 | profile dropped → **nameplate** |
| `[0.9, NaN, 0.9, NaN]` | 0.4500 | `[0.9, 0, 0.9, 0]` |

The headline fixture is therefore the **all-NaN** one — margin `0.0`, engines
**nameplate**, LP **unbounded**, on data the user never supplied — not v1's
`[0.9, NaN, 0.9, NaN]`, which is the mildest configuration of the four.

v1's table also mis-cited the netting. `solver_service.py:3576-3583` classifies
varying-vs-constant on the **finite** values only, so `[0.9,NaN,0.9,NaN]` is
`constant`, `nettable=False`, and `report.py:238`'s `fillna(0.0)` never runs on
it. A netting claim needs a genuinely varying column — `[0.9,NaN,0.5,NaN]`
(`varying`, `nettable=True`, derate `0.35`).

## 2. How it gets in — six write paths, and the read surface emits it back

`PUT /timeseries` does no validation (`routers/network.py:3161-3165`), but it
is **not** the boundary. None of these check finiteness either:

| entry point | file:line |
|---|---|
| `POST /timeseries/upload` (CSV; an empty cell is NaN) | `routers/network.py:3211` |
| `POST /loads/upload_profile` | `routers/network.py:3704` |
| `POST /generators/upload_profile` | `routers/network.py:3984` |
| `POST /links/upload_profile` | `routers/network.py:4097` |
| CSV/Excel generic upload | `routers/network.py:3014` |
| netCDF import | `routers/io.py:206` |

And `GET /timeseries` renders every non-finite cell as `null`
(`routers/network.py:3143`). **So rejecting `null` at write would make the
API's own GET output un-PUT-able** — v1's F1 breaks the round trip. It also
cannot distinguish `Inf` from `NaN`, because GET renders both as `null`.

Plus `reindex`: every consumer reindexes to the snapshots or a window, so a
profile that does not cover them is NaN for the rest, with no write involved
at all.

## 3. The design: repair once, at the choke point, and say so

**Not `unpriceable`.** v1 proposed reusing it. It is `_err`
(`validation_service.py:1627`) → `has_errors` → `run_simulation` returns
`("error", "validation_failed")` (`solver_service.py:821-826`), and the
margin-lever route 422s on that code (`routers/results.py:4665`). Reusing it
would turn networks that solve today into networks that refuse to — while v1's
own F2 said "a warning, not a block", eight lines earlier. v1 contradicted
itself; this plan does not propose a block at all.

**[F1] One repair, at one point.** `_normalise_dynamic_indexes`
(`solver_service.py:4923`) already reindexes every dynamic frame for the solve
and never fills. Non-finite `_pu` hours are filled there, with the rule stated
in one place: **a `_pu` hour we have no value for is `0` — the asset is
unavailable that hour.** Chosen because it is what the engines and the netting
already do, because it is the conservative reading against an unbounded LP,
and because it is the only one of the four that cannot produce a plan the
network cannot deliver.

**[F2] Preflight must see the same view.** `validate_for_run` runs at
`solver_service.py:812`, *before* the normalisation at `:925`, so the derate
that preflight blocks on and the derate the LP is held to would otherwise be
computed on different frames — the exact "two implementations of the derating
chain would be two standards" hazard `reserve_margin_facts`' own docstring
names. Preflight reads the repaired frame.

**[F3] Disclose, loudly, per asset.** A repair changes a number the user did
not ask to change, so a **warning**-severity issue names every asset whose
`_pu` column carried non-finite hours inside the horizon, with the count. Not
an error: the data is theirs and the repair is stated.

**[F4] `series_is_informative` is downstream of the repair.** After F1 an
all-NaN column is all-zero, which *is* informative and means "unavailable" —
so the fourth rule disappears rather than needing its own handling. This must
be asserted, not assumed.

**[F5] The margin's skipna means become unreachable on the solve path** and
are left alone rather than rewritten: with no non-finite hour reaching them
there is nothing to skip. A test pins that they are unreachable, so a future
frame that bypasses F1 is caught rather than silently mis-priced.

**[F6] `FINGERPRINT_VERSION` → `v3`** (`portfolio.py:54`). A margin payload
persisted before this phase carries old-rule derates on a network whose inputs
have not changed, so its v2 fingerprint still matches and `portfolio_block`
would compare it as current. 12d set the precedent for exactly this
(`portfolio.py:161`).

## 4. What moves for a user, stated honestly

- **A network with complete, finite series: nothing.** That is every fixture
  in the suite and every network the QA plan builds — the S21/S23/S26 PUT
  fixtures all set snapshots to match their index exactly.
- **A network with non-finite or short series: numbers move, in the safe
  direction.** A unit that was silently unbounded in the LP becomes bounded;
  one the engines credited at nameplate (the non-informative case) becomes
  unavailable in those hours. A plan that met its margin may no longer, and
  **that is the defect being fixed** — it met it against hours the LP was free
  to over-produce in.
- The **total** non-coverage case is the largest move: `0.0` derate and
  nameplate engines today, `0` availability everywhere after.

## 5. ★ tests, each with the variant it must fail against

Fixture discipline first, because five tests in the previous phase asserted
through a path their fixture never took:

- Netting claims use `[0.9, NaN, 0.5, NaN]` (`varying`, `nettable=True`), never
  `[0.9, NaN, 0.9, NaN]`, which is classified `constant`.
- Engine-drop claims use an all-NaN or all-ones-finite column, the only ones
  `series_is_informative` rejects.

| ★ | claim | bite |
|---|---|---|
| **F1a** | `p_max_pu=[0.5,NaN,1.0]`, `p_nom=100`, load 500 → the NaN hour dispatches **≤ 100**, not 500 | remove the fill in `_normalise_dynamic_indexes` |
| **F1b** | the same for `p_min_pu`: no negative dispatch | fill `p_max_pu` only |
| **F1c** | links and storage too | fill generators only |
| **F2a** | preflight's derate equals the post-normalisation derate on a NaN-bearing network | move `validate_for_run` back after normalisation |
| **F3a** | the warning names the asset and the hour count; **silent** on a finite network | drop the horizon restriction — it then fires on every network |
| **F4a** | an all-NaN column is informative after the repair, and the engines model it at **0**, not nameplate | run `series_is_informative` before the repair |
| **F5a** | no non-finite value reaches `reserve_margin_facts` on the solve path (asserted by instrumenting the frame, not by re-deriving the mean) | bypass the repair for one component |
| **F6a** | a v2-fingerprinted payload is reported as old-version, not as current | leave `FINGERPRINT_VERSION` at v2 |
| **F7 (anchor)** | derates, terms, `/copt` and `/mc` payloads byte-identical on every fixture with complete finite series | apply the repair unconditionally to all frames, finite or not |

**F7 is built in this phase, as a committed test.** v1 called an "18 fixtures ×
2 modes byte-identity anchor" *the* gate; no such harness exists — that phrase
is a reviewer's ad-hoc measurement recorded in the 12d plan
(`…phase12d-engine-activity-v1.md:541`), and `tests/golden/fixture.py` is one
network. Naming a gate that does not exist is worse than naming none.

## 6. Out of scope, stated

- Rejecting non-finite input at the six write paths. GET emits `null` for
  non-finite cells, so a rejection needs a companion change to the read
  surface and a stated repair path for existing projects; the repair in F1
  makes the consequence safe without that surface change. Recorded as the
  follow-on.
- `PUT /timeseries`' plain-`DatetimeIndex`-versus-MultiIndex defect. F1 makes
  its consequence bounded rather than unbounded, which is this phase's job.
- The static-CF per-asset flag; the `validate` route's TOCTOU.
