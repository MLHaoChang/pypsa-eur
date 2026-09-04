# Phase 12d — the engines honour activity and vintages (plan, v1)

The first hardening item after the portfolio ELCC (12c). Two recorded
defects, one mechanism:

* **The engines' activity blindness** (12c v3 review, finding 2; spec v1.6
  §"activity_mismatch"): `fleet_and_residual` and `snapshot_inputs` admit
  every generator and store that clears the scope tests to EVERY hour of the
  horizon. `build_year` / `lifetime` / `active` are never read. The LP and
  the reserve margin mask by `get_active_assets(P)`
  (`solver_service.py:3497-3506`), so on a multi-period project the COPT,
  the MC study, every ELCC row and both certifying loops score a 2035 asset
  as present in 2030, and the portfolio block refuses the comparison
  (`activity_mismatch`) rather than compare across it.
* **`solved_capacity`'s vintage blindness** (12c §2.2; v3 review finding 4):
  after a vintage-expanded solve the restore writes the parent's `p_nom_opt`
  as the SUM over vintages and drops the vintage rows
  (`vintage_service.py:747-767`); the engines read that sum in every period,
  so a vintage the LP built for 2040 is scored in 2030. The margin's payload
  reads the per-vintage breakdown the restore persists
  (`report.py:126-147`), so the two disagree, and the block refuses
  (`capacity_basis_mismatch`).

Both are the same statement: an asset's capacity is a function of the
period. This phase gives every engine that function.

## 1. The rule

**Per-period capacity.** For a component row `i` (generator or storage
unit) and a period block `P` of the snapshot axis (`mc._period_blocks`:
the investment periods in axis order, or the single block `"ALL"` on a
flat axis):

    c_{i,P} = cap_i · [i active in P]                          (plain row)
    c_{i,P} = initial_i · [parent active in P]
            + Σ_v opt_v · [by_v ≤ P < by_v + lt_v] · [parent.active]   (vintage-expanded parent)

where `cap_i = solved_capacity(row)` (unchanged rule), `[i active in P]` is
PyPSA's own mask `n.components.<comp>.get_active_assets(P)` for an integer
label and `get_active_assets()` (the static `active` column only) for the
`"ALL"` block — the SAME call the margin makes, and the same one PyPSA's
`optimize` uses to decide which variables exist. The vintage line reads
`n.meta["vintage_results"][cls][parent]`: `initial_capacity`, and per
vintage `build_year`, `p_nom_opt` and (new, §2.5) `lifetime`. It replicates
PyPSA's one-line activity rule for rows that no longer exist, and E1 pins
it against PyPSA on rows that do.

**The breakdown is used only when it is CONSISTENT with the row:**
`initial + Σ_v opt_v == p_nom_opt(parent)` within rel 1e-9 / abs 1e-6.
The restore writes exactly that identity (`vintage_service.py:735-747`),
so post-solve it holds; a breakdown left over from an earlier solve on a
network whose capacity has since been edited fails it, and the row falls
back to the plain rule (`cap · activity`). Silently, because the engines
have no channel for warnings — and the portfolio block's fingerprint
catches the user-facing case (`stale_report`).

**Activity series.** The engines keep their `(H,)` shape. Each unit and
store carries `activity_h = c_{i,P(h)} / cap_i^max` with
`cap_i^max = max_P c_{i,P}` as its `capacity_mw` (the nameplate a firm block
brackets, the capacity the loop hashes), so `activity ∈ [0, 1]`, is
piecewise constant per block, and is `None` — the scalar path,
byte-for-byte — whenever it is identically 1 (every single-period network
without `active=False`, every multi-period network whose assets are all
active everywhere: D1, M1, M2, the RTS-79/RBTS anchors are untouched by
construction). A row with `cap^max = 0` is what "unbuilt" already means and
takes the `keep_zero_capacity` branch unchanged; membership (the name set
and its order — the CRN contract) does not depend on activity, so the loops'
positional substreams survive a solve that changes what is active.

## 2. Where it lands

### 2.1 `services/adequacy/activity.py` (new)

