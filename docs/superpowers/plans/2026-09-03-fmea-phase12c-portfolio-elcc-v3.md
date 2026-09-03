# Phase 12c — the portfolio ELCC as a second opinion on the reserve margin (plan, v3)

Supersedes v2 (`2026-09-01-fmea-phase12-elcc-v2.md`, rejected: six blockers)
and v1 (`2026-08-30-fmea-phase12-elcc-derating.md`, "not as scoped"). Both
are kept with their reviews. v3 is "step B" alone: the v2 review split the
phase, step A shipped as **Phase 12b** (the net-load window), and step B was
gated on the profile-discard **fix**, which shipped as **Phase 12c-pre**.
Every v2 blocker is answered by number in §0; the two premises that changed
underneath v2 are stated first because they change what the phase is.

## 0. What changed since v2, and what each blocker gets

**Premise 1 — the shadowed set is gone.** v2 §1 scoped the comparison to
`V ∩ M` and excluded a set `S` of profiled units with outage data, because
ELCC could not price them. 12c-pre attached the series to those units:
they are `kind="generator"` candidates (exclusion by position) with
nameplate `max_h(a_{i,h})`, and the margin credits them at `(1−q) × window
mean` of the same series (`elcc.py:unit_nameplate_mw`, `copt.py:_occurrence_profile`,
`solver_service.py:3525-3527`). There is nothing to exclude and nothing to
call "coverage".

**Premise 2 — the demand basis is inconsistent across surfaces, and that is
a finding, not a caveat.** `load_scalers` / `load_scalers_by_carrier`
multiply `loads_t.p_set` per period inside `_apply_modelling_assumptions`
and are reverted after the LP (`solver_service.py:160-187`, the undo
discipline at `5087-5090`). So the LP, the ENS cap and the reserve-margin
*constraint* see scaled demand; `snapshot_inputs` and `fleet_and_residual`
read raw `p_set` with no cfg in their signatures (`mc.py:175`,
`copt.py:fleet_and_residual`), so the MC study, the COPT, every ELCC row
and — the part that matters — **both certifying loops** (`results.py:3700,
3754, 4333, 4438`, all `snapshot_inputs(n, keep_zero_capacity=True)`) evaluate
the *unscaled* system. On a project with a 2035 growth factor of 1.25 the
coupling loop certifies a plan built for 125 % of demand against 100 % of
it. The preflight's margin reachability check has the same basis
(`reserve_margin_facts` from a route reads raw `p_set`). The router already
owns the one correct reconstruction, `lp_scaled_load_frame`
(`results.py:5209-5290`), used by the Results and Compare tabs so *they*
never diverge — the engines were simply never given it. **Recorded as the
fifteenth finding.** §1 fixes it as this phase's Part 0, because a
comparison of two numbers on two demand series is v1's BLOCKER 2 and v2's
SERIOUS 7 in a third form.

| v2 blocker | v3 |
|---|---|
| **1** — `V`, `M`, `S` were not the code's sets; capacity rules undefined; empty `V` on an expansion network indistinguishable from "no VRE" | One population, by the ENGINES' predicate (§2.1), with one capacity rule per side and a stated rule for their disagreement (§2.2); an empty population is its own status (§3.3), never `0/(0+0)` |
| **2** — the netting basis did not exist where it was computed | Step A's problem; 12b solved it by stashing at LP-build time. Not in this phase |
| **3** — A2 inverted | Step A's test; gone with step A. Part 0's pins are written so that a scaled network moves the *numbers* and the bite is the one that leaves them still (§5 D2) |
| **4** — A7 passed against its own bite | Replaced by the shipped `test_a_sum_of_marginals_can_OVERSTATE_the_portfolio` (bb760e6), whose bite was verified; v3 adds no additivity test |
| **5** — the bracket mechanism did not exist; and the correction: `max_h(Σ)` binds as a physical cap | Nameplate is `max_h(Σ)` **per period** for the dominance reason, and B3 is built where the cap *binds*, so the bite (horizon max) widens the bracket past the period's physical maximum and the assertion `elcc ≤ nameplate_period` fails |
| **6** — a bare boolean had nothing to sum; an empty population returned `ok 0.0` | The route computes the population and passes it to `snapshot_inputs(vre_assets=…)`; `elcc_of_portfolio` refuses an empty or zero-nameplate population before `elcc_of_removal` is called (§3.3, B5) |
| **S7** — load basis unstated | Part 0 (§1) |
| **S8** — step A plumbing | Not in this phase |
| **S9** — A1 duplicated a shipped test | Gone with step A |
| **M10** — the injected baseline could desynchronise CRN with no test | `baseline=` travels with a key the callee recomputes; a mismatch raises (§3.4, B7) |
| **M11** — storage/thermal rows reported a zero delta | The population here is profile-bearing generators only; storage is named as out of scope (§6) |

