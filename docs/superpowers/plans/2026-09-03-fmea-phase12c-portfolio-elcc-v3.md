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