- `active_mask(n, comp, period) -> pd.Series[bool]`: the six lines now at
  `solver_service.py:3497-3506` — `get_active_assets(int(P))` for an
  integer label, `get_active_assets()` for a string, all-True on any
  exception (a period not in `n.investment_periods`, a frame without the
  columns). **The margin's `_active` becomes a call to it** (same
  semantics, pinned by the margin's own tests: the `build_year=2040`
  extendable must still not satisfy 2030), so the two sides agree by
  construction rather than by parallel copies.
- `vintage_breakdown(n, cls, name, row) -> dict | None`: the consistent
  breakdown (§1) or None.
- `capacity_by_period(n, comp, name, row, blocks) -> list[float]`: §1's
  rule per block label.
- `activity_series(n, comp, name, row, blocks) -> tuple[float, np.ndarray | None]`:
  `(cap_max, activity)`, `activity=None` when identically 1 (tolerance
  1e-12) or when `cap_max == 0`.
- `activity_summary(units, storage, blocks) -> dict`: the payload
  disclosure (§2.6): per block label the names with activity 0 (`inactive`)
  and with `0 < activity < 1` (`partial`, the vintage case), plus one
  sentence or None.

### 2.2 `copt.py`

- `CoptUnit.activity: np.ndarray | None` — a second `(H,)` field with the
  same `field(default=None, compare=False, hash=False, repr=False)`
  declaration as `profile`.
- `_availability_mw(u, H)` = `cap · (profile or 1) · (activity or 1)`; the
  shape check covers both arrays.
- `_membership_walk` unchanged (membership is not activity). The walk's
  `cap` becomes `cap^max` from `activity_series` in `fleet_and_residual`
  and in `portfolio_population`'s superset pass — ONE helper,
  `_walk_with_activity(n, elec_buses, blocks, *, keep_zero_capacity)`,
  yields `(g, cap_max, activity, occurrence_row)` so the two cannot differ.
  `must_take_generators` / `occurrence_units` (membership only) are
  unchanged: a row with `cap^max = 0` is dropped by the walk exactly as a
  `cap = 0` row is today.
- `fleet_and_residual`: units get `activity`; a must-take's netted series
  is `p_max_pu_h · cap^max · activity_h` (its static value likewise); the
  residual therefore no longer carries a 2035 farm's output in 2030.