## 1. Part 0 — one demand basis for every engine

**The rule.** Every adequacy surface evaluates the demand the LP was built
against. One helper, `services/adequacy/demand.py::lp_demand_frame(n, cfg)`,
returns `loads_t.p_set` with the per-carrier / per-period scalers applied
(the logic now inline in `lp_scaled_load_frame`'s fallback branch, moved
there and called from it, so the router keeps one implementation and the
engines gain it). `cfg=None` or no scalers configured → the frame is
`p_set` itself, and the helper returns it **without copying**.

**The consumers.** `snapshot_inputs(n, *, cfg=None, …)` and
`fleet_and_residual(n, *, cfg=None, …)` take the frame from the helper in
place of `loads_t.p_set`; `reserve_margin_facts(n, cfg, …)` already has
`cfg` and builds its demand from the helper. Static `p_set` (no series
column) is scaled the same way. Every route that snapshots passes
`_state.get("solver_config")`: `/copt`, `/mc`, `/mc/elcc_candidates`, both
loops. Inside the solve wrapper the scaled values are already in place and
the helper is idempotent by construction: the wrapper passes `cfg=None`
(the frame is already what the LP sees), and D3 pins that the two paths
agree.

**What must not move.** With no scalers configured every engine is
bit-identical to today: pinned by hash on three surfaces (§5 D1). The loop
suites carry no scalers and pass unchanged.

**Cost of the choice, stated.** On a project with scalers the MC's LOLE
and every ELCC row *change* after this — upward, since demand grows. That
is the correction, not a regression; the PR body says so, and the coupling
loop's certified values on such projects are re-certified by re-running.

## 2. The population and the two capacity rules

### 2.1 One population, the engines' predicate

`P` = the generators the membership walk admits whose `p_max_pu` **series**
is informative (`copt.series_is_informative`: not identically 1 over finite
values). Exactly the union of

- must-take members with a preserved profile (`inputs.vre_profiles`,
  `kind="vre"`), and
- occurrence units carrying `profile` (`kind="generator"`),

which is the 12c-pre membership rule read back. A unit with an all-ones
column is in neither and is credited identically by both sides at
`(1−q)·cap`; a unit with a static `p_max_pu` only is in neither (the engines
do not apply it — 12c-pre §1.3). Storage is not in `P` (§6).

### 2.2 Two capacity rules, and the rule for their disagreement

- **ELCC side:** `solved_capacity` (`p_nom_opt` for an extendable — 0.0
  unbuilt — else `p_nom`), the engines' rule, as `snapshot_inputs` applies it.
- **Margin side:** the last solve's reserve-margin payload rows,
  `capacity_mw` from the vintage-aware `_built` (`report.py:149`).

They agree on every non-vintage network post-solve (`p_nom_opt` is what
`_built` reads when no vintage row exists). They **disagree** on a
vintage-expanded network, where the restore drops `wind@2030` and the
parent's `p_nom_opt` is not the built size — the recorded backlog item
"`solved_capacity` is not vintage-aware", which is an MC-wide defect and not
this phase's to fix. The comparison therefore checks, member by member,
`capacity_mw(payload) == solved_capacity` (rel 1e-9) and on any mismatch
returns `status="capacity_basis_mismatch"` naming the members and both
values, with **no number**. The same check catches a payload that
describes a network the user has since edited.

A member with `solved_capacity == 0` (unbuilt extendable) contributes
nothing to the removal and is listed in `population.unbuilt`, not priced.
If every member is unbuilt the status is `no_population`.

### 2.3 What is compared, per period

| field | source | definition |
|---|---|---|
| `credit_gross_mw` | margin payload rows for `P` in this period | `Σ derate × capacity_mw` |
| `credit_net_mw` | same rows | `Σ (derate_net if not None else derate) × capacity_mw`; **null** unless the period's `net_window.status == "ok"` |
| `nameplate_mw` | the snapshot | `max_{h ∈ period}(Σ_{i∈P} a_{i,h})` — the group's physical maximum in that period |
| `elcc_mw`, `elcc_share`, `status`, `reason` | `elcc_of_portfolio` | one bisection per period (§3.2) |
| `ratio_gross`, `ratio_net` | derived | `elcc_mw / credit_*_mw`, null when either side is null or zero |

The two credits are the margin's own numbers read back from its own
payload — not recomputed — so the comparison cannot drift from what the
panel already shows. When the last solve set no margin the block carries
`margin: null` and the ELCC rows alone.

## 3. The engine

