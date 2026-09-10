# Phase 12h — a static capacity factor is applied, and "it already includes outages" is a flag the asset carries (plan v2 — v1 rejected, the design core standing; amended per its review)

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

- one reader, `occurrence.flag_is_set(v)` — True only for `True`, `np.True_`,
  `1`, `1.0`, `"true"`; NaN, None, `""`, `"False"` → False — is the ONLY way
  the flag is read (`resolve_outage_params`, preflight, payloads);
- the column is **normalised to `bool` dtype after every write** —
  `_create_component`, `_update_component`, `_bulk`, project load —
  `df[col] = df[col].map(flag_is_set).astype(bool)`, so `_bulk`'s bool
  branch is actually reached and netCDF stays `bool`;
- the schema field is `p_max_pu_includes_outages: bool | None = None` with a
  `None → False` validator, because `GET /generators` serialises the NaN of a
  mixed column as `null` and the properties panel spreads `...current` into
  its PUT — a strict `bool` would 422 every unflagged row;
- `_bulk`'s bool branch clears `null` to the column's default for **every**
  bool column — PyPSA's class default where the metadata has one
  (`n.add(committable=None)` gives `False`, measured), `False` for a custom
  column — closing the 500 for `committable`/`p_nom_extendable`/`cyclic_state_of_charge`
  as well, pinned (`committable: null → False`).

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

**H2b — a rate-zero unit with a profile leaves the mixture.** "Nothing
downstream changes code" was false (v1 review, finding 2, MAJOR):
`split_fleet` gates on `profile is None` only, so a flagged unit carrying an
informative series stayed in the `2^k` mixture, **burned a `K_EXACT` slot and
displaced a real unit into the netted approximation** — measured on nine
constant-series units with `g0` flagged: LOLE **7.456 h** against the exact
**0.972 h**, and `g0`'s FMECA row a silent `forced_outage` at 0 events/yr.
So in `fleet_and_residual` a unit with `rate == 0` (flag set, or typed) and
a profile is **netted deterministically at its full `a_{i,h}`** into the
residual — exact, it has one state — and never enters `mixed`/`netted`/the
cap; the same applies to a typed `q = 0` today, which was displacing units
the same way. Payloads list such units under `outages_folded_units`.

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
cost is stated: M1's `hydro_const` assertion (`profile == full(0.8)`) becomes
`profile is None, capacity 48`; the `gas_static` row of the membership pin
recomputes on capacity 72 (from 80); the ELCC candidates test that asserts
`nameplate_mw == 80  # static NOT applied` becomes 72; A7's continuity bound
still holds at level 0.5 (on grid); A3′/A11′ use varying profiles and are
untouched. Every project with a constant availability series on an
outage-bearing unit sees its numbers move — upward, the shipped route
understated.

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
grid, the exact mean shift and EUE within 1e-3 relative, **without** a LOLE
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
| `outages_folded_into_availability` (warning) | flag set on an occurrence unit with a sub-1 availability | N generator(s) are modelled without sampled outages because their availability is declared to include them; the reserve margin credits them at the availability alone. **Variant** `…_ignored` when the flag is set but there is no rate to fold (no outage data) or no sub-1 availability: the flag was ignored and the unit is modelled as before |

`profile_and_outage_modelled` keeps its population (asset-typed, `q > 0`)
and loses its false remedy ("remove the outage rate", which a carrier-default
unit cannot do); it names the flag. A flagged asset-typed unit has `q = 0`
and so leaves that population and enters `outages_folded_into_availability`
— one sentence per unit, never two.

**H4b — the payloads say it too** (v1 review, finding 9): `/copt` and `/mc`
carry `static_cf_units` (scaled from the static column) and
`outages_folded_units`; `reserve_margin_facts` no longer lists a rate-zero
unit under `carrier_default` (its derate uses no class average), so
preflight's "derates N assets using carrier class averages" stays true; the
FMECA row of a rate-zero unit carries a `note`.

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
- ★ H1b — the data model, every path the review broke: a first `POST` on a
  frame lacking the column leaves every other row `False` in a `bool`
  column (not NaN in `object`); a project reloaded from netCDF reads `bool`;
  a `_bulk` write of `False`/`"false"`/`null` reads back `False` (not
  `'False'`); a GET → PUT round trip of an unflagged row is 200; and
  `committable: null` through `_bulk` reads `False` where today it is a 500.
  Bites: drop the normaliser; drop the `None → False` validator.
- ★ H2a: the margin's derate reads 0.80 with the flag and 0.76 without, on
  the §0 fixture, through `reserve_margin_facts`. Bite: as H1a.
- ★ H2b: nine constant-series units with `g0` flagged read the exact
  **0.972 h**, not 7.456; the split has no netted unit; `g0` is in
  `outages_folded_units`. Bite: leave a rate-zero profiled unit in the
  mixture.
- ★ H3a: static `0.8` ≡ constant series `0.8` ≡ scaled capacity: COPT metrics
  to 1e-9 on grid (eight units) and `mc_adequacy` LOLE/EUE identical under one
  seed; off grid (`cf = 0.833`) the table mean shifts by exactly `cap × cf`
  and EUE agrees within 1e-3 relative, with no LOLE equality asserted. Bite:
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
  unit gets one sentence, not two. Bites: fire the old code; drop the
  sub-1 condition.
- ★ H4d: `/copt` and `/mc` payloads carry `static_cf_units` /
  `outages_folded_units`; `reserve_margin_facts["carrier_default"]` omits a
  rate-zero unit. Bites: drop each.
- Rewritten pins, listed so none is a surprise: `test_adequacy_profiled_units.py`
  M1/A4′ (`gas_static` hash on capacity 72; `hydro_const` → `profile is None,
  capacity 48`; `gas_ones` unchanged) and the ELCC candidates `nameplate_mw`
  (80 → 72); the **four** `test_adequacy_occurrence.py` tests that pin the
  retired code (lines 348, 376, 395, 413).
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
reading a derate.

| id | check |
|---|---|
| S31.1 | the §0 fixture over the API (168 h snapshots); preflight names `nuc` under `availability_may_include_outages` and the retired code is absent |
| S31.2 | `/results/copt` EUE reads **800.1 MWh** (before 12h: 640.5, the nameplate row) |
| S31.3 | `PATCH /api/network/_bulk` `{component_class, names, updates: {p_max_pu_includes_outages: true}}`; preflight now carries `outages_folded_into_availability` and not the first code; `GET /generators` reads the flag back `true` |
| S31.4 | `/results/copt` EUE reads **168.0 MWh**; after a solve under `reserve_margin = 0.1`, `/results/reserve_margin`'s row for `nuc` reads derate **0.80** |
| S31.5 | clearing the flag through `_bulk` (`null`) reads back `false` (not a 500), and a re-solve's margin row reads **0.76** again |

Bitten live by dropping the capacity scaling (S31.2 reads 640.5) and by
ignoring the flag in `resolve_outage_params` (S31.4 reads 800.1 / 0.76).

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