- `screening_analysis`: when any unit carries an `activity`, the fleet is
  evaluated PER BLOCK: within block `P` every unit's activity is one
  constant `a_{i,P}` (asserted), so the block's fleet is the units with
  `cap_i · a_{i,P} > 0` at THAT scalar capacity, with `profile[start:end]`
  and `activity=None` — the existing single-block pipeline (split, net,
  table, mixture, attribution) runs unchanged on the block's residual and
  weights. Merge: `lole/eue` summed, `lolp_max` the max, `by_period` the
  union (each block yields its own key), rows merged BY NAME (`ΔEUE` and
  `criticality_eur_per_year` summed, `severity = crit / occ`, a `note`
  kept if any block set it), `split` reported as the union of names
  mixed / netted in any block, `fidelity_note` from that union. When no
  unit carries an activity the call is the single-block path it is today
  (M1's residual and row hashes, and every COPT pin, are unchanged). The
  per-block table is EXACT for piecewise-constant capacity; folding
  activity into `profile` instead would push every asset with a build year
  into the `2^k` mixture and the netted approximation beyond `K_EXACT`, and
  would say so in a fidelity note about a fidelity that was never lost.
  `dist` and `residual` in the return value become per-block dicts keyed by
  label when there is more than one block (the route reads neither).

### 2.3 `mc.py`

- `StorageSpec.activity: np.ndarray | None` (same declaration).
- `snapshot_inputs`: units come from `fleet_and_residual` with their
  activity; stores get `p_nom_mw = cap^max`, `e_nom_mwh = max_hours ·
  cap^max`, `activity`; `vre_profiles[name] = p_max_pu_h · cap^max ·
  activity_h` — the SAME product the residual netted, so un-netting is
  exact.
- `sample_capacity`: `cap` is the scalar when both `profile` and `activity`
  are None (M2's byte-identical path), else the `(H, 1)` column
  `(profile or 1) · (activity or 1) · cap` — the same `np.add(where=)`
  broadcast 12c-pre introduced; the chain, its stream and its consumption
  are untouched.
- `_simulate_blocks`: per block, each store's factor `a_{s,P}` (its
  activity at `start`, asserted constant over the block); stores with
  factor 0 are dropped from the block's arrays, the others dispatch at
  `p_nom · a`, `e_nom · a` (the vintage fraction). The `single_order`
  shortcut is per block. A network without store activity runs the arrays
  it runs today.

### 2.4 `elcc.py`, `portfolio.py`

- `unit_nameplate_mw(u)` = `max_h` of `(profile or 1) · (activity or 1)`
  (finite) `· cap` — the unit's best ACTIVE hour; dominance holds hour by
  hour as before. A unit with activity but no profile has nameplate
  `cap^max`; `elcc_candidates` is otherwise unchanged (a profiled unit at
  nameplate 0 is still excluded).
- `_resolve` for `vre`: the preserved profile already carries activity;
  its `max` is the bracket. Nothing else changes in `elcc_of_removal`: a
  generator member excluded by position is excluded in every hour, and a
  period in which the member was never active contributes nothing to the
  Δ = 0 probe there — the portfolio's `no_contribution` row (12c A5) is
  what a per-period ELCC of an inactive group already says.
- `Member` gains `capacity_by_period: tuple[tuple[str, float], ...]`
  (label → `c_{i,P}`); `capacity_mw` stays `cap^max`.
  `portfolio_population` reads both from the shared walk helper.
- `portfolio_block`'s activity check (A4): a member with no margin row in
  `P` is a mismatch ONLY when `c_{m,P} > 0`; a member with `c_{m,P} = 0`
  and no row is the two sides agreeing. The capacity check compares the
  payload's per-parent aggregate in `P` with `c_{m,P}` (not with
  `cap^max`). The "credited but absent from the snapshot" check is kept
  as the tripwire it now is. The `activity_mismatch` status stays in the
  enum; E4b reaches it with a payload row edited by hand.
- `network_fingerprint`: add the static `active` column of generators and
  storage units, storage `build_year` / `lifetime`, and the consistent
  vintage breakdown (label, build_year, p_nom_opt, lifetime per vintage) —
  a breakdown that changes under an unchanged frame is a solve the block
  must not compare against.

### 2.5 `vintage_service.py` (one field)

The persisted breakdown gains `"lifetime": float(df.at[v_name, "lifetime"])`
per vintage, read at meta-build time (`vintage_service.py:531-547`) while
the rows exist. Older breakdowns without it: `lifetime` falls back to the
parent's finite positive `lifetime`, else `inf` — the two branches of the
rule that created the row (`vintage_service.py:392-396`), stated once in
`activity.py` as the fallback and pinned (E6c).

### 2.6 Routes and the two loops

- `/copt` and `/mc` payloads gain `activity: {"by_period": {label:
  {"inactive": [names], "partial": [names]}}, "note": str | None}`
  (`activity_summary`), computed under the lock beside the snapshot; the
  note reads "k unit(s) / store(s) are inactive in <P> by build year and
  lifetime: <names> (…)" and names partial (vintage-scaled) ones the same
  way. `None` when nothing is inactive or partial anywhere.
- Both loops' `_hash` (`results.py:3819`, `4507`) hash the activity bytes
  beside the profile bytes and the store activity beside `(p_nom, e_nom)`:
  "exactly what the MC reads" stays true (12c-pre review finding 5's
  rule).
- Frontend: one chip on the COPT card (`copt-activity-note`) and on the MC
  panel (`mc-activity-note`), "n inactive in P" with the note as title;
  `activity` typed on both results.

### 2.7 Docs

MC spec **v1.7** (the rule, the per-block COPT, the storage factor, the
breakdown consistency test, the fallback lifetime); margin spec §2.1 note
(the shared mask); design doc §5.3 note; QA plan S27; this plan's shipped
record.

## 3. What does NOT change (stated)

- Membership: the walk, `must_take_generators`, `occurrence_units`, the
  preflight's disclosure, the 404/422 contracts.