### 3.1 `elcc_of_portfolio(inputs, members, *, seed, draws, baseline, baseline_key, …)`

`members` is `[(kind, name)]` over `P` with `solved_capacity > 0`. The
removal is the two-kind removal `elcc_of_removal` already supports in one
call: `reduced = replace(inputs, residual = residual + Σ_vre profile)` and
`exclude = {positions of the generator members}`. Returns one row per
period label (`inputs.periods`, "ALL" on a flat network).

### 3.2 Per period

The margin is enforced per period; a horizon-wide ELCC beside it is the
Phase-4 mistake (v2 Q2). `elcc_of_removal` gains `period=None`: when set,
the predicate compares `by_period[period]["lole_hours"]` and the floor is
that period's `min positive weight / n_samples`. Well-posed because a firm
block in every hour cannot raise any period's LOLE, the periods are
chronologically independent (storage SoC and outage states restart at the
boundary, `mc.py:_simulate_blocks`), and CRN holds per period as it holds
overall — `LOLE_P(Δ)` is a monotone non-increasing step function. The
horizon-wide predicate is the `period=None` path, unchanged, and the
existing tests pin it.

Cost: one baseline (shared, §3.4) plus one bisection per period; the panel
says "one bisection per investment period" where the draw count is chosen.

### 3.3 Refusals are data

`status ∈ {ok, unidentifiable, not_bracketed}` per period from
`elcc_of_removal`, plus two block-level statuses that never reach it:
`no_population` (nothing profile-bearing with built capacity) and
`capacity_basis_mismatch` (§2.2). A zero group nameplate in a period
(every member at 0 availability all period) is `not_bracketed` with a
reason, not `ok 0.0` — pinned (B5), because `elcc_of_removal(nameplate=0)`
returns `ok 0.0` today (v2 BLOCKER 6, verified).

### 3.4 The N+1 baseline, and keeping CRN honest

`elcc_of_removal` gains `baseline=None, baseline_key=None`. The route's
worker computes the headline `mc_adequacy` once and passes it to every
marginal row and to the portfolio with
`baseline_key = (draws, seed, cov_target, max_draws, batch, id-of-inputs-hash)`;
the callee recomputes the key from its own arguments and raises
`ValueError` on mismatch, so an injected baseline from a different sample
set cannot silently break the replay (v2 MINOR 10). Pinned bit-identical to
the self-computed path (B7). Saves one baseline per row — 9 % per marginal
on the v2 fixture — and is what makes "nearly free on top of a study that
already ran" true for the portfolio.

## 4. Route, payload, panel

- `McRequest.elcc_portfolio: bool | None`. The route derives `P` from the
  snapshot (vre names with profiles ∪ profiled units) — the snapshot is
  taken with `vre_assets` = all must-take names carrying an informative
  series, which is what `elcc_candidates` already enumerates.
- Result gains the sibling key `elcc_portfolio` (never inside `elcc`,
  v2 A9 kept as B8): `{status, population: {members: [{kind, name,
  capacity_mw}], unbuilt: [names], n_vre, n_generator}, margin_available:
  bool, periods: [{period, nameplate_mw, elcc_mw, elcc_share, status,
  reason, baseline_lole_h, credit_gross_mw, credit_net_mw, ratio_gross,
  ratio_net}], load_basis: "lp"}`; `None` when not requested.
