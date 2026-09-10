# Phase 12h — a static capacity factor is applied, and "it already includes outages" is a flag the asset carries (plan v4 — v1, v2 and v3 rejected, the design core standing all three times; amended per all three reviews)

**Status:** plan v1, for adversarial review before a line is written.
**Adjudicates:** the fourteenth finding (Phase 12c-pre, plan v2 §1.3): the
engines and the reserve margin disagree about a generator that carries a
STATIC `p_max_pu < 1` and outage data, by two errors of different size in
opposite directions, and the recorded resolution is "a per-asset 'this CF
includes outages' flag — a data-model change".

## 0. The premise, measured

One fixture, 168 h, load 100 MW flat; `nuc` 100 MW, static `p_max_pu = 0.8`,
`q = 0.05` (EFORd, MTTR 100 h); `gas` 25 MW, `q = 0.05`. Three readings of the
same unit, through the shipped code (`fleet_and_residual` →
`screening_analysis`; `reserve_margin_facts`):

| how the unit is read | COPT LOLE | COPT EUE | margin derate |
|---|---|---|---|
| **today**: engines ignore the static CF — the row equals "nameplate 1.0, q = 0.05" to the digit | 8.40 h | 640.5 MWh | 0.76 (= 0.8 × 0.95, both applied) |
| CF is a typed availability: applied as a constant series **and** outages sampled | **16.38 h** | **800.1 MWh** | 0.76 |
| CF already includes outages: applied, rate zeroed | 8.40 h | 168.0 MWh | **0.80** |

So the engines' error is not "25 %" in the abstract: on this fixture it is a
**halved LOLE** (8.4 against 16.38) and a 20 % EUE understatement under the
first reading, while the margin is right under the first reading and 5 %
optimistic under the second. Neither surface can know which reading applies,
because the static column carries both meanings in the wild — 12a's "a flat
0.25 typed on a farm" and PyPSA-Eur's `nuclear_p_max_pu.csv`, a historical
capacity-factor table that already contains forced outages, written to the
static column by `add_electricity.py`. The one fact that makes this
decidable is that **the two readings differ only in whether the outage rate
is applied**, and the rate is resolved at exactly one place:
`occurrence.resolve_outage_params`. Every consumer — both engines, the
reserve margin's `derate`, the net-load window, the worksheet, the
disclosures — reads `q` from that frame (measured above: the margin's
derate moved 0.76 → 0.80 with nothing but `q` changed). A flag that zeroes
the rate there resolves both surfaces at once, by construction.

**Also measured:** a boolean custom column survives the project's netCDF
round trip (`[True, False]`, dtype `bool`); the MC already handles `q ≤ 0`
at two sites (`mc.py:341, :439`) and the COPT's two-state builder is exact at
`q = 0`; the margin reads `avail_static` from the static column already
(`solver_service.py:3489`), so its half needs no change.

## 1. What ships

**H1 — the flag, and the data model it needs.** A custom Generator column
`p_max_pu_includes_outages` (bool, default `False`), declared beside the
three occurrence columns in `occurrence.py`. The v1 review measured that a
plain custom bool column is wrong at **every** write path: the first
`n.add(..., p_max_pu_includes_outages=False)` on a frame lacking the column
creates an `object` column with **NaN** on every other row, and
`bool(nan) is True` — every pre-existing generator would read as flagged; a
netCDF round trip of that column returns **float64** (0/1/NaN); `_bulk` on
the object column takes the string branch and stores `'False'`, which is
truthy; and `_bulk` with `null` on a clean bool column is not a `None` write
today, it is a **500** (`TypeError: Invalid value 'nan' for dtype 'bool'`,
measured for `committable` too). So:

- one reader, `occurrence.flag_is_set(v)` — True for `True`, `np.True_`,
  `1`, `1.0` and the strings `"true"`/`"1"`/`"yes"` case-insensitively (the
  set `_bulk`'s own bool branch accepts); NaN, None, `""`, `"False"` → False
  — is the ONLY way the flag is read (`resolve_outage_params`, preflight,
  payloads);
- one normaliser, `occurrence.normalise_flag_column(n)` — **creates** the
  column (all `False`, dtype `bool`) when absent, else
  `df[col].map(flag_is_set).astype(bool)` — and it runs at the
  **dtype-sensitive boundaries**, not "after every write" (v2 review,
  finding 1, BLOCKER: the solve itself re-poisons the column — the VOLL and
  DSR slack rows are added with `n.add` without the column, the column
  becomes `object`, and the next project save fails with `unsupported dtype
  for netCDF4 variable: bool`; the undo snapshot fails the same way,
  silently). The v3 review pinned the exact trigger: an `object` column
  fails export **only when every value is a bool** — `[True, False, nan]`
  and `[True, False, None]` export fine, `[True, False, True]` raises — and
  that is precisely the post-restore state, because the slack row carried
  the NaN and was then removed. So the dtype normalisation is justified by
  **netCDF export alone**; v1's argument from `bool(nan) is True` no longer
  holds once `flag_is_set` is the only reader, and is withdrawn. The sites:

  * **one export helper.** There are exactly three runtime
    `export_to_netcdf` calls — `routers/projects.py:1532` (project save),
    `routers/io.py:48` (`_export_netcdf_bytes`), `routers/network.py:2186`
    (`_push_undo_snapshot`) — and all three already take
    `PyPSAService.get_netcdf_io_lock()`. They become one
    `PyPSAService.export_network_to_netcdf(n, path)` that normalises first
    (v3 review, finding 11: a helper is the smaller surface than three call
    sites, and a fourth site added later inherits the fix);
  * **the solver's restore callback**, at the end of `restore()` — verified a
    real hook: `_apply_modelling_assumptions` returns `(restore, captured)`,
    both slack `n.add`s register their removal inside it, and
    `run_simulation` calls it through a once-guarded `_guarded_restore` on
    success, `SolveAborted`, `ValidationRefused` and the generic exception
    path. No earlier site substitutes for it;
  * **`_bulk`**, before the unknown-column check and the dtype dispatch, and
    **inside `PyPSAService.get_lock()`** — which is an `RLock`, so the
    prologue, the check, the dispatch and the write take it together and the
    normaliser is not a write racing a foreground solve (v3 review, finding
    8). This is what makes bulk-flagging an import work: without the
    create-if-absent the route refuses `has no column(s)` (v2 finding 8);
  * **`_create_component`/`_update_component`**, which already hold the lock
    (`_create_component` dumps without `exclude_unset`, so
    `n.add(…, p_max_pu_includes_outages=None)` runs there);
  * **the import and network-replacing paths**, which are seven and two, not
    "project load": bundle import, template, background hydrate,
    `load_project`, the io import, undo restore, the snapshots reset —
    plus `PyPSAService.set_network` (re-clustering, the eighth
    network-replacing path). None of these can break a save now that the
    export helper normalises, but each can silently **drop** the flag, which
    is why they normalise too.

  Measured cost on a 300-row frame: 0.17 ms;
- the schema field is `p_max_pu_includes_outages: bool | None = None` with a
  `None → False` validator, for an explicit `null` from a scripted PUT or
  the chat tools (with the normaliser in place `GET` serialises the column
  as `true`/`false`, so the panel's GET → PUT round trip never sends
  `null`); `_merge_partial_update` widens to custom columns, so a PUT that
  omits the field never resets a set flag;
- `_bulk`'s bool branch clears `null` to the column's default for **every**
  bool column — PyPSA's class default where the metadata has one
  (`p_nom_extendable False, committable False, active True, maintainable
  False`; `n.add(committable=None)` gives `False`, measured), `False` for a
  custom column — closing a **reachable 500**: the bulk editor sends `null`
  for a blank cell (`coerce.ts`), and `p_nom_extendable: null` raises
  `TypeError: Invalid value 'nan' for dtype 'bool'` today. No test pins the
  old behaviour.

Generator only: the finding is about generators, and the other
occurrence-bearing classes carry no availability the engines read this way
(their `row.get` returns None → unset; verified).

**H2 — the rule, at the one place.** `resolve_outage_params` returns
`rate = 0.0` for a row whose flag is set **and** whose source is not
`missing` **and** which carries a sub-1 availability (static `p_max_pu <
1 − 1e-9`, or an informative series). It keeps `basis`/`mttr_hours`/`source`
and adds `outages_in_availability: bool`. The third condition is the v1
review's finding 7: zeroing the rate of a unit whose availability is 1 does
not "have no effect", it makes the unit perfectly firm — the maximal effect
— so there the rate is **left alone** and preflight says the flag was
ignored. Every consumer of `q` was enumerated and verified at `q = 0` by the
review: the MC short-circuits its transition and sampling (`mc.py:341, :439`)
and a q = 0 unit never advances its child stream, so other units' CRN paths
are unchanged by a flag flip; `_unit_states` → `[(0, 0), (cap, 1)]` and
`build_copt` skips `p ≤ 0`; `validate_outage_params` accepts 0; the
worksheet's `f_i = 0`; `unpriceable` is NaN-gated so the unit stays credited
at `avail` (derate 0.80 measured); ELCC's `baseline_key` hashes `q`, so a
flag flip invalidates the CRN baseline; the portfolio's `network_fingerprint`
hashes the resolved rate, so a flip yields a correct `stale_report`.

**H2b — a rate-zero unit with a profile leaves the mixture, and nothing
else.** "Nothing downstream changes code" was false (v1 review, finding 2,
MAJOR): `split_fleet` gates on `profile is None` only, so a flagged unit
carrying an informative series stayed in the `2^k` mixture, **burned a
`K_EXACT` slot and displaced a real unit into the netted approximation** —
measured on nine units: LOLE **7.456 h** against the exact **0.972 h**, and
the unit's FMECA row a silent `forced_outage` at 0 events/yr.

v2 proposed netting the unit into the residual inside `fleet_and_residual`.
The v2 review measured what that removes (finding 4): the unit vanished from
`inputs.units`, so from the **portfolio population**, from
**`elcc_candidates`** and from the FMECA rows — while the margin still
carried its row — and `sample_capacity` spawns its child streams **by
position**, so removing a unit shifts every downstream unit's CRN substream.
So the mechanism lives in **`split_fleet`**, not in the walk: the unit
**stays in `units`** (q = 0, profile kept) and `split_fleet` gains a fourth
bucket, `deterministic` — rate-zero units with a profile — which
`_screen_block` nets at their full `a_{i,h}` (exact: one state) and never
counts against `k_exact`. Measured: the prototype equals the `k_exact = 9`
answer to 1e-9, and with the unit flagged the other units' `sample_capacity`
draws are bit-identical (only the flagged unit's move).

Two things the fourth bucket does **not** get for free, both measured by the
v3 review:

- **`attribute_criticality` iterates table + mixed + netted** (`copt.py:798`),
  so a unit in a fourth bucket is in none of them and its FMECA row is
  silently **absent** — v1 finding 2's failure mode again, in a quieter
  form. It gains a `deterministic=()` parameter and emits the ΔEUE-0 row with
  the note (measured: `g0 row present: False` → `True` with the parameter);
- **the 12d per-block merge rebuilds `FleetSplit` from three name sets**
  (`copt.py:1000-1012`), so a deterministic unit disappears from the merged
  split on every multi-period or capacity-series network — and `/copt`'s
  `outages_folded_units` would be empty on exactly the networks 12d exists
  for, while the metrics stay right. `FleetSplit` gains `deterministic_names`
  and the merge carries it.

`fidelity_note` needs no change: deterministic units are in neither `mixed`
nor `netted`, so its counts stay true (verified).

**H3 — the engines apply a static CF, and a constant series the same way.**
12c-pre left the static column unread on purpose, because folding it in
*and* applying `q` double-counts the PyPSA-Eur case. With H2 that case has a
name, so the fold ships: when **no informative series exists**, a static
`p_max_pu ≠ 1` (finite) **scales the unit's capacity** — `capacity_mw × cf`,
and `capacity_series × cf` where 12d gave the unit a per-period series —
with `profile` left `None`. (Applied only in the absence of a series: beside
one it would be read twice, and the portfolio would refuse
`capacity_basis_mismatch` at `_CAP_REL = 1e-9`.)

Scaling rather than a constant profile, because a constant availability
needs no per-hour mixture — and the v1 review measured the cost of the
mixture route on exactly this shape: nine units at a **constant series**
0.8 read LOLE **9.617 h** through the shipped `2^k` route (eight mixed, one
netted) against the exact **11.964 h** (k_exact = 64, and the scaled route to
the digit) — a 24 % understatement, from the cap. So **a constant informative
series is folded the same way** (all cells finite, `max − min ≤ 1e-9`): the
margin already classifies exactly this as `profile_kind == "constant"`. The
fold carries a marker: `CoptUnit.folded_constant: float | None` — the
constant availability the unit was scaled by. **It is data, not a membership
gate.** v3 made it the gate ("member if `profile is not None or
folded_constant is not None`") and the v3 review measured two blockers in
that one line:

- `folded_constant` is set by **both** folds, so a **static-CF** unit
  (`gas_static`, static 0.9, no column at all) entered the portfolio
  population — the very thing `test_B11` exists to forbid, and a direct
  contradiction of H5. Measured: `test_B11` red under a full prototype of
  the v3 design (`{'gas_static', 'wind_for', 'wind_mt'}` against the
  expected `{'hydro_const', 'wind_for', 'wind_mt'}`), and the two halves of
  the population stopped agreeing, because the must-take half still gates on
  the **series** rule;
- the fold rescales `u.capacity_mw`, which is what `Member.capacity_mw`
  reports, while the margin's row stays at `p_nom` — so `portfolio_block`
  refused **`capacity_basis_mismatch`** for every folded member (measured:
  "margin 80 MW vs engines 72 MW; margin 60 MW vs engines 48 MW"), killing
  the whole ELCC-vs-margin comparison on any project with a constant-series
  occurrence unit. The suite missed it only because no block-level fixture
  carries one: latent, not absent. H3 had asserted the opposite.

So v4 keeps the population rule the codebase already has and states in
`portfolio.py`: **the informative-series rule, on both halves.** The
generator half gates on `_informative(u.name)` — the same helper the
must-take half uses, reading the network's `p_max_pu` column — which is True
for a folded constant-series unit (its column is informative) and False for
a static-CF unit (there is no column). Verified with the amendment: members
are `['hydro_const', 'wind_for', 'wind_mt']`, `test_B11` green. And a folded
member reports its **unfolded** capacity — `u.capacity_mw /
u.folded_constant`, and the same for `capacity_by_period` — so it is
compared against the margin on the same basis, while `member_contributions`
keeps reading `_availability_mw`, so the physics is unchanged. Verified with
both amendments: `block status: ok`.

The marker's remaining jobs are that unfolding, and telling the payloads and
disclosures which units were folded.

The blast radius is then exactly what the v3 review measured by running the
**whole adequacy suite** under a prototype of the fold — 3 failed, 630
passed: M1 (the `gas_static` row recomputes on capacity 72, from 80;
`hydro_const` → `profile is None, folded_constant 0.8, capacity 48`;
`gas_ones` unchanged), A8 (the ELCC candidates `nameplate_mw`, 80 → 72), and
B11, which the membership amendment above returns to green. The four
occurrence tests pinning the retired code (lines 348, 376, 395, 413) are
rewritten with H4. Nothing in `test_adequacy_mc*`, `elcc*`,
`reserve_margin*` or the golden fixture moves. A7's continuity bound holds
under the fold at level 0.5 (relative LOLE difference 7.7e-13). Every
project with a constant availability series on an outage-bearing unit sees
its numbers move — upward, the shipped route understated.

**Where the fold goes** (v3 review, finding 15 — load-bearing and unstated
before): on the **occurrence-unit branch only**. `fleet_and_residual`'s
must-take branch already applies the static column itself
(`avail = static × cap_h`, `copt.py:617`); a scaling applied to `cap` before
that branch would **square** it for every must-take farm.

**Constant detection**: all cells finite and `max − min ≤ 1e-9`, which agrees
with the margin's `profile_kind == "constant"` classifier at ±1e-10 and
±1e-8 (measured). They **diverge on a partial-coverage series** such as
`[0.8, 0.8, NaN]`: the fold does not apply (a NaN cell is not constant) and
the unit takes the profile route with NaN as availability 0, as today, while
the margin classifies it constant. Stated rather than reconciled: that
series is refused at solve since 12f, and the engines see it only through
the raw-network routes.

**What "exact" means here, stated honestly** (v1 review, finding 3): the
scaled two-state unit is exact **in mean** — the table's mean shifts by
exactly `cap × cf`, and EUE agrees with the profile route to 0.3 % on the
review's off-grid cases — but a capacity that is not a grid multiple
(`100 × 0.833 = 83.3` on a 1 MW grid) is apportioned across two grid states
by `_unit_states`, so LOLE can cross a threshold state that does not exist
(measured `13.986` against `16.380`/`8.400` at loads 83.5/83.2). That is the
table's existing discretisation, the same one every non-integer nameplate
has always had, not a new class of error — and the pin says so: H3a asserts
the on-grid identity (eight units, series vs scaled, 1e-13 measured) and, off
grid, the exact mean shift and EUE within **5e-3** relative (the v2 review
measured 2.38e-3 and 2.24e-3 on the correct implementation — v1's "0.3 %" had
been copied as 1e-3, a pin the right code would fail), **without** a LOLE
equality. The MC has no such term: `sample_capacity` is bit-identical
between the scaled and the profile routes (seed 7, 64 draws, also at 0.833)
and `mc_adequacy` LOLE/EUE identical.

**H3 elsewhere, verified by the review:** ELCC's `unit_nameplate_mw` is the
scaled capacity, which is the profile route's best hour, and the firm block
dominates hour by hour; attribution's perfect-capacity counterfactual at
`cap × cf` is 12c-pre's mixed-unit `a_{i,h}` counterfactual by the same
argument; the portfolio's membership is profile-gated (`portfolio.py:121`),
so a scaled unit is not a member and no `activity_mismatch` /
`capacity_basis_mismatch` arises; vintage clones copy custom columns, so a
flagged parent's clones carry the flag into the solve-time margin.

**H4 — preflight tells the truth again.** `static_p_max_pu_not_applied` is
retired (its sentence is false after H3). Two codes replace it, both from
the same membership walk so they reach a carrier-default-only import:

| code | fires when | says |
|---|---|---|
| `availability_may_include_outages` (warning) | occurrence unit, `q > 0`, flag unset, static `p_max_pu < 1` and no informative series — the old warning's population minus typed-`q = 0` units (the shipped check had no `q > 0` test; a unit with no outages to sample has nothing to double-count, and now says nothing) | both the CF and the outage rate are applied (nameplate × CF × (1 − q)) by the engines and the margin alike; if the value is a historical capacity factor that already contains forced outages — PyPSA-Eur's nuclear table is one — set `p_max_pu_includes_outages` on the asset so the rate is not applied twice |
| `outages_folded_into_availability` (warning) | flag set on an occurrence unit with a sub-1 availability | N generator(s) are modelled without sampled outages because their availability is declared to include them; the reserve margin credits them at the availability alone. **Variant** `…_ignored` when the flag is set but there is no sub-1 availability (static 1, no informative series), or no outage data at all — the latter walked from `_membership_walk` directly, because `occurrence_units` excludes a `source == "missing"` row (v2 review, finding 10): the flag was ignored and the unit is modelled as before |

`profile_and_outage_modelled` is **split by `profile_kind`** (v3 review,
finding 7): after H3 a *constant* informative series is folded to capacity,
so there is no mixture and no outages sampled on a series, and its shipped
sentence — "outages are sampled on the availability series … and the COPT
mixes the unit exactly per hour over its outage states" — would be false for
exactly those units. A constant-series unit leaves that population and joins
`availability_may_include_outages`, whose population therefore reads "a
sub-1 availability, **static or constant series**, no varying series": its
CF ambiguity is identical to the static case, and under v3 it would never
have been told the flag exists. The varying-series population keeps the
sentence, which is true of it, and loses its false remedy ("remove the outage rate", which a carrier-default
unit cannot do); it names the flag. A flagged asset-typed unit has `q = 0`
and so leaves that population and enters `outages_folded_into_availability`
— one sentence per unit, never two.

**H4b — the payloads say it too** (v1 review, finding 9): `/copt` and `/mc`
carry `static_cf_units` (scaled from the static column) and
`outages_folded_units`; `reserve_margin_facts` no longer lists a rate-zero
unit under `carrier_default` (its derate uses no class average), so
preflight's "derates N assets using carrier class averages" stays true; the
FMECA row of a rate-zero unit carries a `note` (its `source` still reads
`carrier_default` for a flagged library unit — that is where the rate came
from before it was folded, and the note says why it is zero). The golden
fixture carries **no** occurrence units, so H4's population pins use the
occurrence test fixtures, not the golden network.

**H5 — what does not change.** The portfolio population and the net-load
window are decided by the *series* rule (`series_is_informative` on the
column) and a static value creates no series, so neither moves. ELCC prices
the unit at its scaled capacity, which is what its best hour now is. The LP
is untouched: PyPSA has always applied the static `p_max_pu`.

## 2. What moves for a user (stated, not hidden)

Every project with a static `p_max_pu < 1` on an outage-bearing generator
sees its COPT, MC, ELCC and both certifying loops move after this — LOLE
*up* (the unit was credited at nameplate). A PyPSA-Eur import with nuclear
moves from the engines' large error (CF ignored) to the margin's small one
(rate applied twice) **until the flag is set**, and the preflight names the
asset and the flag on every such run. That is the trade the finding
recorded: the engines and the margin now agree, and the remaining question
is answered by the one party who can — the person who knows what the number
is.

## 3. Tests (every ★ with its bite)

- ★ H1a: `resolve_outage_params` returns `rate 0.0` + `outages_in_availability
  True` for a flagged row with asset data and a sub-1 availability;
  unchanged for a flagged row with `source == "missing"`, and unchanged
  (rate kept) for a flagged row whose availability is 1. Bite: ignore the flag.
- ★ H1b — the data model, every path both reviews broke: a first `POST` on
  a frame lacking the column leaves every other row `False` in a `bool`
  column (not NaN in `object`); **a solve with `voll > 0` followed by a
  project save returns 200 and the column reads `bool`** (the v2 BLOCKER;
  bite: drop the restore-callback and export-site normalisers — the save is
  a 500); a project reloaded from netCDF reads `bool`; `_bulk` on an import
  whose frame has no column sets the flag (bite: drop the create-if-absent);
  a `_bulk` write of `False`/`"false"`/`"True"`/`null` reads back the bool
  **and the column is still `bool` dtype afterwards** (the bite is on the
  dtype, not on the value: v3 fixed v2's finding twice over — normalise
  before dispatch *and* case-insensitive strings — so normalising after
  dispatch now stores the string `'True'` and `flag_is_set` reads it as
  **set**, and an object-of-strings column exports fine; only the dtype
  assertion separates the two); a project whose **on-disk** column is
  `float64` (an older-build save, or an external netCDF) loads as `bool` —
  the reload assertion needs that fixture, because a clean `bool` column
  round-trips as `bool` with no load-side normaliser at all; an explicit
  `"p_max_pu_includes_outages": null` PUT is 200 and reads `False` (bite:
  drop the validator — 422); and `committable: null`, `p_nom_extendable:
  null` and **`active: null`** through `_bulk` read `False`, `False` and
  **`True`** where today each is a 500 — `active` pinned explicitly because
  the bulk editor sends `null` for a blank cell and PyPSA's default there is
  `True`, so blanking that cell re-activates a deactivated asset. That is a
  decision, not a side effect.
- ★ H2a: the margin's derate reads 0.80 with the flag and 0.76 without, on
  the §0 fixture, through `reserve_margin_facts`. Bite: as H1a.
- ★ H2b: nine units with informative **varying** series (the
  `[0.05, 0.15, 0.35, 0.45]` tile — the v2 review found that a constant
  series is folded by H3 first, so the v2 fixture could not bite) with `g0`
  flagged, **at load 300**, read the exact **98.379 h** against the shipped
  **96.828 h**. v3 kept v1's numbers (0.972 against 7.456) after changing the
  fixture out from under them: the v3 review measured that those belong to
  the *constant* fixture v3 forbids, and that the varying tile is fully
  **saturated** at v1's load 600 (168.0 h either way — no bite at all), the
  bite existing only in a load band and running the other way (the shipped
  route *under*states there). The load is part of the pin. Also asserted:
  the split has no netted unit; `g0` stays in `units`, in the portfolio
  population and in `elcc_candidates`; it keeps its FMECA row (ΔEUE 0,
  noted); it appears in the merged split's `deterministic_names` on a
  **two-period** fixture; and the other units' CRN substreams are unchanged
  (per-unit draws bit-identical with and without the flag). Bites: leave a
  rate-zero profiled unit in the mixture; net it out of `units` instead (the
  portfolio loses it); drop `deterministic=()` from `attribute_criticality`
  (the row vanishes); drop `deterministic_names` from the merge (empty on
  the two-period fixture).
- ★ H3a: static `0.8` ≡ constant series `0.8` ≡ scaled capacity: COPT metrics
  to 1e-9 on grid (eight units) and `mc_adequacy` LOLE/EUE identical under one
  seed; off grid (`cf = 0.833`) the table mean shifts by exactly `cap × cf`
  and EUE agrees within 5e-3 relative, with no LOLE equality asserted. Bite:
  drop the capacity scaling — static reads the nameplate row (8.40 h).
- ★ H3b: the §0 table, all three rows, hand values pinned (`16.38`, `800.1`,
  `168.0`). Bite: as H3a.
- ★ H3c: 12d interaction — a static CF on a unit with a per-period capacity
  series scales the series, not only the scalar. Bite: scale the scalar only.
- ★ H3d: nine static-CF units and nine constant-series units both stay
  table-only (`mixed == netted == []`) and read **11.964 h**. Bite: fold the
  constant series as a profile — 9.617 h.
- ★ H4a–H4c: one test per code through `validate_for_run` (incl. the
  `_ignored` variant and the retired code absent); the asset-typed flagged
  unit gets one sentence, not two; a **constant-series** unit with a typed
  rate is named by `availability_may_include_outages` and **not** by
  `profile_and_outage_modelled`, while a varying-series unit is named by the
  latter alone. Bites: fire the old code; drop the
  sub-1 condition.
- ★ H4d: `/copt` and `/mc` payloads carry `static_cf_units` /
  `outages_folded_units`; `reserve_margin_facts["carrier_default"]` omits a
  rate-zero unit — on a **carrier-default (library-rate) fixture**, since on
  the S31 fixture both units carry typed rates and the list is empty either
  way (v3 review, finding 13). Bites: drop each.
- ★ H6: `coupling.snapshot_hash` hashes `float(u.q)`. It does not today (it
  hashes name, capacity, profile, capacity series, storage and residual),
  and v2's "the hash changes on a flag flip" was verified against v2's
  design, where the unit left `units`; under v4 it stays, so only `q` moves
  and the hash does **not** change — while the MC is measurably not
  bit-identical across the flip. That breaks the hash's own documented
  contract ("equal hash ⇒ bit-identical MC"), which both certifying loops
  rely on for plateau reuse. One line; the test flips the flag and asserts
  the hash moves. Bite: drop the line. (`baseline_key` already hashes `q`
  and is unaffected.)
- ★ H3e: the portfolio population is unchanged by the fold — `test_B11`
  stays green, `hydro_const` is a member through the **series** rule and
  `gas_static` is not a member. Bites: gate membership on the
  `folded_constant` marker (v3's own rule — `gas_static` enters and B11 is
  red); gate it on `profile is not None` alone (`hydro_const` is evicted).
- ★ H3f: `portfolio_block` reads `ok` on a fixture whose member carries a
  folded constant series, because the member reports its **unfolded**
  capacity. Bite: report `u.capacity_mw` — `capacity_basis_mismatch`, with
  the margin's 60 MW against the engines' 48 MW.
- Rewritten pins, listed so none is a surprise (the v2 review ran the whole
  adequacy suite under a prototype of the fold: exactly these fail):
  `test_adequacy_profiled_units.py` M1/A4′ (`gas_static` hash on capacity
  72; `hydro_const` → `profile is None, folded_constant 0.8, capacity 48`;
  `gas_ones` unchanged) and A8 (ELCC candidates `nameplate_mw` 80 → 72); the
  **four** `test_adequacy_occurrence.py` tests that pin the retired code
  (lines 348, 376, 395, 413).
- Frontend: `IncludesOutagesInput` rendered on the Generator card only
  (`OutageInputs` is shared by five cards and stays as it is); the key in
  `toFS(gen, [...])` so it survives the remove+add save cycle; the payload
  assigns `p_max_pu_includes_outages: form.… === 'true'` explicitly beside
  `committable`, never through the `...current` spread; `Generator` in
  `types.ts`; `adequacy.p_max_pu_includes_outages` in `propertyDocs.ts`. Two
  new vitest files (no existing test snapshots the outage block), bitten.
- Docs: `copt.py` module docstring (lines 33–35) and `_occurrence_profile`'s
  "static column deliberately NOT read"; design spec lines ~391–395; MC spec
  ~340–343; a "superseded by 12h" note on 12c-pre §1.3; `QA_E2E_PLAN.md`
  S31.

## 4. Live — S31

`GET /results/reserve_margin` serves only the **persisted solve-time
stash** (204 otherwise; v1 review, finding 4), so S31 sets
`solver_config.reserve_margin = 0.1` and solves — S23's pattern — before
reading a derate. And the §0 fixture cannot be that solve's network: with
`gas` at 25 MW the derated firm capacity is 99.75 MW against a 110 MW
requirement and preflight refuses `reserve_margin_unreachable` (v2 review,
finding 7, measured over HTTP). The S31 fixture is therefore §0 with **`gas`
at 50 MW, marginal cost 20** (measured: the solve is `optimal` and the row
reads `('nuc', 'ALL', 0.76, 'asset')`; `required 110.0` against
`firm_fixed 123.5`, so no `reserve_margin_unreachable`). Its COPT hand
values were derived on **that** fixture by the v3 review and are pinned by a
unit test beside H3b, so the live rows compare against numbers the suite
owns: **441.0 MWh** today (the nameplate row), **600.6** with the CF and the
rate applied, **168.0** with the flag. §0's 640.5 is the *gas-25* value and
is not reused here. `/results/copt` needs no solve
(`metrics.eue_mwh`, voll-independent); `POST /api/network/snapshots` accepts
the 168 h range; `outage_rate_basis: "EFORd"` is accepted; the API-built
fixture reproduces today's nameplate row over HTTP (`eue_mwh 640.5`,
`lole_hours 8.4` with `gas` at 25 MW).

| id | check |
|---|---|
| S31.1 | the S31 fixture over the API; preflight names `nuc` under `availability_may_include_outages` and the retired code is absent |
| S31.2 | `/results/copt` EUE reads **600.6 MWh** (before 12h: **441.0**, the nameplate row) |
| S31.3 | `PATCH /api/network/_bulk` `{component_class, names, updates: {p_max_pu_includes_outages: true}}` **on a frame whose column the API POST never created** (the fixture is built with the flag omitted, so the normaliser's create-if-absent is what makes this a 200); preflight now carries `outages_folded_into_availability` and not the first code; `GET /generators` reads the flag back `true` |
| S31.4 | `/results/copt` EUE reads **168.0 MWh**; after a solve under `reserve_margin = 0.1`, `/results/reserve_margin`'s row for `nuc` reads derate **0.80**, and the project **saves** (200) after that solve |
| S31.5 | clearing the flag through `_bulk` (`null`) reads back `false` (not a 500), and a re-solve's margin row reads **0.76** again |

Bitten live by dropping the capacity scaling (S31.2 reads the nameplate row),
by ignoring the flag in `resolve_outage_params` (S31.4 reads S31.2's value /
0.76), and by dropping the export-site normaliser (S31.4's save is a 500).

## 5. Out of scope, recorded

- A per-**series** "includes outages" for the dynamic column is covered by
  the same flag (H2 zeroes the rate whatever carries the availability, and
  H2b takes the unit out of the mixture), so nothing is deferred there.
- Inferring the flag from a carrier name or an import source (e.g. "nuclear
  from PyPSA-Eur") — not built: a guess in the data model is the thing this
  program keeps being wrong with.
- StorageUnit/Link/Line occurrence rows have no availability the engines
  read this way; the flag is not offered there.

---

## v1 REVIEW — REJECT, the design core standing (twelve findings; all verified)

The reviewer reproduced §0 to the digit and both design decisions — zero the
rate at `resolve_outage_params`, scale capacity rather than attach a
profile — and rejected v1 on four things around them. Every finding below
was reproduced against the code before it was recorded; the amendments are
incorporated above.

| # | severity | finding | verified | amendment |
|---|---|---|---|---|
| 1 | BLOCKER | a plain custom bool column is wrong at every write path: the first `n.add` on a frame lacking it leaves NaN (`object`) on every other row and `bool(nan) is True`, so every existing generator reads as flagged; netCDF returns float64; `_bulk` stores the string `'False'` (truthy); `_bulk null` on a clean bool column is a **500** today (`committable` too), not a `None` write; a strict `bool` schema field would 422 the panel's GET→PUT of a `null`. | yes — reproduced (`probe4_6.py`, the TypeError) | H1 as amended: one reader `flag_is_set`, normalise to `bool` after every write, `bool \| None` with a None→False validator, `_bulk null` → default for every bool column; H1b covers each path. |
| 2 | MAJOR | a flagged unit with an informative series stayed in the `2^k` mixture, burned a `K_EXACT` slot and displaced a real unit into netting — nine constant-series units with `g0` flagged: **7.456 h** against the exact **0.972 h**, and a silent FMECA row. §5's "covered by the same flag" was false. | yes — reproduced | H2b: a rate-zero profiled unit is netted deterministically at its full `a_{i,h}`, never in the mixture or the cap; typed `q = 0` fixed the same way; `outages_folded_units` on payloads; ★ H2b on the nine-unit fixture. |
| 3 | MAJOR | "exact as a two-state unit on the grid" holds only at grid multiples; `100 × 0.833` is apportioned across two grid states and LOLE crosses a state that does not exist (`13.986` vs `16.380`/`8.400`), EUE within 0.3 %. H3a's 1e-9 pin passed only because `0.8 × 100 = 80`. | yes — reproduced | H3 states mean-exactness honestly; H3a pins the on-grid identity and the off-grid mean shift + EUE bound, no LOLE equality. |
| 4 | MAJOR | S31.4/S31.5 unimplementable: `/results/reserve_margin` serves the persisted solve-time stash only (204 otherwise) and S31 never solved or set the margin. | yes — route read | S31 sets `reserve_margin = 0.1` and solves before each derate read (S23's pattern). |
| 5 | MAJOR | the test inventory missed the pin H3 breaks: `test_M1_membership_pin_and_A4…` hashes `gas_static` with `capacity_mw = 80, profile None`; four (not three) occurrence tests pin the retired code; the ELCC candidates test asserts `nameplate_mw == 80 # static NOT applied`. | yes — read | listed in §3 as rewritten pins. |
| 6 | MAJOR (recommendation) | the constant-series asymmetry is real and **the shipped route is the wrong one**: nine constant-series units read 9.617 h through the mixture (cap) against the exact 11.964 h — 24 % under; eight units agree to 1e-13. | yes — reproduced | a constant informative series is folded as capacity scaling too; cost stated (M1 `hydro_const`, A7 note); numbers move upward for such projects. |
| 7 | MINOR | zeroing the rate of a unit whose availability is 1 is the maximal effect, not "no effect". | yes | H2's third condition; the rate is left and `…_ignored` is emitted; three codes → two plus a variant. |
| 8 | MINOR | "the old warning's exact population" was not exact: the shipped check had no `q > 0` test. | yes — read | stated in H4. |
| 9 | MINOR | disclosure gaps: no payload names a static-scaled unit; a rate-zero carrier-default unit still lands in the margin's `carrier_default` list, making preflight's "derates N assets using class averages" false. | yes — read | H4b. |
| 10 | MINOR | omissions: copt.py docstring, design spec, MC spec, 12c-pre §1.3 note, QA plan; no frontend reference to the old code (verified); no vitest snapshots the outage block. | yes | §3 docs list. |
| 11 | MINOR | frontend mechanics unspecified (shared `OutageInputs`, `toFS` key, explicit payload assignment, types, docs). | yes | §3 frontend list. |
| 12 | NIT | "four occurrence columns" → three; H3's garbled sentence about when the static value applies (load-bearing: beside a series the portfolio would refuse `capacity_basis_mismatch`). | yes | fixed in H3. |

**Verified sound by the review:** §0 to the digit; MC `sample_capacity`
bit-identical between scaled and profile routes (seed 7, 64 draws, also at
0.833), and a q = 0 unit never advances its child stream; COPT at grid
multiples identical to 1e-13; ELCC bracket = scaled capacity = best hour,
dominance hour by hour; `baseline_key` hashes `q`; attribution's
counterfactual right by 12c-pre's own argument; portfolio membership
profile-gated, fingerprint hashes the resolved rate; every q = 0 consumer
(MC, `_unit_states`, `build_copt`, `validate_outage_params`, worksheet,
`unpriceable`, report, stress, sweep); vintage clones copy custom columns;
Storage/Link/Line frames read the flag as unset; `/results/copt` needs no
solve and its EUE is voll-independent.

---

## v2 REVIEW — REJECT, the design core standing again (thirteen findings; all verified)

The reviewer ran the two named test files (45/45 on the branch), prototyped
H2b and H3 in scratch copies, ran the **whole adequacy suite** under the H3
prototype, and drove the S31 fixture over HTTP with a real solve. Every
finding below was reproduced against the code before it was recorded; the
amendments are incorporated above.

| # | severity | finding | verified | amendment |
|---|---|---|---|---|
| 1 | BLOCKER | "normalise after every write" missed the **solve**: the VOLL and DSR slack rows are added with `n.add` without the column, the column becomes `object`, and the next project save fails `unsupported dtype for netCDF4 variable: bool` (the undo snapshot fails silently the same way). None of v2's four sites runs between the solve's restore and the save. | yes — reproduced end to end, real solve, `POST /api/projects → 500` | H1: one normaliser at the dtype-sensitive boundaries — every `export_to_netcdf` site, the solver's restore callback, project load/import, the top of `_bulk`, the create/update sites; H1b's solve-then-save assertion. |
| 2 | MAJOR | `_bulk` normalised *after* the write, so on a post-solve `object` column the string branch stored `'True'`, and `flag_is_set`'s exact `"true"` read it as unset — S31.3 could silently not set the flag. | yes | normalise before dispatch; strings case-insensitive (`"true"`/`"1"`/`"yes"`). |
| 3 | MAJOR | ★ H2b could not bite: its nine constant-series units are folded by H3 first, so no rate-zero *profiled* unit exists and the mixture is empty either way (0.972 h with or without H2b). | yes — reproduced | H2b's fixture uses **varying** series. |
| 4 | MAJOR | H2b as designed (net the unit out of `units`) removed it from the portfolio population, from `elcc_candidates` and from the FMECA rows while the margin kept its row, and shifted every downstream unit's CRN substream (children spawned by position). | yes — reproduced | H2b lives in `split_fleet` as a fourth bucket `deterministic`; the unit stays in `units`. |
| 5 | MAJOR | H3's fold moved the portfolio population: the whole adequacy suite under a prototype of the fold fails exactly M1, A8 and **`test_B11`** (`hydro_const` evicted, the two halves disagree); v2 said the population does not move and did not list B11. | yes — the suite log | `CoptUnit.folded_constant` marker; membership keeps folded constants; ★ H3e; the blast radius listed as measured. |
| 6 | MAJOR | ★ H3a's off-grid EUE bound of 1e-3 relative fails on the correct implementation (2.38e-3 / 2.24e-3 measured; v1's "0.3 %" was copied as 1e-3). | yes — reproduced | bound 5e-3, mean-shift equality kept. |
| 7 | MAJOR | S31.4/5 still unimplementable on the §0 fixture: derated firm 99.75 MW against a 110 MW requirement → `reserve_margin_unreachable`, `validation_failed`, `/results/reserve_margin` 204. | yes — over HTTP | S31 fixture: `gas` 50 MW at marginal cost 20 (measured `optimal`, row 0.76); hand values re-derived on it. |
| 8 | MAJOR | `_bulk` refused the flag on an import — `Generator has no column(s): p_max_pu_includes_outages` — so bulk-flagging the motivating PyPSA-Eur nuclear case was impossible; S31.3 only worked because the API POST had created the column. | yes — reproduced | the normaliser creates the column when absent, at load and at the top of `_bulk`; S31.3 builds the fixture without the flag. |
| 9 | MINOR | H1b's "drop the validator" bite did not bite: with the normaliser, GET never serialises `null`. | yes | H1b sends an explicit `null` PUT. |
| 10 | MINOR | the `_ignored` variant could not fire for "flag, no outage data" from `occurrence_units` (a `source == "missing"` row is must-take and excluded); the golden fixture has **no** occurrence units and cannot pin H4. | yes | that case walks `_membership_walk`; H4 pins use the occurrence fixtures. |
| 11 | MINOR | constant detection agrees with the margin at ±1e-10/±1e-8 but diverges on a partial-coverage series (`[0.8, 0.8, NaN]`). | yes | stated in H3. |
| 12 | NIT | `carrier_default` skip is at the one right place (preflight reads `facts["carrier_default"]`); the FMECA `source` still says `carrier_default` for a flagged library unit. | yes | said in H4b. |
| 13 | NIT | normaliser cost 0.17 ms / 300 rows; bool defaults enumerated; no custom bool column besides the flag; `_bulk null` on a bool is a reachable 500 (the bulk editor sends `null` for a blank cell) that no test pins; `outages_in_availability` breaks no positional consumer (seven consumers, all named). | yes | folded into H1. |

**Verified sound by the review:** §0 through the prototype and over HTTP;
A7 continuity under the fold (7.7e-13); A3′ untouched; `test_a_flat_profile_is_not_disclosed`
untouched; the H3 blast radius is exactly M1/A8/B11 in the engine tests;
table-mean exactness off grid; `snapshot_hash` and `baseline_key` change on
a flip; the margin treats a rate-zero varying unit consistently; clustering,
`remove + add`, `n.add` with `np.bool_`, a clean netCDF/CSV round trip,
undo/load, vintage clones, the chat tools' partial merge and
`_merge_partial_update` all preserve a clean `bool` column; H4's one
sentence per flagged unit on every fixture probed; `/results/copt` and the
168 h snapshot POST over HTTP.

---

## v3 REVIEW — REJECT, the design core standing a third time (sixteen findings; all verified)

The reviewer prototyped the whole v3 design — the fold, the `folded_constant`
marker and the `deterministic` bucket — ran the **entire adequacy suite**
under it (3 failed, 630 passed), re-measured §0 and the S31 fixture end to
end, and reproduced the v2 blocker with the normaliser at the site v3
specifies. The rejection is not of the design: **two of v3's own ★ pins are
red on the design v3 specifies**, and a third is unwritable as pinned.

| # | severity | finding | verified | amendment |
|---|---|---|---|---|
| 1 | BLOCKER | `folded_constant` as the membership gate admits **static-CF** units to the portfolio population — `test_B11` red under the full prototype (`gas_static` in, `hydro_const` expected) — contradicting ★H3e, H5 and H3-elsewhere at once, and breaking the "informative-series rule on **both** halves" invariant, since the must-take half still gates on the series. | yes — suite run | the marker is **data, not a gate**; the generator half gates on `_informative(u.name)`, the same helper the must-take half uses. Verified: members `['hydro_const','wind_for','wind_mt']`, B11 green. |
| 2 | BLOCKER | the fold rescales `u.capacity_mw`, which is what `Member.capacity_mw` reports, while the margin's row stays at `p_nom` — so `portfolio_block` refuses **`capacity_basis_mismatch`** for every folded member ("margin 80 MW vs engines 72 MW; margin 60 MW vs engines 48 MW"), killing the ELCC-vs-margin comparison on any project with a constant-series occurrence unit. Fixing finding 1 does not fix it. Latent, not absent: no block-level fixture carries one. | yes — reproduced | a folded member reports its **unfolded** capacity (`capacity_mw / folded_constant`, and `capacity_by_period` likewise); `member_contributions` keeps `_availability_mw`, so the physics is unchanged. Verified: `ok`. ★H3f. |
| 3 | MAJOR | ★H2b kept v1's numbers (0.972 against 7.456) after v2 changed its fixture: those belong to the *constant* fixture v3 forbids, and the varying tile is **fully saturated** at v1's load (168.0 h either way — no bite), the bite existing only in a load band and with the opposite sign. | yes — load sweep | re-derived on the fixture actually used: load 300, **98.379** exact against **96.828** shipped; the load is part of the pin. |
| 4 | MAJOR | the `deterministic` bucket silently **deletes the unit's FMECA row**: `attribute_criticality` iterates table + mixed + netted only. v3 asserts the row "stays". | yes — `row present: False` → `True` | `deterministic=()` parameter, ΔEUE-0 row with the note; stated in H2b. |
| 5 | MAJOR | the 12d per-block merge rebuilds `FleetSplit` from three name sets, so a deterministic unit **vanishes from the merged split** on every multi-period network — `/copt`'s `outages_folded_units` empty on exactly the networks 12d exists for. | yes | `deterministic_names` on `FleetSplit` and in the merge; ★H2b asserts it on a two-period fixture. |
| 6 | MAJOR | "`snapshot_hash` changes on a flag flip (verified)" is false **under v4's own amendment**: v2 verified it against v2's design, where the unit left `units`; here it stays, so only `q` moves and `snapshot_hash` does not hash `q` — while the MC is measurably not bit-identical across the flip. That breaks the hash's documented contract, which both loops use for plateau reuse. | yes — reproduced | hash `float(u.q)`; ★H6. |
| 7 | MAJOR | H4 left `profile_and_outage_modelled` saying "outages are sampled on the availability series … the COPT mixes the unit exactly per hour", which H3 makes **false for a constant series**; and `availability_may_include_outages` excluded series-carrying units, so the constant-series case — whose CF ambiguity is identical to the static one — would never be told the flag exists. | yes | split by `profile_kind`: constant series joins `availability_may_include_outages`, varying keeps the sentence. |
| 8 | MINOR | the `_bulk` normaliser as placed turns a read-only prologue into a **write outside the mutation lock**, concurrent with a solve that adds and removes rows. | yes — mechanics measured | `get_lock()` is an `RLock`: prologue, unknown-column check, dispatch and write take it together. |
| 9 | MINOR | ★H1b's "a project reloaded from netCDF reads `bool`" cannot bite — a clean `bool` column round-trips with no load-side normaliser at all. | yes | bite it with a fixture whose on-disk column is `float64`. |
| 10 | MINOR | ★H1b's "`'True'` read as unset" bite is stale: v3 fixed v2's finding twice (normalise-before-dispatch *and* case-insensitive strings), so normalising after dispatch now reads the string as **set**, and an object-of-strings column exports fine. | yes | restate the bite on the **dtype**. |
| 11 | MINOR | the site list, made concrete: exactly three runtime `export_to_netcdf` calls (all already under the io lock) — a single `PyPSAService.export_network_to_netcdf` helper is the smaller surface; and the live **import** sites are seven, not "project load", with `set_network` (re-clustering) and the snapshots reset absent from v3's list. | yes | H1's site list rewritten around the helper; the import and network-replacing paths enumerated. |
| 12 | MINOR | `_bulk` bool `null → class default` silently **re-activates rows**: `active`'s default is `True` and the bulk editor sends `null` for a blank cell. Today it is a 500, so nothing happens. | yes | pin `active: null → True` explicitly in ★H1b, as a decision. |
| 13 | MINOR | ★H4d's `carrier_default` assertion is not exercisable on the S31 fixture — both units carry typed rates, so the list is empty with and without the flag. | yes | H4d uses a carrier-default fixture. |
| 14 | NIT | H1 still argued from `bool(nan) is True`, which no longer holds once `flag_is_set` is the only reader. | yes | withdrawn; the normalisation is justified by netCDF export alone, whose exact trigger is now pinned (an `object` column fails **only when every value is a bool** — the post-restore state). |
| 15 | NIT | where the fold goes is load-bearing and was unstated: the must-take branch already applies the static column, so a scaling before that branch would **square** it for every must-take farm. | yes | H3 says: the occurrence-unit branch only. |
| 16 | NIT | S31's hand values, derived on the gas-50 fixture: **441.0** today, **600.6** with CF and rate, **168.0** with the flag; §4's 640.5 is the gas-25 value. | yes | §4 corrected. |

**Verified sound by the review (re-measured, not taken on trust):** §0 and
the S31 fixture to the digit; the solver's restore callback is a real hook on
every exit path, and the v2 blocker reproduces and is fixed by the normaliser
there (`SAVE after solve: FAIL` → `OK, dtype bool`); the `_bulk` placement
mechanics; the `deterministic` bucket is exact (equal to `k_exact = 9` at
1e-9); per-unit CRN identity under a flip; `fidelity_note` still counts
correctly; `reserve_margin_facts` does not double-apply (derates
byte-identical with and without the fold); `unit_nameplate_mw` for a folded
unit and the 12d branch that makes ★H3c's bite bite; H3a/H3b/H3d's pins
(on-grid exact; off-grid EUE 2.377e-3 and 2.237e-3, inside 5e-3; H3d 11.9635
table-only against the shipped 9.6171); H4's one code per unit and the
reachable `_ignored`; `_merge_partial_update` widens to custom columns;
`coerce.ts` sends `null` for a blank cell and its bool-string set matches
`_bulk`'s; `CoptUnit` extends safely (frozen, trailing default); and the
engine-suite blast radius is exactly M1, A8 and B11.