- Any network where every scope-admitted asset is active in every period
  with no vintage breakdown: byte-identical on every surface (E0).
- The LP, the margin constraint, the margin payload, `_built`.
- Lines/Links/Stores: not in the engines (v1 scope), so not here.
- The demand side: loads have no activity in these engines (a load's
  `active` is not honoured by the margin either — recorded, not fixed).

## 4. Hand values

**F1 — the activity fixture** (`tests/test_adequacy_activity.py::activity_network`).
Two periods 2030 / 2035, H = 24 h each, weights 1, flat load 80 MW,
`base_a` and `base_b` 50 MW each at q = 0.1 (build 2000, life 100),
`new` 40 MW at q = 0.2 with `build_year=2035`, `lifetime=100`.

- 2030 (two units): shed iff either base is down: LOLP = 1 − 0.81 =
  **0.19**; EUE_h = 0.09·30·2 (one down: 80 − 50 = 30) + 0.01·80 =
  5.4 + 0.8 = **6.2 MWh/h**.
- 2035 (three units): shed iff both base down, or one base down AND `new`
  down: LOLP = 0.01 + 2·0.09·0.2 = **0.046**; EUE_h = 0.01·(0.8·40 +
  0.2·80) + 0.036·30 = 0.01·48 + 1.08 = **1.56 MWh/h**.
- Per period LOLE = LOLP · 24: **4.56 h** and **1.104 h**.
- Blind (today): both periods at the 2035 values.

**F2 — the vintage fixture**: `_vintage_network` with wind
`lifetime=10` (so `wind@2030` expires before 2040 and the LP must build
`wind@2040`), margin 0.5, flat load 150, `base` 200 MW derate 0.95 → 190
firm, required 225: the LP builds **35 MW in each vintage** (min cost),
parent `p_nom_opt = 70`; `c_2030 = c_2040 = 35`; `cap^max = 35`,
activity identically 1 → None. The residual in each period is
`150 − 35·profile`; blind reads `150 − 70·profile`. With `alternating`
the derate is 0.5 and the vintages are 70 each, parent 140.

**F3 — the vintage-fraction fixture**: F2 with the 2030 bound raised so
the LP builds 35 in 2030 and the 2040 vintage is `p_nom_min = 20`:
`c_2030 = 35`, `c_2040 = 20 + 35·[lifetime covers 2040]`; with
`lifetime=100`, `c_2040 = 55`, `cap^max = 55`, `activity = (35/55, 1)`.
The 2040 residual nets 55·profile, the 2030 residual 35·profile — the
`partial` case of the disclosure.

## 5. Tests (`tests/test_adequacy_activity.py` unless noted)

★ = must fail against the named broken variant; restores by hash.

- **E0** (anchor): D1, M1, M2, the RTS-79 / RBTS anchors, S24-S26's unit
  pins — unchanged (existing tests); plus a new hash pin of
  `snapshot_inputs` on `two_period_network()` (all active) recorded on
  `6e40a7a` before the change.
- **E1** (anchor + ★): `active_mask` equals PyPSA's `get_active_assets`
  per period on F1 and `static.active` on a flat axis; the vintage rule in
  `capacity_by_period` equals PyPSA's mask applied to LIVE vintage rows
  (build the rows, compare). Bite: `<` for `<=` on `build_year`.
- **E2** ★ COPT: `/copt`-path `screening_analysis` on F1 gives
  `by_period[2030] = 4.56 h`, `[2035] = 1.104 h`, EUE 148.8 / 37.44 MWh,
  total 5.664 h; `new`'s criticality row is nonzero and `base_a`'s ΔEUE
  is the SUM of its two per-block counterfactuals (recomputed by hand from
  the two tables). Bite: drop the per-block loop (single table) → 2030
  reads 1.104.
- **E3** ★ MC: `mc_adequacy` on F1 at 2000 draws: `by_period[2030]`
  within its CI of 4.56, `[2035]` of 1.104; and BIT-IDENTITY —
  `by_period[2030]` equals, bit for bit, the 2030 block of a network
  without `new` at all (the excluded unit still consumes its substream:
  `sample_capacity(exclude={new})` and activity 0 agree to the byte).
  Bite: ignore `activity` in `sample_capacity`.