- `GET /results/mc/elcc_candidates` unchanged.
- Panel: a checkbox beside the picker ("also price the profile-bearing
  fleet as one portfolio — one bisection per period"); a block under the
  ELCC table with one row per period and the sentence: *"The margin credits
  this group by its peak-window mean × (1 − q); the sampler prices the same
  group by the firm block that restores its own loss-of-load. Two
  standards, neither a correction of the other."* Forbidden words:
  "corrected", "true credit".

## 5. Acceptance (each ★ with a bite that can fail; restores by hash)

**Part 0**

★ **D1 — bit-identity without scalers.** `snapshot_inputs`,
`fleet_and_residual` and `reserve_margin_facts` on a two-period network
with no scalers hash identically to the values pinned on `52d4244`
(residual bytes, residual series, stash `demand_mw`). *Bite: copy-and-scale
by 1.0 through a float path that reorders the sum.* (If the bite does not
bite, the pin is recorded as a regression anchor and the bite as
non-biting.)
★ **D2 — a scaler moves the numbers in its period only.** With
`load_scalers = {"2035": 1.25}` the MC residual, the COPT residual and the
margin `peak_mw` are ×1.25 in 2035 and unchanged in 2030, on all three
surfaces. *Bite: apply the factor to every period; bite: leave the COPT
on raw `p_set`.*
★ **D3 — the wrapper path and the route path agree.** Inside a solve (scaled
in place, `cfg=None`) and from the route (raw + helper) the margin stash
`demand_mw` is identical. *Bite: apply the helper inside the wrapper too
→ ×1.25².*

**The engine**

★ **B1 — per-period portfolio, exact.** A two-period fixture where the two
farms overlap in 2030 (both hours 0–9) and not in 2035 (A 0–9, B 10–19):
per-period ELCC equals the hand values (2035: the non-overlap 100 MW cap;
2030: the overlap value). *Bite: compare the horizon LOLE for every period
→ both periods report one number.*
★ **B2 — the floor is per period.** Period 2030 with no shed hour and 2035
with several: 2030 `unidentifiable`, 2035 `ok`. *Bite: use the horizon
floor and LOLE.*
★ **B3 — nameplate is the period's physical cap, and it binds.** On the B1
fixture the 2035 credit saturates at `max_{h∈2035}(Σa) = 100`; asserted
`elcc_2035 ≤ 100 + tol`. *Bite: `max_h` over the horizon (200 in 2030's
overlap hours) → the bracket widens and the located credit exceeds 100.*
★ **B4 — mixed kinds.** One must-take farm and one profiled occurrence unit
(q = 0.05 on the same profile): the removal un-nets the first and excludes
the second; equals the hand value. *Bite: drop the `exclude` → the second
stays in the fleet and the credit is the first farm's alone.*
★ **B5 — refusals.** Empty population → `no_population`; zero nameplate in
a period → `not_bracketed` with reason; neither calls the bisection
(counted). *Bite: pass through to `elcc_of_removal(nameplate=0)` → `ok
0.0`.*
★ **B6 — capacity basis.** A payload row saying 70 MW beside
`solved_capacity = 0` (the vintage shape, built by hand on `n.meta`) →
`capacity_basis_mismatch` naming the member and both values, no number.
*Bite: drop the check → `no_population` (the 0 MW member is dropped).*
★ **B7 — the injected baseline.** Rows with `baseline=` are bit-identical
to rows without; a `baseline_key` for a different seed raises. *Bite: drop
the key comparison.*
★ **B8 — not in `elcc`.** `sum(r["elcc_mw"] for r in result["elcc"])` is the
member sum only. *Bite: append the block's row to `rows`.*
★ **B9 — the route.** `elcc_portfolio: true` → block present with the
period rows; absent/false → `None`; `margin_available` reflects the last
solve. *Bite: always compute.*
★ **B10 — the comparison reads the payload.** `credit_gross_mw` equals
`Σ derate × capacity_mw` over the payload rows of `P` only. *Bite: sum
every row → the thermal fleet is included.*
★ **B11 — membership pin.** On the 12c-pre M1 fixture `P` = {wind_for,
hydro_const, wind_mt}; not gas_static, not gas_ones. *Bite: admit any
member with a column → gas_ones.*

**Live — S25.** A two-farm network (one must-take, one with outage data)
with a reserve margin, solved over HTTP; `POST /results/mc` with
`elcc_portfolio: true`; the block's `credit_gross_mw` equals the served
margin payload's sum over the two farms, `elcc_mw` is `ok`, and the row is
not in `elcc`. *Bitten live: drop the population's generator half → the
occurrence farm is not removed and the credit halves.*

## 6. Out of scope, stated

- Storage in the portfolio (its margin credit is a duration haircut, not a
  profile; a mixed group would compare a haircut to a bisection).
- Changing the constraint's coefficients from ELCC (v1 §2's reasons stand;
  an allocation rule is its own design).
- `solved_capacity` vintage-awareness (backlog; refused, not hidden, §2.2).
- The static-CF flag and the margin derate's NaN rule (backlog).

## 7. Open questions for the review

1. **Part 0's scope.** Fixing the demand basis engine-wide is the honest
   resolution and changes MC numbers on every scaled project (correctly).
   The alternative is a `load_basis_mismatch` refusal on the comparison
   only, leaving the loops certifying against the wrong demand. Is the
   wider fix right for this phase, or its own item first?
2. **`capacity_basis_mismatch` vs fixing `solved_capacity`.** Refusing is
   honest; the fix is small in `copt.solved_capacity` (read
   `n.meta["vintage_results"]` as `_built` does) but changes the MC on every
   vintage network. Same question.
3. **Per-period ELCC.** Is the per-period predicate (§3.2) well-posed as
   argued, and is `min positive weight in the period / n` the right floor?
4. **The ratio.** Is `elcc / credit` the right single number for the panel,
   or should the block show only the three MW figures?

---

## v3 REVIEW OUTCOME — reject as written; v3.1 amendments below (2026-09-03)

