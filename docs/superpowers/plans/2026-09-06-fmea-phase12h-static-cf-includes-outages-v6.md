# Phase 12h — a static capacity factor is applied, and "it already includes outages" is a flag the asset carries (plan v6 — scope cut to the finding it adjudicates; v5 accepted with seven changes, applied)

**Status:** plan v5, for review before a line is written.
**Adjudicates:** the fourteenth finding (Phase 12c-pre, plan v2 §1.3): the
engines and the reserve margin disagree about a generator carrying a STATIC
`p_max_pu < 1` **and** outage data, by two errors of different size in
opposite directions, and the recorded resolution is "a per-asset 'this CF
includes outages' flag — a data-model change".

**v1–v4 were rejected and v5 accepted with changes** (five adversarial
reviews, 61 findings, five blockers; the full records are the appendices of
`…-v1.md` … `…-v4.md`, kept). The design core — zero the rate at
`resolve_outage_params`; scale capacity rather than attach a profile —
stood every time. What kept failing was one thing v2 bolted on, and **v5
cuts it out**: see §5.

## 0. The premise, measured

One fixture, 168 h, load 100 MW flat; `nuc` 100 MW, static `p_max_pu = 0.8`,
`q = 0.05` (EFORd, MTTR 100 h); `gas` 25 MW, `q = 0.05`. Three readings of
the same unit, through the shipped code:

| how the unit is read | COPT LOLE | COPT EUE | margin derate |
|---|---|---|---|
| **today**: the engines ignore the static CF — the row equals "nameplate 1.0, q = 0.05" to the digit | 8.40 h | 640.5 MWh | 0.76 (= 0.8 × 0.95, both applied) |
| CF is a typed availability: applied **and** outages sampled | **16.38 h** | **800.1 MWh** | 0.76 |
| CF already includes outages: applied, rate zeroed | 8.40 h | 168.0 MWh | **0.80** |

The engines' error on this fixture is a **halved LOLE** and a 20 % EUE
understatement under the first reading; the margin is right there and 5 %
optimistic under the second. Neither surface can know which reading applies,
because the static column carries both meanings in the wild — 12a's "a flat
0.25 typed on a farm", and PyPSA-Eur's `nuclear_p_max_pu.csv`, a historical
capacity-factor table that already contains forced outages, written to the
static column by `add_electricity.py`.

The fact that makes this decidable: **the two readings differ only in whether
the outage rate is applied**, and the rate is resolved at exactly one place,
`occurrence.resolve_outage_params`. Every consumer — both engines, the
margin's `derate`, the net-load window, the worksheet, the disclosures —
reads `q` from that frame (measured: the margin's derate moved 0.76 → 0.80
with nothing but `q` changed). A flag that zeroes the rate there resolves
both surfaces at once, by construction.

**Also measured:** a boolean column survives the project's netCDF round trip
**only while it stays `bool` dtype**; the MC already handles `q ≤ 0` at two
sites and the COPT's two-state builder is exact at `q = 0`; the margin reads
`avail_static` from the static column already, so its half needs no change.

## 1. What ships

**H1 — the flag, and the data model it needs.** A custom Generator column
`p_max_pu_includes_outages` (bool, default `False`), declared beside the
three occurrence columns in `occurrence.py`. A plain custom bool column is
wrong at every write path (v1–v3 reviews): the first `n.add` on a frame
lacking the column creates an `object` column, and **the solve itself does
that** — the VOLL and DSR slack rows are added without the column and then
removed, leaving `object` of pure bools, which netCDF refuses
(`unsupported dtype for netCDF4 variable: bool`), so the next project save
is a 500 and the undo snapshot fails silently. The exact trigger is pinned:
an `object` column fails export **only when every value is a bool** —
`[True, False, nan]` and `[True, False, None]` export fine. So:

- one reader, `occurrence.flag_is_set(v)` — True for `True`, `np.True_`,
  `1`, `1.0` and the strings `"true"`/`"1"`/`"yes"` case-insensitively (the
  set `_bulk`'s own bool branch accepts); NaN, None, `""`, `"False"` → False.
  It is the **only** way the flag is read;
- one normaliser, `occurrence.normalise_flag_column(n)` — **creates** the
  column (all `False`, `bool`) when absent, else
  `df[col].map(flag_is_set).astype(bool)` — at the **dtype-sensitive
  boundaries**:
  * **one export helper**, `PyPSAService.export_network_to_netcdf(n, path)`,
    replacing the three runtime `export_to_netcdf` calls (project save, the
    io export, `_push_undo_snapshot`) — all three are `(network, path)`
    shaped, and a fourth site added later inherits the fix. **The helper
    does not take `get_netcdf_io_lock()`**: that lock is **not reentrant**
    (measured), and the three callers already hold it, so a helper that took
    it again would hang the save while holding the mutation lock — the app
    would wedge rather than error;
  * **the solver's restore callback**, at the end of `restore()` — a real
    hook, verified: `_apply_modelling_assumptions` returns
    `(restore, captured)`, both slack `n.add`s register their removal inside
    it, and `run_simulation` calls it through a once-guarded
    `_guarded_restore` on success, `SolveAborted`, `ValidationRefused` and
    the generic exception path. No earlier site substitutes for it;
  * **`_bulk`**, before the unknown-column check and the dtype dispatch and
    **inside `PyPSAService.get_lock()`** — an `RLock`, so prologue, check,
    dispatch and write take it together and the normaliser is not a write
    racing a solve. This is also what makes bulk-flagging an import work:
    without the create-if-absent the route refuses `has no column(s)`;
  * **`_create_component`/`_update_component`**, which already hold the lock;
  * **the seven import paths and two network-replacing paths** — bundle
    import, template, background hydrate, `load_project`, the io import,
    undo restore, the snapshots reset, plus `PyPSAService.set_network`
    (re-clustering). None can break a save once the export helper
    normalises, but each can silently **drop** the flag.

  Measured cost on a 300-row frame: 0.17 ms;
- the schema field is `p_max_pu_includes_outages: bool | None = None` with a
  `None → False` validator, for an explicit `null` from a scripted PUT or the
  chat tools; `_merge_partial_update` widens to custom columns, so a PUT that
  omits the field never resets a set flag;
- `_bulk`'s bool branch clears `null` to the column's class default, **read
  from PyPSA's own metadata** (the `finite_default_inputs` pattern
  generalised to `type == "boolean"`), falling back to `False` for a custom
  column — closing a **reachable 500** (the bulk editor sends `null` for a
  blank cell). Two of those defaults are `True`, not one: `active` on every
  class, and **`Link.cyclic_delay`**, which is bulk-editable and clears to
  `True` (v5 review, finding 4 — enumerating the defaults by hand had missed
  it, and an implementer following that list would have silently flipped
  every selected link). **`active` is refused 422** instead, naming
  `true`/`false` **and saying why**: its default is `True`, so clearing it
  would *activate* every selected asset, behind a confirm toast that reads
  "Set active = (unset) on 200 generator(s)?". The 422 is the shape `_bulk`
  already uses for a value it could write but refuses on downstream
  semantics (12g's non-finite refusal); 400 is its wrong-type answer.

Generator only: the finding is about generators, and the other
occurrence-bearing classes carry no availability the engines read this way.

**H2 — the rule, at the one place.** `resolve_outage_params` returns
`rate = 0.0` for a row whose flag is set **and** whose source is not
`missing` **and** which carries a sub-1 availability (static
`p_max_pu < 1 − 1e-9`, or an informative series). It keeps
`basis`/`mttr_hours`/`source` and adds `outages_in_availability: bool`. The
third condition matters: zeroing the rate of a unit whose availability is 1
does not "have no effect", it makes the unit perfectly firm — so there the
rate is left alone and preflight says the flag was ignored. (One consequence
to state: this makes what is today a pure function of the component frame
read `n.generators_t.p_max_pu`. The series read is Generator-only and cheap.)

Every consumer of `q` was enumerated and verified at `q = 0`: the MC
short-circuits its transition and sampling and a q = 0 unit never advances
its child stream; `_unit_states` → `[(0, 0), (cap, 1)]` and `build_copt`
skips `p ≤ 0`; `validate_outage_params` accepts 0; the worksheet's `f_i = 0`;
`unpriceable` is NaN-gated so the unit stays credited at `avail`; ELCC's
`baseline_key` hashes `q`, so a flip invalidates the CRN baseline; the
portfolio's `network_fingerprint` hashes the resolved rate, so a flip yields
a correct `stale_report`.

**H2b — a rate-zero unit with a profile leaves the mixture, and nothing
else.** `split_fleet` gates on `profile is None` only, so a flagged unit
carrying an informative **varying** series stays in the `2^k` mixture,
**burns a `K_EXACT` slot and displaces a real unit into the netted
approximation** — measured on nine units: **96.828 h** against the exact
**98.379 h** at load 300, and the unit's FMECA row a silent `forced_outage`
at 0 events/yr. (This is needed by the flag alone: H2 zeroes the rate of a
flagged unit whatever carries its availability, so a flagged varying-series
unit has `q = 0` and keeps its profile. Verified under the static-only fold.)

The mechanism lives in **`split_fleet`**, not in the walk — v2 proposed
netting the unit out of `units` and the v2 review measured that this removes
it from the portfolio population, from `elcc_candidates` and from the FMECA
rows while the margin keeps its row, and shifts every downstream unit's CRN
substream (children are spawned **by position**). So the unit **stays in
`units`** (q = 0, profile kept) and `split_fleet` gains a fourth bucket,
`deterministic` — rate-zero units with a profile — which `_screen_block`
nets at their full `a_{i,h}` (exact: one state) and never counts against
`k_exact`. Measured: equal to the `k_exact = 9` answer to 1e-9, and the other
units' `sample_capacity` draws bit-identical.

Two things the bucket does not get for free:

- **`attribute_criticality` iterates table + mixed + netted**, so a unit in a
  fourth bucket has **no FMECA row**. It gains a `deterministic=()`
  parameter and emits the ΔEUE-0 row with a `note`. Verified: the row sorts
  last under both `(-ΔEUE, name)` and the worksheet's
  `(-criticality, mode_id)`, so no ordering pin breaks, and it renders;
- **the 12d per-block merge rebuilds `FleetSplit` from name sets**, so a
  deterministic unit vanishes from the merged split on multi-period
  networks. The merge rebuilds `deterministic` from its name set exactly as
  it does the other three — **one field, not a second `deterministic_names`**
  (the v4 review found that a merge-only field leaves the payload empty on
  every single-block network, which is the ordinary case).

`fidelity_note` needs no change: deterministic units are in neither `mixed`
nor `netted`, so its counts stay true.

**H3 — the engines apply a static CF.** 12c-pre left the static column
unread on purpose, because folding it in *and* applying `q` double-counts
the PyPSA-Eur case. With H2 that case has a name, so the fold ships: when
the unit has **no `p_max_pu` column at all**, a static `p_max_pu ≠ 1`
(finite) **scales the unit's capacity** — `capacity_mw × cf`, and `capacity_series × cf` where
12d gave the unit a per-period series — with `profile` left `None` and
`folded_constant = cf` recorded as payload data. The gate is the **presence of the column**, not
`series_is_informative` (v5 review, finding 1): PyPSA lets any column
supersede the static cell — measured, a static `0.8` beside an **all-ones**
column dispatches at `1.0` — and `reserve_margin_facts` reads the column
too, so folding the static there would invent a 20 % derate that neither the
LP nor the margin applies, re-creating this phase's own defect in a new place
and on the side where the engines credit *less* than the plan can deliver.
An all-ones column is exactly the shape two shipped fixtures carry. Applied,
then, only when there is no column, and **on the occurrence-unit branch
only**: `fleet_and_residual`'s must-take branch already applies the
static column itself, so a scaling before that branch would **square** it for
every must-take farm.

Scaling rather than a constant profile, because a constant availability
needs no per-hour mixture and costs no `K_EXACT` slot.

**What "exact" means here, stated honestly:** the scaled two-state unit is
exact **in mean** — the table's mean shifts by exactly `cap × cf`, and EUE
agrees with the profile route to 2.4e-3 relative on the reviewers' off-grid
cases — but a capacity that is not a grid multiple (`100 × 0.833 = 83.3` on a
1 MW grid) is apportioned across two grid states, so LOLE can cross a
threshold state that does not exist (measured `13.986` against
`16.380`/`8.400`). That is the table's existing discretisation, the same one
every non-integer nameplate has always had. The MC has no such term:
`sample_capacity` is bit-identical between the scaled and profile routes and
`mc_adequacy` LOLE/EUE identical.

**Verified elsewhere:** ELCC's `unit_nameplate_mw` is the scaled capacity,
which is the profile route's best hour, and the firm block dominates hour by
hour; attribution's perfect-capacity counterfactual at `cap × cf` is
12c-pre's own counterfactual by the same argument; **the portfolio is
untouched** — a static-CF unit has no `p_max_pu` column, so neither the
shipped `profile is not None` gate nor the population's series rule admits
it, before the fold or after; `reserve_margin_facts` never reads `CoptUnit`
and its derates are byte-identical with and without the fold; vintage clones
copy custom columns, so a flagged parent's clones carry the flag into the
solve-time margin.

**Blast radius, measured by running the whole adequacy suite under a
prototype: 2 failed, 631 passed** — `test_M1_membership_pin_and_A4…` (the
`gas_static` row recomputes on capacity 72, from 80; `gas_ones` and
`hydro_const` unchanged) and `test_A8_elcc_nameplate…` (`nameplate_mw`
80 → 72). Nothing else in the engine suite, nothing in
`test_adequacy_mc*`/`elcc*`/`reserve_margin*`/`portfolio*`, nothing in the
golden fixture.

**H4 — preflight tells the truth again.** `static_p_max_pu_not_applied` is
retired (its sentence is false after H3). Two codes replace it, both from
the membership walk so they reach a carrier-default-only import:

| code | fires when | says |
|---|---|---|
| `availability_may_include_outages` (warning) | occurrence unit, `q > 0`, flag unset, static `p_max_pu < 1` and **no `p_max_pu` column** — the same gate as the fold, so the sentence is true of exactly the units the fold touches | both the CF and the outage rate are applied (nameplate × CF × (1 − q)) by the engines and the margin alike; if the value is a historical capacity factor that already contains forced outages — PyPSA-Eur's nuclear table is one — set `p_max_pu_includes_outages` on the asset so the rate is not applied twice |
| `outages_folded_into_availability` (warning) | flag set on an occurrence unit with a sub-1 availability | N generator(s) are modelled without sampled outages because their availability is declared to include them; the reserve margin credits them at the availability alone. **Variant** `…_ignored` when the flag is set but there is no sub-1 availability (static 1, no informative series), or no outage data at all — the latter walked from `_membership_walk` directly, because `occurrence_units` excludes a `source == "missing"` row |

`profile_and_outage_modelled` keeps its population **unchanged** —
asset-typed, an **informative** series (constant *or* varying), `q > 0` —
and its sentence, which is true of every unit in it, because v6 does not
fold a constant series and such a unit is still mixed exactly per hour
(v5 review, finding 2: a draft parenthetical said "varying", which would
have left an unflagged constant-series unit named by **nothing**); it loses its false
remedy ("remove the outage rate", which a carrier-default unit cannot do) and
**names the flag instead**, so a series-carrying unit whose CF may already
include outages is told the flag exists. A flagged asset-typed unit has
`q = 0` and so leaves that population and enters
`outages_folded_into_availability` — one sentence per unit, never two.
Verified on eleven fixtures including S31: one code each, none silent that
should speak, the retired code gone, `_ignored` reachable at both routes.

**H4b — the payloads say it too.** `/copt` and `/mc` carry
`folded_units` — `(name, folded_constant, source: "static")`, the `source`
field existing so 12i can add `"constant_series"` — and
`deterministic_units`. Both are needed because a folded unit is in **no**
existing list, and a deterministic unit leaves `/copt`'s `profile_units`
(which is `mixed + netted`) after H2b; `fidelity_note`'s counts stay true
for what remains. **`/mc` is different and must be said so** (v5 review,
finding 3): it never calls `split_fleet`, and builds `profile_units` as
"every unit with a profile", so a deterministic unit stays in it — where
that list's documented meaning, "outages were sampled on the availability
series", is false of a `q = 0` unit. On `/mc`, `deterministic_units` is
`[u for u in inputs.units if u.profile is not None and u.q <= 0]` and those
names are **removed** from `profile_units`; the two lists are disjoint.
`reserve_margin_facts` no longer lists a rate-zero unit under
`carrier_default` (its derate uses no class average), so preflight's "derates
N assets using carrier class averages" stays true. The FMECA row of a
rate-zero unit carries a `note`; its `source` still reads `carrier_default`
for a flagged library unit, because that is where the rate came from before
it was folded.

**H5 — what does not change.** The portfolio population, the net-load window
and both loops' levers. A static value creates no series, and the population
is decided by the series rule on both halves.

**H6 — `coupling.snapshot_hash` hashes `float(u.q)`.** It does not today (it
hashes name, capacity, profile, capacity series, storage and residual), so a
flag flip leaves the hash **equal** while the MC moves (measured 13.69 h →
3.69 h) — a break of the hash's own documented contract, "equal hash ⇒
bit-identical MC", which both certifying loops rely on for plateau reuse.
One line. Verified free: no test anywhere pins a `snapshot_hash` literal, the
hash is stable on an unchanged network, and 158 coupling/loop/activity/
frontier tests stay green under it.

## 2. What moves for a user (stated, not hidden)

Every project with a static `p_max_pu < 1` on an outage-bearing generator
sees its COPT, MC, ELCC and both certifying loops move after this — LOLE
**up**, because the unit was being credited at nameplate. **H2b moves
numbers for projects with no flag at all**, and that must be said too: a
typed `outage_rate_value = 0` on a profiled unit is reachable today, and on
a fleet at the exact-mixture cap that unit's promotion out of the mixture
frees a slot and pulls a netted unit back in — measured 96.828 h → 98.379 h,
which is the exact answer. A correctness improvement, but a movement. A PyPSA-Eur import
with nuclear moves from the engines' large error (CF ignored) to the margin's
small one (rate applied twice) **until the flag is set**, and preflight names
the asset and the flag on every such run. That is the trade the finding
recorded: the engines and the margin now agree, and the remaining question is
answered by the one party who can — the person who knows what the number is.

## 3. Tests (every ★ with its bite)

- ★ H1a: `resolve_outage_params` returns `rate 0.0` + `outages_in_availability
  True` for a flagged row with asset data and a sub-1 availability; unchanged
  for `source == "missing"`; unchanged (rate kept) when the availability is 1.
  Bite: ignore the flag.
- ★ H1b — the data model, every path the reviews broke: a first `POST` on a
  frame lacking the column leaves every other row `False` in a `bool` column;
  **a solve with `voll > 0` followed by a project save returns 200 and the
  column reads `bool`** (bite: drop the restore-callback and export-helper
  normalisers — the save is a 500); a project whose **on-disk** column is
  `float64` (an older save, or an external netCDF) loads as `bool` (a clean
  `bool` column round-trips with no load-side normaliser, so the fixture must
  be the float one); `_bulk` on an import whose frame has no column sets the
  flag (bite: drop the create-if-absent); a `_bulk` write of
  `False`/`"false"`/`"True"`/`null` reads back the bool **and the column is
  still `bool` dtype** (the bite is on the dtype: normalising after dispatch
  stores the string `'True'`, which `flag_is_set` reads as set and which
  exports fine — only the dtype separates them); an explicit `null` PUT is
  200 and reads `False` (bite: drop the validator — 422); `committable: null`
  and `p_nom_extendable: null` read `False` where today each is a 500;
  **`Link.cyclic_delay: null` reads `True`**, from the metadata; and
  **`active: null` is 422**, naming `true`/`false` and saying why.
- ★ H2a: the margin's derate reads 0.80 with the flag and 0.76 without, on the
  §0 fixture, through `reserve_margin_facts`. Bite: as H1a.
- ★ H2b: nine units with informative **varying** series (the
  `[0.05, 0.15, 0.35, 0.45]` tile) with `g0` flagged, **at load 300**, read
  the exact **98.379 h** against the shipped **96.828 h** — the load is part
  of the pin, because the fixture is saturated at load 600 (168.0 h either
  way, no bite). Also: the split has no netted unit; `g0` stays in `units`,
  in the portfolio population and in `elcc_candidates`; it keeps its FMECA
  row (ΔEUE 0, noted); and it appears in `split.deterministic` on **both** a
  single-block and a two-period fixture. Bites: leave a rate-zero profiled
  unit in the mixture; net it out of `units` (the portfolio loses it); drop
  `deterministic=()` from `attribute_criticality` (the row vanishes); rebuild
  the merge without `deterministic` (empty on the two-period fixture).
- ★ H3a: static `0.8` ≡ scaled capacity ≡ a constant series `0.8` — the
  third leg is a cross-route oracle **against a path v6 deliberately leaves
  alone** (a constant series is not folded here; that is 12i), not a claim
  about it: COPT
  metrics to 1e-9 on grid (eight units) and `mc_adequacy` LOLE/EUE identical
  under one seed; off grid (`cf = 0.833`) the table mean shifts by exactly
  `cap × cf` and EUE agrees within **5e-3** relative, with **no** LOLE
  equality asserted. Bite: drop the capacity scaling — static reads the
  nameplate row (8.40 h).
- ★ H3b: the §0 table, all three rows, hand values pinned (`16.38`, `800.1`,
  `168.0`). Bite: as H3a.
- ★ H3c: 12d interaction — a static CF on a unit with a per-period capacity
  series scales the **series**, not only the scalar. Bite: scale the scalar
  only — `activity.block_capacity` takes the block's constant value, so
  every block capacity is left unscaled.
- ★ H3d: the must-take branch is untouched — a must-take farm with a static
  CF is netted at `static × cap` exactly once. Bite: apply the fold before
  the `source != "missing"` branch — the CF is squared (measured: LOLE
  1.2 → 24.0, EUE 72 → 264).
- ★ H3e: a static `0.8` beside an **all-ones** `p_max_pu` column keeps
  nameplate 100 and the margin's `0.95`, because PyPSA reads the column
  (measured `[1.0, 1.0, …]`). Bite: gate the fold on `series_is_informative`
  instead of on the column's presence — the capacity reads 80 and the
  engines disagree with the LP by 20 %.
- ★ H2c: a **flagged constant-series** unit — the case the narrowing creates
  — has `rate 0.0`, keeps its profile, lands in `split.deterministic`, and
  reads the exact **168.0 MWh** on the §0 fixture (equal to what 12i's fold
  would give it, and never worse: the bucket nets in float and costs no
  `K_EXACT` slot). It is named by `outages_folded_into_availability`, not by
  `profile_and_outage_modelled`. Bite: leave it in the mixture.
- ★ H4a–H4c: one test per code through `validate_for_run` (incl. the
  `_ignored` variant at both routes and the retired code absent); the
  asset-typed flagged unit gets one sentence, not two; a varying-series unit
  is named by `profile_and_outage_modelled` alone, and that message names the
  flag; and an **unflagged, asset-typed, constant-series** unit with `q > 0`
  is **also** named by `profile_and_outage_modelled` (bite: narrow that
  population to varying series — the unit goes silent, named by nothing).
- ★ H4d: `/copt` and `/mc` carry `folded_units` and `deterministic_units`, on
  **single-block and multi-block** fixtures; `reserve_margin_facts["carrier_default"]`
  omits a rate-zero unit — on a **carrier-default (library-rate) fixture**,
  since on S31 both units carry typed rates and the list is empty either way.
  Bites: drop each list; drop the `carrier_default` skip; leave a
  deterministic unit in `/mc`'s `profile_units` (the two lists overlap).
- ★ H6: `snapshot_hash` differs across a flag flip and is stable on an
  unchanged network. Bite: drop the `q` term — the hashes collide while the
  MC moves.
- Rewritten pins, listed so none is a surprise (measured by running the whole
  adequacy suite under a prototype — exactly these two fail):
  `test_M1_membership_pin_and_A4…` (`gas_static` hash on capacity 72) and
  `test_A8_elcc_nameplate…` (`nameplate_mw` 80 → 72). Plus the **four**
  `test_adequacy_occurrence.py` tests pinning the retired code (lines 348,
  376, 395, 413).
- Frontend: `IncludesOutagesInput` on the Generator card only (`OutageInputs`
  is shared by five cards and stays as it is); the key in `toFS(gen, [...])`
  so it survives the remove+add save cycle; the payload assigns
  `p_max_pu_includes_outages: form.… === 'true'` explicitly beside
  `committable`, never through the `...current` spread; `Generator` in
  `types.ts`; `adequacy.p_max_pu_includes_outages` in `propertyDocs.ts`. Two
  new vitest files, bitten.
- Docs: `copt.py`'s module docstring and `_occurrence_profile`'s "the static
  column is deliberately NOT read"; `stress.py`'s "the availability
  multiplier hits generators whose availability is PROFILE-BORNE — the same
  must-take rule the COPT applies", which goes stale for the same reason; the design spec's and the MC spec's
  matching lines; a "superseded by 12h" note on 12c-pre §1.3;
  `QA_E2E_PLAN.md` S31.

## 4. Live — S31

`GET /results/reserve_margin` serves only the **persisted solve-time stash**
(204 otherwise), so S31 sets `solver_config.reserve_margin = 0.1` and solves
— S23's pattern — before reading a derate. The §0 fixture cannot be that
network: with `gas` at 25 MW the derated firm capacity is 99.75 MW against a
110 MW requirement and preflight refuses `reserve_margin_unreachable`
(measured over HTTP). The S31 fixture is §0 with **`gas` at 50 MW, marginal
cost 20** (measured: `optimal`; `required 110.0` against `firm_fixed 123.5`;
row `('nuc', 'ALL', 0.76, 'asset')`). Its COPT hand values were derived on
**that** fixture and are pinned by a unit test beside H3b, so the live rows
compare against numbers the suite owns.

| id | check |
|----|-------|
| S31.1 | the S31 fixture over the API (168 h snapshots); preflight names `nuc` under `availability_may_include_outages` and the retired code is absent |
| S31.2 | `/results/copt` EUE reads **600.6 MWh** (before 12h: **441.0**, the nameplate row) |
| S31.3 | `PATCH /api/network/_bulk` sets the flag **on a frame whose column the API POST never created** (the fixture is built without it, so the create-if-absent is what makes this a 200); preflight now carries `outages_folded_into_availability` and not the first code; `GET /generators` reads it back `true` |
| S31.4 | `/results/copt` EUE reads **168.0 MWh**; after a solve under `reserve_margin = 0.1`, `/results/reserve_margin`'s row for `nuc` reads derate **0.80**, and the project **saves** (200) after that solve |
| S31.5 | clearing the flag through `_bulk` (`null`) reads back `false` (not a 500), and a re-solve's margin row reads **0.76** again |

Bitten live by dropping the capacity scaling (S31.2 reads 441.0), by ignoring
the flag in `resolve_outage_params` (S31.4 reads 600.6 / 0.76), and by
dropping the export-helper normaliser (S31.4's save is a 500).

## 5. Out of scope, recorded

**Phase 12i — fold a CONSTANT informative series as capacity, too.** v2 added
this to 12h after measuring that the shipped `2^k` route understates such
fleets: nine units at a constant 0.8 read **1.374 h** against the exact
**1.709 h**, a **19.6 %** understatement, because the ninth unit is pushed
past `K_EXACT` into the netted approximation. It is a real defect and worth
its own phase — but it is **not this one**. Measured by the v4 review, side
by side on the same suite:

| | 12h with the constant-series fold | 12h without it (v5) |
|---|---|---|
| adequacy suite | 2 failed, 631 passed | **2 failed, 631 passed** (same two) |
| `portfolio.py` | membership gate rewritten, capacity unfold, a zero-division edge | **zero lines** |
| blockers produced across four reviews | three, **all** from this half | — |
| adjudicates the fourteenth finding | yes | **yes** — the finding is about the *static* column |

**What 12i is still worth, precisely.** H2b already handles the *flagged*
half of the constant-series population exactly — measured equal to the fold
and never worse — so 12i's remaining value is the **unflagged**
cap-saturating fleet: the 1.374 h against 1.709 h case. And `folded_units`
ships with `source: "static"` so 12i adds `"constant_series"` to a contract
that already exists.

The last blocker it produced is instructive and belongs in 12i's premise:
`series_is_informative(zeros)` is **True**, so an all-zero availability series
— the ordinary "off for this study" idiom, and `test_A8`'s own `wind_zero`
fixture — folds to `folded_constant = 0.0`, and the unfold the portfolio
needs then divides by zero (a 500 on `/results/elcc_portfolio`). 12i must
decide what a zero-constant fold means before it ships.

12i also inherits: the portfolio membership must keep the series rule on both
halves (a marker as the gate admits static-CF units and turns `test_B11`
red); a folded member must report its **unfolded** capacity or
`portfolio_block` refuses `capacity_basis_mismatch` for every one;
`profile_and_outage_modelled` must split by `profile_kind`, because its
sentence is false for a folded constant series; and that split reverses
12c-pre's Q4 decision for carrier-default constant-series units, which must
be stated rather than inherited.

**Also out of scope:** inferring the flag from a carrier name or an import
source ("nuclear from PyPSA-Eur") — a guess in the data model is the thing
this program keeps being wrong about; and StorageUnit/Link/Line occurrence
rows, which carry no availability the engines read this way.

## 6. The review record

| plan | verdict | findings | what it was rejected on |
|---|---|---|---|
| v1 | REJECT | 12 (1 blocker) | a custom bool column is wrong at every write path; a flagged series unit displaces a real one from the exact mixture; an exactness claim whose pin could not see the failure; an unimplementable live step |
| v2 | REJECT | 13 (1 blocker) | the normaliser missed the solve, which breaks the next save; a ★ that could not bite; the H2b mechanism removed the unit from the portfolio, ELCC and the CRN positions |
| v3 | REJECT | 16 (2 blockers) | **its own marker**: as a membership gate it admitted static-CF units (`test_B11` red) and rescaled the capacity the margin is compared against (`capacity_basis_mismatch`) |
| v4 | REJECT | 10 (1 blocker) + a scope recommendation | the unfold divides by zero on an all-zero series; a merge-only field leaves the payload empty on single-block networks; folded units vanish from `/copt`'s disclosure — **and the scope measurement above** |
| v5 | **ACCEPT WITH CHANGES** | 8 (0 blockers) | the fold gate must be the column's presence, not informativeness (an all-ones column supersedes the static in PyPSA *and* in the margin); a draft parenthetical would have silenced constant-series units; `/mc` has no `split_fleet`; a second True-defaulting bool; four missing pins and six record slips — **all applied in v6** |

The v1–v3 records are appendices of their own plan files. **v4's and v5's
records are the tables above plus this section**, since each rejection's
detail is carried forward into the version that answers it; nothing was
deleted.