- **E4** ★ must-take + portfolio (rewrites 12c's B12):
  `two_period_network(wind_build_year=2035)` — the 2030 residual equals
  the demand (nothing netted), `vre_profiles["wind"][:H] == 0`; solved
  under the margin the block is `ok`, the 2030 row `no_contribution`, the
  2035 row `ok` with the 12c-B1-style bracket. Bite: net the must-take
  without activity → 2030 residual differs, and the block is
  `activity_mismatch` (today's outcome, which B12 pinned and which is no
  longer correct).
- **E4b** ★ tripwire: a payload row for a generator the snapshot does not
  know, added by hand → `activity_mismatch`. Bite: drop the check.
- **E5** ★ storage: F1 plus a 30 MW / 2 h store with `build_year=2035`:
  the 2030 block's `(lole, eue)` per draw equals the no-store run's bit
  for bit; 2035 differs. Bite: ignore store activity in
  `_simulate_blocks`.
- **E6** ★ vintage (F2, real solve): `capacity_by_period(wind) = [35, 35]`
  and the parent's `p_nom_opt` is 70; the MC residual per period is
  `150 − 35·profile` (hash-pinned by recomputation); `portfolio_block`
  is `ok` with the capacity check passing per period. Bite: use
  `solved_capacity` (70) in every period → residual differs and the
  block reads `capacity_basis_mismatch`.
- **E6b** ★ (F3): `activity == (35/55, 1)`, `cap^max = 55`, the 2040
  residual nets 55·profile; the disclosure lists `wind` under `partial`
  for 2030. Bite: the breakdown's `lifetime` ignored (treat the 2030
  vintage as expired in 2040) → `c_2040 = 20`.
- **E6c** ★ fallback lifetime: a breakdown without `lifetime` on a parent
  with `lifetime=10` expires the 2030 vintage in 2040; with `lifetime=inf`
  it does not. Bite: fallback `inf` unconditionally.
- **E6d** ★ stale breakdown: edit `p_nom_opt` after the solve →
  inconsistent → plain rule (`cap · activity`). Bite: drop the consistency
  test → the stale breakdown's 35 is used against a row that says 90.
- **E7** ★ static `active=False` on a FLAT network: excluded from the
  fleet, the candidates and the residual; the margin agrees (its own mask).
  Bite: `activity_series` ignoring the "ALL" mask.