Verdict: **reject as written → v3.1**: no section needs redesign; rules and
tests do. Sixteen findings; every one re-run against the code before being
recorded (`scratchpad/v3_p{1..6}*.py`). The fifteenth finding was
**confirmed end to end** by the reviewer's own probe: on a two-period
network with `load_scalers = {"2035": 1.25}`, route-side `snapshot_inputs`,
`fleet_and_residual` and `reserve_margin_facts` all read the raw peak
(179.93) while the solve-time wrapper stashes 224.92 and `p_set` is
restored bit-identical, so the post-solve snapshot the loops evaluate
equals the pre-solve raw residual. No engine reads `loads_t.p`. Both loops
call `snapshot_inputs(n, keep_zero_capacity=True)` on the restored network.

### Findings, verified

1. **BLOCKER — Part 0's rule was inverted for static `p_set`.**
   `_apply_modelling_assumptions` scales only `loads_t.p_set` *columns*,
   gated on `cfg.multi_investment_periods`, a MultiIndex and a non-empty
   frame (`solver_service.py:5313-5320`); a static `loads.p_set` is never
   scaled, and `lp_scaled_load_frame` returns `None` for a static-only
   network. So static loads are on ONE basis everywhere today, and §1's
   "static `p_set` is scaled the same way" would have created a mismatch on
   every static-load network — including every reserve-margin test fixture.
   **The helper must reproduce the LP's behaviour verbatim**, not improve
   on it.
2. **SERIOUS — the engines ignore the activity mask.** `_membership_walk` /
   `fleet_and_residual` never consult `build_year`/`lifetime` (no
   `get_active_assets` in copt.py or mc.py), while `reserve_margin_facts`
   masks per period (`solver_service.py:3509`). Measured: a must-take farm
   with `build_year=2035` is netted into the **2030** residual and has no
   2030 margin row. Pre-existing and MC-wide; §2.2's "rule for their
   disagreement" cannot see it because there is no row to compare.
3. **SERIOUS — §2.1's population is not "the engines' predicate".**
   `elcc_candidates` admits every must-take with `peak > 0`
   (`elcc.py:185-193`) and `snapshot_inputs` builds a profile from the
   column whether or not it is informative, or from the STATIC `p_max_pu`
   when there is no column (`mc.py:264-274`); `fleet_and_residual` nets a
   must-take's static value too. Measured: candidates include
   `wind_ones_col`, `wind_static09`, `wind_nocol`, none informative. The
   population needs the plan's OWN filter on both halves.
4. **SERIOUS — §2.2's vintage mechanism was misstated.** The restore writes
   the parent's `p_nom_opt` as the aggregate over vintages
   (`vintage_service.py:747`), so `solved_capacity(wind) = 70 = built`; the
   payload's shape is `wind: 0.0` + `wind@2030: 70` + `wind@2040: 0`. A
   member-by-member check fails as 0 ≠ 70 for the wrong reason; a by-parent
   aggregate agrees in the common case. B6's fixture was the inverse of the
   real shape.
5. **SERIOUS — B3's bite does not bite.** Per-period dominance guarantees
   the top probe at `nameplate_P` satisfies, so the located step edge is
   ≤ `nameplate_P` whatever the bracket; measured 100.0 with bracket 100 and
   with bracket 200. `max_h(Σ)` per period stays the ceiling for the
   dominance reason; B3 is an anchor, not a bitten ★.
6. **MODERATE — the wrapper cannot pass `cfg=None`**:
   `_wrap_with_reserve_margin` calls `reserve_margin_facts(n, cfg, …)` and
   needs `cfg` (`solver_service.py:3693`). Applying the router's scaling on
   the in-place-scaled frame measured 281.15 = 1.25² × raw. An explicit
   switch is needed.
7. **MODERATE — the router's fallback branch is not the LP's resolution**:
   no `multi_investment_periods` gate (measured: `mip=False` → LP unscaled
   179.93, router 224.92), `f == f` vs `math.isfinite`, carrier fallback
   `"electrical"` vs `"unspecified"`. The factor resolution must be
   extracted from `_apply_modelling_assumptions` and used by all three.
8. **MODERATE — `/copt` takes no lock** (`results.py:5020-5021`) and can read
   the in-place-scaled frame mid-solve: a transient inconsistency today, a
   double-scaling after Part 0.
9. **MODERATE — the worker must not read `_state`** (request-scoped;
   `post_mc` closes over `record` for this reason, `results.py:3327-3331`).
   The margin payload is `_state.get("last_reserve_margin")`
   (`results.py:5124`, emitted at `solver_service.py:1362`), sanitised, with
   `capacity_mw` possibly `None`; it must be captured in the request before
   `Thread.start()`.
10. **MODERATE — cost.** Measured 22–23 `mc_adequacy` calls for a two-period
    portfolio (11–12 per period) vs 11 horizon-wide, every call over the
    full horizon; "nearly free on top of a study that already ran" is false
    for the per-period design. The Δ = 0 probe can be shared across periods.
11. **MODERATE — `not_bracketed` was re-purposed** for a no-op removal;
    measured: `elcc_of_removal(nameplate=0)` on a no-op removal returns
    `ok 0.0`, on a real removal `not_bracketed`. A distinct status is needed.
12. **MINOR — `baseline_key`** omitted `**sim_kwargs` (`initial_soc_frac`,
    `storage_enabled`, `exclude_storage`) and `id()` is not a content hash.
13. **MINOR — staleness**: a `p_max_pu` edit after the solve is not caught by
    the capacity check; the report carries hashes from Phases 10/11.
14. **MINOR — line references**: the scaling is `solver_service.py:5307-5374`
    (not 160-187, which are field comments); the `p_set` restore closure
    `5371-5374`; preflight is `validate_for_run` at `813`, before the
    transforms at `987`.
15. **MINOR — D1's bite**: ×1.0 is exact in IEEE-754; only a reordered sum
    over ≥ 3 loads bites. D1 is a regression anchor.
16. **MINOR — Q4**: a ratio invites the "correction" reading the copy
    forbids.

Verified true (no action): the fifteenth finding (above); `by_period` LOLE
is the per-draw mean of per-period weighted shed hours, monotone
non-increasing in Δ per period (measured on [0, 200]), SoC and outage states
restart per block; the per-period floor is `resolution_floor_h`'s
definition restricted; B1's bite bites (horizon predicate → 2035
`not_bracketed`), B2 verified (2030 `unidentifiable`, 2035 `ok`), B4's bite
bites (100.0 without `exclude` vs 130.08 mixed); un-net + exclude in one
call is supported without double counting and CRN holds; `post_mc`'s
baseline is bit-identical to `elcc_of_removal`'s own (same defaults
`max_draws=2000, batch=250`); `solved_capacity == payload capacity_mw` on a
plain extendable network post-solve; B11's population on the M1 fixture is
`{hydro_const, wind_for, wind_mt}`; `loads_t.p` IS scaled post-solve and
`lp_scaled_load_frame` prefers it, so the helper must be fed `p_set`.

Reviewer's answers to §7: Q1 — Part 0 is its own item, first. Q2 — refuse by
parent-aggregate mismatch; do not fix `solved_capacity` here. Q3 — the
per-period predicate is well-posed and the floor is right. Q4 — the three MW
figures; the ratio only beside the two-standards sentence, or not at all.

### v3.1 amendments

**Sequencing.** Part 0 ships first as its own item (**12c-0**), with its own
gates and its own shipped-code review, before 12c's code is written.