- **E8** ★ loops' `_hash`: two snapshots differing only in one unit's
  activity (and, separately, one store's) hash differently. Bite: hash
  without the activity bytes.
- **E9** ★ `unit_nameplate_mw` / candidates: `new` on F1 is a candidate at
  40 MW (not 20, not 0); a profiled unit active in 2035 only has nameplate
  `max_{h∈2035}(profile)·cap`. Bite: nameplate as `mean(activity)·cap`.
- **E10** ★ fingerprint: flipping `active` on a generator, or changing a
  vintage's persisted `lifetime`, changes the fingerprint. Bite: drop the
  fields.
- **E11** route + frontend: `/copt` and `/mc` carry `activity` with `new`
  under `inactive` for 2030 on F1; the chips render with the note as title
  (vitest, two cases each); `null` note and no chip on a single-period
  network.
- **E12** ★ the margin's `_active` delegates: monkeypatch
  `activity.active_mask` to all-False → `reserve_margin_facts` credits
  nothing. Bite: the margin keeps its own copy.

## 6. Bites, gates, live

Every ★ is run against its named variant from a saved copy of the module
(restored by sha256, never by `git checkout`); a bite that does not bite
is recorded and replaced before the test is kept. Gates on the final
commit: adequacy suite; full tree diffed against `base_fails_sorted.txt`
(branch-minus-master EMPTY); frontend vitest + `tsc`; live S15–S27 on the
uvicorn server. **S27** (QA plan): F1 built over the API (two periods,
`new` at `build_year=2035`), `/copt` `by_period` at the hand values,
`/mc` within CI, both payloads' `activity.by_period["2030"].inactive ==
["new"]`, and the chips; bitten live by the single-table variant of
`screening_analysis`, restored by hash.

## 7. Out of scope, stated

- Loads' `active` (neither side honours it — recorded).
- Links / Stores / Lines in the engines.
- The `/copt` synchronous contract and the abort routes; the static-CF
  flag; the `validate` route TOCTOU (next hardening items).
- Changing the margin's arithmetic: it already masks; this phase makes
  the engines agree with it and removes two refusals that existed only
  because they did not.

## 8. Open questions for the review

1. **Per-block COPT vs. folding activity into `profile`.** §2.2 argues
   per-block is exact and the fold degrades fidelity for no reason. Is
   there a case where the per-block merge of criticality rows (sum of
   per-block ΔEUE) is NOT what the horizon-wide leave-one-out would give?
   (The counterfactual "unit i perfectly available" is per hour, the EUE
   is a sum over hours, and blocks partition the hours — the sum should
   be exact.)
2. **The breakdown consistency test.** Is `initial + Σ opt == p_nom_opt`
   the right staleness test, or can a legitimate post-solve state fail it
   (the Pass-3 deficit recovery at `vintage_service.py:700-720` adds to
   the earliest vintage AND to `total`, so the identity still holds — but
   check)?
3. **The vintage fraction as `activity`.** `cap^max = max_P c_P` with
   `activity = c_P / cap^max` keeps the scalar-vs-column dispatch and the
   nameplate meaning; is there a consumer that reads `capacity_mw` as
   "the capacity in the first period" and would now be wrong?
4. **`get_active_assets` on a MultiIndex whose level-0 labels are not in
   `investment_periods`.** The helper falls back to all-True on the
   `ValueError`; should that instead be a loud refusal? (The margin
   falls back silently today.)
5. **The loops.** Activity changes between iterates on a vintage network
   (the LP builds a different vintage mix) — is the `plan_hash` reuse
   still exact, and is there any place a loop compares `capacity_mw`
   across iterates and would now see `cap^max` move?

## v1 REVIEW (2026-09-04, adversarial subagent) — accept with amendments

Verified true by the reviewer (real HiGHS solves via the tests' helpers,
PyPSA 1.3.0 probed directly): F1's hand values; F2 (35/35, parent 70,
parent `wind` inactive in 2040 by its own lifetime, payload 2040 carries
`wind@2040` only) and F3 (35/20, parent 55); `get_active_assets` semantics
(no-arg → static `active`; int label → `build_year ≤ P < build_year +
lifetime` AND `active`; storage identical; a frame without the columns does
NOT raise); E3's core bit-identity (a zero column ≡ `exclude` ≡ a fleet
without the unit, with the unit LAST); the 2×2 of the relaxed activity check
(§2.4) with both sides on the same classifiers, walk and period labels.
Findings, each checked against the code before being recorded:

1. **SERIOUS — E6's portfolio half was vacuous.** F2's flat profile is not
   informative, so `wind` is not a member and the block is `no_population`
   with or without the bite. → E6/E6b's portfolio assertions run on
   `_vintage_network(alternating=True)` with `lifetime=10` (vintages 70/70,
   parent 140); the bite reads engines 140 vs payload 70 per period.
2. **SERIOUS — E4's 2035 row is `ok 0.0` at margin 0.15** (every 2035
   hour sheds iff `base` is down, wind or no wind; measured at 32/128/500
   draws). → E4 solves at `reserve_margin=0.3` (peaker 46.22 MW): 2030
   `no_contribution`, 2035 `ok` with `elcc_mw` pinned (83.203 at draws 32,
   seed 1 — re-measured at implementation).
3. **SERIOUS — the myopic strategy writes same-shape entries** into
   `vintage_results` (`solver_service.py:5946-6012`: `source:
   "myopic_freeze"`, one period, `p_nom_opt = delta`, no `lifetime`) and
   the consistency test admits them. → Stated policy: a freeze entry IS a
   breakdown — the delta exists from its period onward for the parent's
   lifetime and did not exist before it, which is what the myopic LP saw;
   the fallback lifetime (§2.5) is the parent's. Pinned by E6e on a
   hand-built freeze entry.
4. **MODERATE — `elcc.baseline_key` hashes no activity.** → It hashes the
   capacity series bytes of every unit and store (E8b, B7-style).
5. **MODERATE — three membership statements disagreed, and a second walk
   would re-create the disagreement 12c-pre removed.** →
   `_membership_walk` itself yields `(g, cap_max, capacity_series, row)`
   and applies the `cap_max > 0` test; every consumer (the margin,
   `fleet_and_residual`, `must_take_generators`, `occurrence_units`,
   `portfolio_population`) reads that one walk. Membership depends on the
   static `active` column and on "unbuilt or inactive in every period"
   (both are `cap_max = 0`) and on nothing per period.
6. **MODERATE — the consistency test is not a sufficient staleness test**:
   `apply_vintage_bounds` returns before its clear when the bounds are
   gone (`vintage_service.py:185-193`), so a re-solve without bounds keeps
   the old breakdown, whose total can equal the new plain `p_nom_opt`. →
   The non-myopic entries are cleared at the top of `apply_vintage_bounds`
   before any early return (mirror of `_clear_myopic_build_periods`); the
   consistency test stays as the backstop. Q2 answered: every recovery
   path mirrors its addition on `total` (`599-600, 605-606, 682-683,
   708-709, 743-746`); only a NaN `p_nom` can fail the identity, and that
   falls to the plain rule.
7. **MODERATE — E3's bit-identity needs a fixed sample count and `new`
   last** (adaptive batching stops at different `n_samples` on the two
   networks; positional streams). → Stated in E3.
8. **MODERATE — the byte-identity claim in §2.2 is confined to the
   no-activity case**; on the per-block path totals are sums of block sums
   (rel 1e-12), a unit may be mixed in one block and netted in another. →
   The split union is de-duplicated: `netted` if netted in any block, else
   `mixed`; `note` set if any block set it; the return shape is a dict iff
   the per-block path ran.
9. **MODERATE — the all-True fallback is unreachable on a well-formed
   network** (`investment_periods` IS level 0 on a MultiIndex; a frame
   without the columns returns `static.active`); it would only mask a
   string label or a missing `active` column. → `active_mask` has no
   `try`; the margin's `_active` keeps its own guard around the delegated
   call (behaviour preserved, E12 pins the delegation with a lazy import).
10. **MINOR — E6d on F2 expects `(90, 0)`**, the parent being inactive in
    2040 under the plain rule. Stated.
11. **MINOR — the ratio `cap^max · (c_P / cap^max)` is not exact in
    general.** → The unit carries `capacity_series` in MW (`(H,)`, None
    when constant), not a fraction: the residual netting, `vre_profiles`
    and the per-block COPT read `c_P` directly; the sampler's float32
    column is `(profile or 1) · capacity_series`. §1's "activity series"
    is restated in MW.
12. **MINOR — omissions.** `routers/vintage.py` docstring and
    `frontend/src/api/network.ts` type gain optional `lifetime`;
    `CoptResult`/`McResult` declare `activity?` optional. Q3: no consumer
    reads `capacity_mw` as a first-period value (grep); the storage
    nameplate becomes `cap^max`, which still dominates. Q5: the loops
    compare `plan_hash` only.
13. **MINOR — E12 requires a lazy import** in `reserve_margin_facts`.
14. **MINOR — MC assertions** compare against the payload's per-period
    `lole_ci` (12c), not a point value (measured 4.6735 / 1.0515 at 2000
    draws).

Bites audited: every ★ fires except E6 (finding 1, replaced) and E4
(finding 2, re-pinned). Q1: the per-block merge is exact for table and
mixed units (EUE is a weighted sum over hours; blocks partition hours; the
counterfactual is per hour). Q4: answered by finding 9.

### v1.1 — the plan as amended

§1: `capacity_series_h = c_{i,P(h)}` in MW, `capacity_mw = max_P c_{i,P}`,
`None` when the series is constant at `capacity_mw`. §2.1: `active_mask`
raises on a bad label; `_period_blocks` moves beside it and `mc` imports
it. §2.2: one walk (finding 5); per-block split de-dup and return-shape
rules (finding 8). §2.3: the sampler column is `(profile or 1) ·
capacity_series`. §2.4: `baseline_key` hashes the series (finding 4).
§2.5: myopic entries are breakdowns (finding 3); the non-myopic clear at
the top of `apply_vintage_bounds` (finding 6); the fallback lifetime. §2.6
adds `routers/vintage.py` and `network.ts`. §5: E3 (finding 7), E4 at
margin 0.3 with the pinned credit, E6/E6b on `alternating=True`, E6d
`(90, 0)`, E6e (myopic entry), E8b (`baseline_key`), E9 stated for
stores too, E12 lazy import.

## 12d SHIPPED (2026-09-04)

Built as amended (v1.1). `services/adequacy/activity.py` (`period_blocks`,
`active_mask`, `vintage_breakdown`, `ActivityContext.capacity_by_period` /
`capacity_series`, `block_capacity`, `activity_summary`);
`CoptUnit.capacity_series` / `StorageSpec.capacity_series` in MW;
`_membership_walk` yields `(g, cap_max, series, row)` — the one walk for
the margin, the fleet, the must-take list, the preflight population and
the portfolio; `screening_analysis` per block with `_screen_block` the
12c-pre pipeline; the sampler column `(profile or 1) × series`; per-block
store arrays; `unit_nameplate_mw` on the best active hour; `baseline_key`
and the loops' hash (now `coupling.snapshot_hash`, one implementation)
cover the series; `Member.capacity_by_period` and the relaxed activity
check; the fingerprint covers `active`, storage `build_year`/`lifetime` and
the persisted breakdown; the margin's `_active` delegates (lazy import);
`vintage_service` persists each vintage's `lifetime` (read from the LIVE
frame — the bound `df` predates the added rows) and clears its own
breakdown entries at the top of `apply_vintage_bounds`; `/copt` and `/mc`
carry `activity`; one chip per panel.

**Anchors.** E0: `snapshot_inputs` on six all-active fixtures hashed on
`6e40a7a` and after — byte-identical (`scratchpad/anchors12d`). D1, M1, M2,
RTS-79/RBTS unchanged.

**Hand values, measured.** F1: COPT 4.56 / 1.104 h, 148.8 / 37.44 MWh,
rows `base_a` 99.84 (= 76.8 + 23.04), `new` 27.84; MC at 2000 draws 4.6735
/ 1.0515 with both hand values inside their period CIs. F2 (alternating,
lifetime 10): breakdown 70 / 70 with `lifetime` 10.0 persisted, parent 140,
`capacity_by_period` (70, 70), residual 80/150, block `ok`, credit 35 per
period. F3 (lifetime 100, 2040 floor 20): 70 / 20, parent 90, series (70,
90), `partial` in 2030. E4 at margin 0.3: `elcc_mw = 83.203125` at draws 32
seed 1 and draws 128 seed 0.

**Bites (20 variants, 21 ★ incl. B12), all bite:** E1 (`<` for `<=`), E2
(single table), E3 (sampler scalar cap), E4+B12 (net without the series,
both fail), E4b (drop the credited check), E5 (store series ignored), E6
(aggregate everywhere), E6b (vintage only in its build period), E6c
(fallback `inf`), E6d (no consistency test), E6e (skip sourced entries),
E6f (clear behind the early returns — **first variant did not bite**: the
test removed the bounds through `delete_bounds_for_asset`, which already
drops the results entry; rewritten to wipe the bucket wholesale, then
bites), E7 (`"ALL"` mask all-True), E8 (loop hash without the series), E8b
(`baseline_key` without it), E9 (mean nameplate), E10a/b/c (fingerprint
without `active` / the breakdown / storage `build_year`), E12 (the margin
keeping its own six lines). Runner and log: `scratchpad/bites12d`.

**Gates on the final commit:** adequacy 580 passed; full tree 2815
passed / 43 failed, identical to master both ways; frontend 884/884 / 92
files, `tsc` clean; live S15–S27 green; S27 bitten live (single-table
`screening_analysis`: 2030 reads 1.104), restored by hash.