**A1 — Part 0 is the LP's behaviour verbatim (findings 1, 6, 7, 8, 14).**
The per-(period, carrier, column) factor resolution is extracted from
`_apply_modelling_assumptions` into `services/adequacy/demand.py::load_scale_factors(n, cfg) -> dict[(period, col), factor]`
(gated exactly as the LP is: `cfg.multi_investment_periods`, a MultiIndex,
a non-empty `loads_t.p_set`, `math.isfinite`, the LP's carrier fallback) and
`lp_demand_frame(n, cfg) -> DataFrame | None` applies it to `loads_t.p_set`
columns only — a static `loads.p_set` is untouched, as the LP leaves it.
`_apply_modelling_assumptions` calls `load_scale_factors` (one resolution,
D3 by construction) and `lp_scaled_load_frame`'s fallback branch calls
`lp_demand_frame`. The consumers — `snapshot_inputs`, `fleet_and_residual`,
`reserve_margin_facts` — take `cfg=None` and a keyword
`demand_scaled_in_place: bool = False`; the solve wrapper passes
`demand_scaled_in_place=True` with its `cfg`, so the helper is skipped
there and the switch is explicit. `/copt` takes the network lock like
`/mc`. Every snapshotting route passes `_state.get("solver_config")`.
Consumer census recorded: after Part 0 the LP, ENS cap, margin constraint,
DSR sizing and stress sweep are on the LP basis as before; `/mc`, `/copt`,
`elcc_candidates`, both loops, preflight `_check_reserve_margin` and the
margin loop's `reserve_margin_facts` move from raw to the LP basis;
`asset_results` load metrics stay as they are (not adequacy).

**A2 — Part 0's pins.** D1 is a *regression anchor*: hashes of the MC
residual bytes, the COPT residual and the stash `demand_mw` on a two-period
SERIES-load network with no scalers, pinned on the pre-change code. D2 uses
a series load (`loads_t.p_set` column): with `{"2035": 1.25}` the three
surfaces scale in 2035 only; bites: every period; skip the COPT. D3: the
wrapper path (`demand_scaled_in_place=True`) and the route path give an
identical stash `demand_mw`; bite: run the helper inside the wrapper too
(×1.25²). D4 (new): a static-load network is unchanged on every surface
with scalers configured; bite: scale the static value. D5 (new): `/copt`
under the lock — a concurrent solve cannot be observed mid-transform
(pinned by a test that holds the lock and asserts the route blocks).

**A3 — the population is filtered here (finding 3).** `P` = names the walk
admits whose `p_max_pu` COLUMN is informative, for both halves — the plan's
own filter, applied to the must-take candidates (`series_is_informative`
on the column; a must-take with only a static value or no column is not in
`P`, though it remains an ELCC candidate on its own) and to the profiled
units (already the engine's rule). B11 stands. A member ≡ 1 within a
period or NaN-only in a period is consistent on both sides (measured:
margin `constant/d=1.000` and `constant/d=0.000`; engine nameplate 0).

**A4 — activity, and the two capacity rules (findings 2, 4, 13).** Per
period, the comparison first checks membership: a `P` member with no
payload row in the period (inactive for the margin, present for the
engines), or a payload row for a name absent from the snapshot, is an
`activity_mismatch` refusal naming the members. Then capacity, **by
parent aggregate per period**: payload rows are grouped by
`name.rpartition("@")[0]` and summed; the sum must equal
`solved_capacity(parent)` (rel 1e-9, `None` treated as mismatch), else
`capacity_basis_mismatch`. B6 is built on the shipped `_vintage_network`
(both statuses reachable: aggregate 70 = 70 passes; a later vintage in an
earlier period is the activity case). The engines' activity blindness is
recorded in the hardening backlog beside `solved_capacity`. Staleness: the
block also carries the report's network hash and refuses (`stale_report`)
when it differs from the snapshot's.

**A5 — statuses (finding 11).** Block-level: `ok`, `no_population`,
`activity_mismatch`, `capacity_basis_mismatch`, `stale_report`,
`margin_unavailable` (no margin on the last solve — the ELCC rows still
run). Per period: `ok`, `unidentifiable`, `not_bracketed`, and
`no_contribution` (Σa ≡ 0 in the period — a no-op removal, reported as
such, never `ok 0.0` and never `not_bracketed`).

**A6 — cost and copy (findings 10, 16).** Cost is stated as
`n_periods × ~10 full evaluations` plus one shared baseline; the Δ = 0
probe is evaluated once for all periods; "nearly free" is withdrawn. The
panel sentence quantifies like the per-asset one. The block shows the
three MW figures per period; no ratio.

**A7 — baseline injection (finding 12).** `baseline_key` = the loops'
`_hash` shape over `MCInputs` (units incl. profile bytes, storage,
residual bytes) plus `(draws, seed, cov_target, max_draws, batch)` plus the
sorted `sim_kwargs` items; mismatch raises.

**A8 — tests.** B3 demoted to an anchor inside B1. B5 gains the
`no_contribution` case. B6 on `_vintage_network`. B12 (new):
`activity_mismatch` on the `build_year=2035` fixture. B13 (new):
`stale_report` after a `p_max_pu` edit.

**A9 — where the payload is read (finding 9).** `post_mc` captures
`_state.get("last_reserve_margin")` and the report hash in the request and
closes over them; the worker never touches `_state`.

---

## 12c-0 SHIPPED — one demand basis (2026-09-03)

Built per v3.1 A1/A2. `services/adequacy/demand.py` owns the resolution
(`load_scale_factors`, `lp_demand_frame`, `demand_frame_for`);
`_apply_modelling_assumptions` step 5 applies it in place;
`lp_scaled_load_frame`'s fallback calls it (the router's inline copy, with
its three divergences, is gone); `fleet_and_residual`, `snapshot_inputs`,
`elcc_candidates` and `reserve_margin_facts` take `cfg` and the explicit
`demand_scaled_in_place` switch; the solve wrapper passes the switch; `/mc`,
`/mc/elcc_candidates`, `/copt` and both loops (initial snapshot and the
worker's per-iterate re-snapshot, config captured in the request) pass the
config; `/copt` takes the mutation lock.

**Tests** (`tests/test_adequacy_demand_basis.py`, 10): D1 anchor (three
hashes pinned on `52d4244`; the no-scaler path returns the frame itself),
D2, D3, D3b (a real HiGHS solve: stash 1.25× once, `p_set` restored,
the loops' snapshot on the same basis), D4, the gate (parametrised on
`multi_investment_periods`), finiteness, per-carrier precedence, D5.

**Bites** (restores by hash): D2 ×2, D3 (ignore the switch), D3b (wrapper
drops the switch), D4, gate, D5 — **7/7 bit**. One first attempt did not
bite and is recorded: biting the wrapper's call against D3 could not fail
it, because D3 calls the facts directly and never passes through the
wrapper; that exposed the wrapper's switch as unpinned, D3b was added to
pin it through a real solve, and the D3 bite was moved to the switch
itself.

**Gates:** targeted regression (routes, loops, reserve margin, results
range, golden coverage) 296 passed; live S15–S24 and the full tree recorded
in the commit message. No live suite for the basis itself, and the QA plan
says why (a per-period load series cannot be set over the API).

**Recorded, not fixed here:** the engines' activity blindness (v3 review
finding 2) and `solved_capacity`'s vintage blindness stay on the backlog;
12c refuses on both rather than comparing across them.

### 12c-0 SHIPPED-CODE REVIEW (2026-09-03, on `a627e56`) — accept with fixes

Verdict **accept with fixes**, docs-only for the code. The reviewer ran the
OLD `_apply_modelling_assumptions` from a detached worktree of `ca37368`
against the new one on 6 networks × 15 scaler configurations: in-place
`p_set`, static `p_set`, restore identity and the log line identical in
**90/90** cases (its first pass had silently imported the new code through
the fixture's `sys.path` and was thrown away — recorded by the reviewer).
D1's anchor re-derived on the old tree; the bites reproduced 6/6 by
monkeypatch; the loops' snapshots verified to carry the config
(`base_cfg` precedes both closures; per-iterate configs are
`dataclasses.replace(base_cfg, …)`); a live loop run with the scaler
differs from the run without (margin loop 1.375 h vs 1.6875 h at the same
margin; the coupling loop meets in one iterate without and exhausts its
budget with) and the wrapper's stash peak equals the post-loop snapshot's
peak in every scaled run; the consumer census confirmed with no raw reader
left where the LP is scaled; D5 proves the only case that exists (a
background solve runs on a different context's network and lock).

Findings, verified and acted on:

1. **MODERATE — the QA plan's "no honest live reproduction" was false.**
   `POST /api/network/loads/upload_profile` replicates a flat upload across
   the periods (`routers/network.py:3704`, `_apply_profile_upload`), which
   is exactly the multi-period column the scalers gate on. **Fixed:** the
   note is replaced by a live suite, **S25**, that uploads a 24 h ramp,
   sets a 2035 factor of 1.25 over HTTP, solves, and checks `/results/copt`
   per period against a four-state hand table on each basis (2.31 h raw,
   4.11 h scaled) and the margin's peaks (123 / 153.75). Bitten live: with
   `/copt` on the raw frame the 2035 value collapses to the 2030 one.
2. **MODERATE — `lp_scaled_load_frame`'s fallback differs from the old
   router in four ways; two were unstated:** it now returns the live
   `p_set` itself when nothing scales (the old copy was made whenever any
   scaler was configured), and the carrier-map build no longer degrades to
   `"electrical"` on a pathological `loads` frame. Every consumer is
   read-only (verified). **Fixed:** both docstrings say the frame may be
   the live input and must never be mutated.
3. **MINOR (pre-existing, adjacent) — a margin-less solve did not clear
   `last_reserve_margin`** when driven through `run_simulation` directly
   (the loops' path; the HTTP route clears it at start,
   `routers/simulation.py:597`). Observed live by the reviewer: after a
   scaled margin run, a no-scaler coupling run still served the 224.92
   peak. **Fixed** in `run_simulation` (★ D6, bitten).
4. **MINOR — `/copt` read `must_take_generators` after releasing the
   lock.** **Fixed:** under the same hold.
5. **MINOR — the per-period mask was recomputed per column** (measured
   3.824 s vs 3.849 s on 300 × 3 × 6000: no regression, the `.loc`
   dominates). **Fixed:** hoisted, in both the LP and the helper.
6. **MINOR (pre-existing) — the `validate` route's TOCTOU**: it guards on
   `_solver_in_flight()` rather than the lock, so a solve starting in the
   millisecond window would let the route's `reserve_margin_facts` scale
   an in-place-scaled frame. Recorded; the route avoids the lock by design.

**A harness lapse, recorded.** The live S25 bite was restored with
`git checkout -- routers/results.py`, which reverted the file to the last
COMMIT and discarded the uncommitted fixes 2 and 4 in it; the hash check
reported the mismatch and both were re-applied from their patch. Restores
are by saved copy and hash — the unit-bite rule — for live bites too.

Gates after the fixes recorded in the commit message.
