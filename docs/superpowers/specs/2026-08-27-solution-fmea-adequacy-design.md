# Design — Solution FMEA & the Cost-vs-Availability Trade-off (v4)

**Status:** assessment / design. No feature code written.

**Revision history.** v1 (`d445f83`) was reviewed adversarially on two axes — the
reliability/optimisation mathematics, and every claim it made about the codebase. That
review found v1's central technical proposal wrong and about a third of its "already
exists" inventory overstated; v2 (`41dd9c7`) recorded the corrections. **v3 scopes the
feature** after a round of product decisions that materially shrink it: the trade-off
curve moves from the core to an on-demand extra, and availability is scoped to
electricity. **v4 pins the four second-order decisions** — cap geometry, demand response,
VoLL structure and the occurrence statistic — each of which turned out to touch more of
the codebase than its one-line answer suggests.

**Goal.** Let a user state a reliability target, get a least-cost plan that meets it, and
see a ranked, model-computed account of which failure modes drive the residual risk —
with the cost-vs-availability curve available on demand for users who want to see the
whole trade-off rather than one point on it.

---

## 1. What this feature is, and what it is not

The single most common confusion about this work, worth stating before anything else:

- **The cost-vs-availability trade-off is a parametric optimisation study.** Re-solve the
  expansion problem at a series of reliability levels; plot the results. It produces a
  **curve**. It is not FMEA and does not require FMEA.
- **FMEA is the diagnostic decomposition underneath a single plan.** It answers *why*
  availability is what it is and which assets are responsible for the remainder. It
  produces a **ranked table**. It does not produce a curve.

They are complementary, and "solution FMEA" means both — but only one of them is the
trade-off. The link between them is the point of the name: **the FMEA is conditional on
the design.** Each point on the curve is a different plan with a different criticality
ranking; build more storage and the ranking shifts away from thermal outage toward
transmission. You are doing FMEA *on a solution*, so every candidate solution has its
own.

### Constraint or multi-objective? Same problem, two parameterisations

- **Constraint (ε-constraint) form:** `min cost` s.t. `ENS ≤ Ē`. Set a target, get a plan.
- **Weighted form:** `min cost + VoLL × ENS`. VoLL is the weight on the second objective.

These are Lagrangian duals; sweeping either traces the same Pareto frontier. **The
constraint form is primary**, because it is the better way to obtain multi-objective
results: the weighted form recovers only the convex hull, and it is degenerate — VoLL
changes the solution only at breakpoints, so ten VoLL values commonly yield four distinct
plans, clustered, with jumps between them. The ε-constraint sweep samples the curve
uniformly by construction.

---

## 2. Decisions taken

| Question | Decision |
|---|---|
| Deliverable shape | Model-computed occurrence/severity in a formal worksheet |
| Rigour | LP-derived proxy acceptable; Monte Carlo not required to ship |
| Runtime | Desktop-safe by default; PRAS/Antares handoff optional |
| Failure modes | All four classes |
| Headline availability metric | Both LP proxy and COPT side by side, neither headline; their divergence is the diagnostic |
| Worksheet conformance | IEC 60812 FMECA — €/yr criticality is the ranking. No RPN, no Action Priority |
| Two pre-existing bugs | Fixed on `claude/fix-lost-load-cost-and-custom-attr-drop` (`8e2f98d`), off `master` — not carried here |
| Class C data | Funded — bundle reference climate years |
| **Target metric** | **Set either an energy cap or a shed-hours target; always report both** (§5.1) |
| **Cost axis** | **Total system cost — CapEx + FOM + fuel + CO₂ — excluding shed cost** (§5.2) |
| **Product shape** | **Target first; the curve is an on-demand action, not the core loop** (§6, §8) |
| **Carrier scope** | **Electricity only** (§4.3) |
| **Cap geometry** | **Global cap + a looser per-zone ceiling**, zone = bus `country` (§5.1) |
| **Demand response** | **Separated from involuntary shedding** — DSR is a resource, not unserved energy (§4.4) |
| **VoLL structure** | **Single value now, schema shaped for segments later** (§5.5) |
| **Occurrence statistic** | **Accept FOR or EFORd, label which was entered, never silently convert** (§5.4) |

---

## 3. What v1 got wrong

Recorded because the errors are instructive, and because a reader of v1 needs to know
which parts not to build.

### 3.1 The COPT proposal was circular

v1 proposed convolving a Capacity Outage Probability Table against "the residual
load-duration curve the LP already produces", and called the result a genuine LOLE.

That curve is a **decision variable**, not a load model. `residual = load − VRE −
storage_discharge + storage_charge − net_imports` was chosen conditional on every unit
being available. Convolving outage states over it:

1. **freezes storage into its outage-free schedule** — it cannot respond to the outage
   being convolved, biasing LOLE **up**;
2. **inherits the LP's perfect foresight**, which pre-positioned that storage optimally,
   biasing **down**;
3. **nets VRE off deterministically**, destroying its variance and biasing **down** again.

Three unbounded errors in two directions. The result is not a bound and cannot be called
genuine. Worse, v1 called it "probabilistic, not proxy" while elsewhere correctly warning
that perfect-foresight LP output understates LOLE — a self-contradiction on a number
built from that very output.

**Correct construction (§5.3):** COPT over dispatchable thermal only, applied to a
residual curve netting **only exogenous must-take** generation at its given availability
— no storage, no imports, no LP dispatch decisions — with VRE entered as **multi-state
capacity**, not a deterministic subtraction.

### 3.2 The affordability argument was built on the wrong constraint

v1 asserted solves are serialized because netCDF/HDF5 is process-global thread-unsafe,
and concluded Monte Carlo is "categorically out of reach… forced by the process model".

Thread-unsafety forbids **threads, not processes**. The codebase's own docstring
(`services/solve_queue.py:28-30`) says the HDF5 hazard is handled by `_netcdf_io_lock`
around I/O only; `_run_job` holds no HDF5 lock across `run_simulation`
(`solve_queue.py:427-431`), and the LP itself (linopy → HiGHS) touches no HDF5.
Sequential dispatch is a design choice.

The real blockers on parallelism are in §7.2. The Monte Carlo conclusion survives — but
on **runtime** grounds (each LP takes minutes; 10³–10⁴ replications is 3–4 orders of
magnitude away, and 8-way parallelism buys 8×), not on the process model. v1 also
strawmanned SMC as replications *of the investment LP*; PRAS and Antares run a
lightweight dispatch evaluation per replication, which is why they are fast.

### 3.3 The contingency sweep was the wrong primary engine

v1 spent 30–60 LP re-solves computing `ΔEUE_i` per asset. That measures Birnbaum
importance **evaluated at the all-others-available point** — the first-order term of a
risk expansion, with every higher-order term dropped. For generation adequacy those
higher-order terms are not a correction, they are the phenomenon: loss of load in a
system with any reserve margin arises almost entirely from **coincident** outages during
high net load. Two failure modes follow:

- healthy margin → nearly every single-unit `ΔEUE` is exactly **zero**, and the whole
  FMECA table is zeros with no ranking signal;
- thin margin → the ranking is dominated by unit size, which you knew without 60 solves.

Once the COPT exists, the correct importance measure is **analytic and free**:
`LOLE(COPT) − LOLE(COPT deconvolved of unit i)`, a proper Birnbaum importance over the
**full multi-outage state space**, capturing N-2 and beyond, at one extra convolution per
unit. This **collapses the class-A sweep to zero solves**.

### 3.4 `s_nom → 0` is a modelling bug for AC lines

Under PyPSA's KVL/cycle formulation the line's reactance still enters the cycle
constraints, so you get a zero-capacity-but-still-present branch: susceptance handling
degenerates, PTDFs are unchanged, and the answer is silently wrong. A line outage
requires **removing** the branch so PyPSA recomputes `SubNetwork` cycles — or using the
LODF/contingency machinery, which this repo already ships (§6.2).

### 3.5 Smaller corrections

- **λ naming.** `8760·FOR/MTTR` is arithmetically correct as expected events per year,
  but it is the **cycle frequency**, not the failure rate `1/MTTF` that `λ` conventionally
  denotes. They differ by `(1−FOR)` — immaterial at FOR 0.05, 30% at FOR 0.3. Rename `f_i`.
- **MTTR cancels.** `C_i = f_i·S_i = (8760·FOR_i/MTTR_i)·(MTTR_i·d) = 8760·FOR_i·d`.
  Criticality is `unavailability × annual hours × damage rate`, so the entire answer rests
  on **where in the year the outage window is placed** — a free parameter v1 never
  specified. Combined with v1's "restrict to peak-risk hours", it asserted every event
  lands on the annual peak.
- **Group rows were a category error.** v1's "one row per (carrier × region)" inherits a
  per-unit `f_i` while zeroing a whole group — a common-cause event whose independent
  probability is `Π p_i ≈ 0`.
- **"EENS" was not an expectation.** What the LP produces is ENS for **one** weather
  year, one availability realisation, with perfect foresight.
- **The ACER conflation.** v1's first-order condition is correct for `min C(x) +
  VoLL·E(x)`, but ACER's reliability standard is `RS = CONE/VoLL` in **LOLE hours/yr**,
  not EUE. They coincide only if the marginal peaker displaces exactly its nameplate in
  every loss-of-load hour.
- **Severity double-counting.** `S_i = ΔEUE·VoLL + Δopex` double-counts if `Δopex` is
  Δ(objective): the slack generators dispatch at marginal cost `voll` and their cost
  already lands in the economics under `carrier == "load_shedding"`.
- **PRAS licence risk is closed** — modified MIT (Expat). Note PRAS also ships analytical
  convolution methods, so a DIY COPT partly duplicates it.

---

## 4. Scope

### 4.1 Failure-mode taxonomy

| Class | Representation | Occurrence | Severity |
|---|---|---|---|
| **A. Generation forced outage / derating** | COPT state, **not** an LP re-solve | `forced_outage_rate` + `mttr_hours`; multi-state for derating | Analytic leave-one-out on the COPT (§5.3) |
| **B. Transmission / link outage** | **Existing SCLOPF path** for Line/Transformer; new `p_nom → 0` for **Link** only | Per-branch FOR | LP re-solve — genuinely needed here |
| **C. Correlated weather + demand extreme** | Whole-year swap: availability *and* load together | Empirical frequency of the climate year | LP re-solve per year |
| **D. Fuel / cyber / human-operational** | Qualitative worksheet row; optional model-backed proxy | Expert-entered | Expert-entered, or the proxy re-solve |

Class C is funded: bundle a reference climate-year set so it works out of the box rather
than degrading to whatever a user uploads. It needs the §6.3 multi-year storage work
first — the data is useless without somewhere to put it.

Class D is why the formal worksheet exists: these modes are not quantifiable from the
network and must sit beside the computed rows without pretending to be them.

**Not modelled, each materially affecting the answer:** planned/maintenance outages
(excluded from FOR by construction, yet dominant in real unavailability), load-forecast
uncertainty, and interconnector availability — the most contested input in European
adequacy studies.

### 4.2 What the user gets

Per plan:

1. **A ranked worksheet** — one row per failure mode: occurrence (events/yr), severity
   (MWh unserved and € per event), criticality (€/yr), mitigability.
2. **An attribution** — "of the expected unserved energy, X% traces to these N assets."
3. **Achieved reliability** — energy unserved *and* shed-hours, against the target.
4. **On demand:** the cost-vs-availability curve, and how the ranking reshuffles along it.

### 4.3 Carrier scope: electricity only

The target, the cap and the reported metrics cover **electrical load**. The ENS cap sums
only over slack generators at buses whose carrier is electrical. This matches how
adequacy standards are written and avoids blending a shed MWh of heat with a shed MWh of
electricity as though they were equivalent.

**Two consequences that must be visible in the UI, not buried here:**

- **Sector-coupled flexible loads become adequacy *resources*.** An electrolyser or heat
  pump outage *reduces* electrical demand, so on an electricity-only metric its failure
  scores as an **improvement**. Such rows must be rendered as "out of scope for this
  metric", never as negative criticality — otherwise the worksheet recommends breaking
  the electrolyser.
- **Non-electrical service failure is invisible.** An H₂ or heat shortfall does not appear
  at all. If that matters, the scope decision has to be revisited; it is not a gap that
  can be papered over in reporting.

### 4.4 Demand response is not unserved energy

Today a single VOLL slack per load-bearing bus represents *everything the model could not
serve at normal cost* — contracted demand response and involuntary curtailment alike.
That conflation overstates unserved energy wherever DSR is modelled and makes flexibility
look like a liability, so the two are separated:

| Tier | Carrier | Price | Counts against the target? |
|---|---|---|---|
| Voluntary response | `demand_response` | contracted compensation, well below VoLL | **No** — it is a resource |
| Involuntary curtailment | `load_shedding` | VoLL | **Yes** |

Only the involuntary tier feeds the availability target (§5.1) and FMEA severity (§5.4).

**Two hazards this creates, both of which have bitten similar changes before:**

1. **Double-counting against modelled flexibility.** If a network already represents DSR
   as a real asset — a load-shifting Link, or a Generator with a demand-response carrier —
   then a DSR slack tier on the same bus counts the same flexibility twice. The DSR tier
   must be opt-in per bus (or suppressed where a flexible asset is present), with a
   preflight warning, not switched on globally.
2. **The slack is special-cased in ten production files.** `carrier == "load_shedding"`
   or the `__voll_` name prefix appears in **30 non-test places** across
   `solver_service.py`, `routers/results.py`, `routers/network.py`,
   `services/ac_pf_service.py`, `services/asset_results/service.py`,
   `services/project_context.py`, `services/pypsa_service.py`, and the frontend's
   `simulation.ts`, `SolverSettings.tsx`, `LoadFlow.tsx` and `Prices.tsx`. Several of
   those exclude the slack from price-setting and from load-flow. **Any site that
   special-cases `load_shedding` but not `demand_response` will silently let DSR set
   prices or appear as a real generator.**

   Therefore: introduce one `SLACK_CARRIERS` constant and refactor all 30 sites to test
   **membership** rather than equality, *before* adding the second tier. Mechanical,
   testable, and the only way to make the change safely. This is a Phase 0 item.

---

## 5. The numbers

### 5.1 The reliability target — set either, report both

**The enforced constraint is always energy-based**, because it is linear. Two of them,
per the cap-geometry decision — a system target plus a per-zone backstop so the optimiser
cannot satisfy the system number by sacrificing one zone:

```
system:   Σ_t w_t · Σ_{b ∈ electrical}      p_shed[b,t]  ≤  Ē_sys
per zone: Σ_t w_t · Σ_{b ∈ electrical ∩ z}  p_shed[b,t]  ≤  Ē_zone(z)     ∀ z
```

Only the **involuntary** tier enters these sums (§4.4); `demand_response` dispatch does not.

**Zone = the bus `country` field**, which already exists on `BusCreate`
(`models/schemas.py:37`). Two consequences:

- It **defaults to `""`**, so on a hand-built GUI network every bus lands in one unnamed
  zone and the per-zone ceiling silently degenerates to a second copy of the system cap.
  Preflight must warn when a per-zone ceiling is set and `country` is unpopulated —
  otherwise the constraint appears to be doing something and is not.
- `Ē_zone` is expressed as ‱ of **that zone's own** demand so it scales with zone size,
  defaulting to a multiple of the system target (3× is a reasonable starting default —
  loose enough not to bind in normal operation, tight enough to stop one zone absorbing
  everything).

Per-zone ceilings can make the problem **infeasible** where a zone genuinely cannot be
served. That must surface as "zone X cannot meet its ceiling", naming the zone — not as a
bare `infeasible` from the solver.

A **time-based** target (shed-hours per year) is **not** enforceable as a linear
constraint — it needs 8760×|B| binaries with a big-M whose LP relaxation is ~0
(`y_t = shed_t/M`), which is numerically hopeless. So:

- **Enter an energy cap** → one solve, cap enforced directly.
- **Enter a shed-hours target** → an outer bisection on `Ē` until achieved shed-hours
  lands within tolerance. Typically 5–8 solves. Honest caveat: shed-hours is generally
  non-increasing as `Ē` tightens but **not strictly monotone**, so the bisection needs a
  tolerance and may not converge exactly. Report what was achieved, not what was asked.

**Both numbers are always reported**, and this is not redundant. With a binding cap the
optimiser will use its full allowance whenever shedding is cheaper than building, so
**achieved ENS ≈ the cap by construction** and carries little information. Shed-hours is
*not* pinned by an energy cap — the same MWh can be concentrated in one long event or
spread across many short ones — so it is the number that actually tells the user
something.

**Units.** The UI takes the energy target in **parts per ten thousand (‱) of annual
electrical demand**, with a warning band showing where real standards sit. Stating it as
a percentage invites a serious error: *99% energy availability means planning to not
serve 1% of demand* — for a 300 TWh system, 3 TWh/yr unserved. Real standards are two to
three orders of magnitude tighter (GB's 3 h/yr LOLE is 99.966% of **hours**; adequate
systems run EENS around 0.001–0.01% of demand). A user who types "99%" gets a
cheap-looking, badly under-built plan.

### 5.2 The cost axis

**Total system cost: CapEx + FOM + variable OpEx (fuel, VOM) + CO₂ cost — excluding
load-shedding cost.**

The exclusion is not a detail. Total cost as computed by the solver *includes*
`VoLL × ENS`; plotting that against ENS puts the x-axis inside the y-axis and the curve
becomes partly self-referential. This is the same trap as the `Δopex` double-count in
§3.5, in a different place. The cost axis must be the **cost of the system**, not the
cost of the system plus the penalty for the thing on the other axis.

Total cost rather than CapEx-only matters because reliability bought through cheap
peakers barely moves CapEx while moving fuel a lot; a CapEx-only curve would rank that
option artificially well.

In a multi-period run `investment_period_weightings.objective` discounts costs, so the
figure is an **NPV**, and the "optimum" is an NPV optimum — not the annualised number a
reliability standard implies. Label the axis accordingly and state the period basis.

### 5.3 Availability: two numbers, side by side

Neither is the headline:

- **LP proxy** — storage-aware and network-aware, perfect-foresight, single realisation.
  Right system, biased method.
- **COPT screening** — classical convolution over dispatchable thermal, applied to a
  residual curve netting only exogenous must-take generation, VRE as multi-state
  capacity. Defensible method, wrong system: storage-blind, chronology-free, network-free.

**Their divergence is the product.** A large gap means storage and the network are
carrying the adequacy — precisely when the classical number misleads and when a PRAS or
Antares export is worth doing. Presenting either alone invites misplaced trust.

COPT cost is `O(N · C/Δ)` over a **rounding increment Δ**, not `2^N`, so "fast" is
directionally right — but Δ must be named and its rounding bias handled (capacity
apportioned probabilistically to adjacent rounded states, or the table drifts).
The **table** is milliseconds for a national model (~300 units); the **route** is not:
`attribute_criticality` deconvolves every unit in a Python loop over C/Δ and was
measured at ≈ 32 s for 300 units on a 5447-state table (Phase 12c-pre review), and
the synchronous `/copt` contract at that size is an open hardening item beside the
abort routes. Full PyPSA-Eur (10³–10⁴ units) is seconds-to-minutes for the table
alone, and **multi-area** COPTs with transfer limits are exponential in the number of
areas — the actual reason PRAS exists.

**Amendment (Phase 12c) — the portfolio ELCC beside the reserve margin.** The
profile-bearing fleet is priced as ONE group, per investment period, by the
sequential MC's constant-LOLE bisection, and shown beside the reserve margin's
own credit for the same group (its payload rows, not a recomputation). Two
standards on one population, one capacity rule per side with the rule for
their disagreement stated, and no ratio between them (MC spec v1.6).

**Amendment (Phase 12c-0) — one demand basis.** Every engine on this page
evaluates the demand the LP was built against: `services/adequacy/demand.py`
owns the load-scaler resolution the LP applies, and `fleet_and_residual`,
`snapshot_inputs` and the route-side `reserve_margin_facts` read it. Until
then the COPT, the MC, ELCC and both certifying loops evaluated the raw
series on scaled projects — the fifteenth finding, recorded in the 12c v3
plan and its review.

**Amendment (Phase 12c-pre) — profiled occurrence units.** A generator that carries
BOTH an availability series (`p_max_pu` column, not identically 1) and resolvable
outage data enters the sampled fleet with the series attached (`CoptUnit.profile`).
The table is built over the units without a profile and the profiled units are
**mixed exactly per hour over their `2^k` outage states**
(`LOLP_h = Σ_s P[s]·(1 − S(r_h − Σ_i s_i·a_{i,h}))`, `a_{i,h} = profile_i(h)·cap_i`),
up to `K_EXACT = 8` of them; beyond that the smallest-mean units are netted at
expected output and the payload names them, with their attribution rows marked as
understated. Netting a unit at its expectation — the obvious shortcut — was rejected on
measurement: LOLP is convex in the shortfall, so it understates LOLE 3× and a unit's
criticality 14× on a mild profile. The sequential MC samples the same unit's outages on
its series. A **static** `p_max_pu < 1` is still not applied by either engine (it is
ambiguous in the wild — a typed capacity factor, or PyPSA-Eur's nuclear CF that already
contains outages) and preflight says so (`static_p_max_pu_not_applied`); the reserve
margin applies it, so the margin and the engines disagree about such a unit by a
recorded 25 % / 2 % on the nuclear import — an open item, not this amendment's.

### 5.4 Criticality — IEC 60812 FMECA, no RPN

```
f_i  [1/yr]  = 8760 * FOR_i / MTTR_i        # cycle frequency, not failure rate
S_i  [EUR]   = E_t[ΔEUE | outage starts at t] * VoLL + Δopex_excl_load_shedding
C_i  [EUR/yr]= f_i * S_i
```

- **`S_i` is an expectation over event timing**, sampled across representative start
  times — not the damage at the annual peak. Equivalently and more cheaply, compute
  annual expected damage as `8760·FOR_i × mean hourly damage rate` and drop the `f×S`
  factorisation.
- **Grouped rows carry a multiplicity**, not a group-sized occurrence: severity = removing
  **one** unit of the class, occurrence = per-unit `f_i`, row carries `N`. A genuine
  common-cause mode is a **separate row** with a β-factor-derived occurrence.
- **`Δopex` excludes the `load_shedding` carrier**, or `VoLL·ΔEUE` is counted twice.

**The occurrence statistic is labelled, never inferred.** The schema carries
`outage_rate_value` plus `outage_rate_basis ∈ {FOR, EFORd}` and `mttr_hours`:

- **FOR** (service-hours based) is what NERC GADS class averages publish and what a user
  will most easily find.
- **EFORd** (demand based) is what adequacy studies use and what makes the COPT correct.

They diverge most for units with substantial reserve-shutdown hours — peakers, exactly
the units that matter at the margin — where FOR biases availability **optimistic**.
Converting between them needs the unit's demand factor and its service/reserve-shutdown
split, neither of which the model carries, so **the tool must not silently convert**.
Accept either, store which, warn when a low-capacity-factor unit is entered as FOR, and
tag the resulting COPT metrics with the mix of bases that fed them.

A consistency validator is also required: FOR and MTTR will be sourced independently
(class averages vs a maintenance database) and are over-determined — the formula happily
returns 36.5 outages/yr for a large thermal unit given FOR 0.10 with MTTR 24 h. Check the
implied MTTF and flag implausible values.

**No RPN and no Action Priority.** €/yr criticality is the ranking. Severity and
Occurrence bins may be rendered for readers who expect the columns, but RPN from binned
ordinals is a *product of sums* and is not monotone in `C = f·S` — a mode 10× more
critical can rank lower, visibly, on the first worksheet. AIAG-VDA removed RPN in 2019
for exactly this reason, and its Action Priority is a lookup over all 1,000 S/O/D
combinations, undefined without a real Detection column. Detection has no natural
power-system meaning; the worksheet carries a **mitigability** column instead.

### 5.5 VoLL becomes a reporting parameter, not a design parameter

With the ENS cap as the enforced standard, VoLL's job changes. It is still needed to keep
the LP feasible (the slack generators) and to value severity in €. But the **cap** is what
shapes the plan.

This creates a coherence hazard: if VoLL is set high enough, the LP sheds *less* than the
cap allows and VoLL is the effective standard; if low, the cap binds. **Whichever binds
first is the real standard, and the user cannot see which.** The solver must emit a
config-coherence warning naming the binding one. Left unhandled, two users with the same
stated target get different plans for reasons neither can observe.

**One value now, schema shaped for segments.** VoLL ships as a single system-wide number,
which is adequate while it only values severity. ACER's methodology requires a
segment-weighted value (industrial / commercial / residential), and segment weighting
changes the criticality ranking wherever load mix varies by region — so the contract
carries `voll: {default, by_segment?}` from the start even though only `default` is
populated.

**The blocker on segments is the slack geometry, and §4.4 is already moving it.** A single
slack per bus cannot attribute *which* load was shed, so per-segment VoLL is impossible
today regardless of schema. Splitting the slack into per-`(bus, load)` slacks would unlock
per-segment *and* per-carrier attribution at once — at the cost of multiplying the slack
count by loads-per-bus, which on a sector-coupled network is a real LP size increase.
Since §4.4 already re-opens this code, **decide then whether the second tier is
per-bus or per-(bus, load)**; retrofitting it later means touching those 30 sites twice.

### 5.6 The frontier (on demand)

Sweep `Ē` and plot total system cost against achieved ENS and shed-hours. The economic
knee is where marginal system cost equals `VoLL × marginal ENS avoided`.

The **VOLL sweep is a validation** that recovers the cap's shadow price — not an
independent frontier. The two are duals and coincide **only if `C*(Ē)` is convex**. This
repo breaks that: `solver_service.py:939-953` switches to **MILP with a MIP gap** when
`committable` generators are present, so `C*` is non-convex, the VOLL sweep **skips**
unsupported portions, and returned points may not be on the frontier at all. The
VOLL-frontier path must be **disabled or warned** under unit commitment or a nonzero MIP
gap.

---

## 6. What already exists — corrected inventory

### 6.1 Verified, usable

| Capability | Location | Note |
|---|---|---|
| VOLL slack generators | `services/solver_service.py:4405` | **Load-bearing buses only** — see §6.3 |
| Lost Load results tab | `frontend/src/pages/results/LostLoadTab.tsx` | Per-carrier + per-bus |
| Per-bus × per-snapshot shed array | captured `solver_service.py:4467-4477`, persisted `routers/projects.py:1574-1580` | **Full resolution, in `results_state.pkl`** — the real severity source |
| Solve queue | `services/solve_queue.py` | FIFO, abortable, per-project context |
| `compare-state` | `routers/compare.py:71` | One project, from disk; A/B diff is client-side |
| SCLOPF / N-1 contingencies | `solver_service.py:167-200, 3706-3770`; UI `SolverSettings.tsx:399-509` | **Already shipped** — §6.2 |
| Scenario branching | `POST /{base}/scenarios` `routers/projects.py:2299`; `ScenariosPanel.tsx` | Already enqueues onto the solve queue |
| Representative periods (tsam) | `services/time_aggregation_service.py` | Cached per `(period, cfg-fingerprint)` |
| Capacity freezing | `_freeze_period_capacities` `solver_service.py:4911+` | Myopic internal; **no user-facing flag** |
| Objective wrappers | `_wrap_with_curtailment_cost` `solver_service.py:2783` | The pattern the ENS cap must follow |

### 6.2 SCLOPF is already shipped — v1 listed it as an external dependency

`SolverConfig` carries `sclopf`, `sclopf_include_all_lines`,
`sclopf_include_all_transformers`, `sclopf_voltage_threshold_kv`, `sclopf_extra_lines`,
`sclopf_extra_transformers`, `sclopf_scope`. `resolve_branch_outages` is a full
contingency-set resolver, wired into both the full-horizon and myopic paths, with
preflight validation and a complete frontend surface.

**Class B therefore has a solver path and a UI already.** The two real gaps:

- `resolve_branch_outages` covers **Line and Transformer only — not Link.** HVDC and
  power-to-X link outages need a `p_nom → 0` path. That is the class-B work, and it is
  narrower than v1 assumed.
- **SCLOPF is incompatible with `transmission_losses=True`** — hard-blocked at preflight
  (`services/validation_service.py:624-629`). Any FMEA run wanting both is refused.

### 6.3 Overstated or wrong in v1

- **VOLL slacks are not "per bus, all buses".** `solver_service.py:4426-4430` builds
  `load_bus_set` from `n.loads["bus"]` and skips every bus not in it — deliberately (a
  slack on a transit bus lets the LP manufacture energy at VOLL price). **A contingency
  that starves a transit bus produces LP infeasibility, not a priced ΔEUE.** The class-B
  driver must treat infeasible contingencies as a distinct outcome, not discard them.
  (Docstrings at `routers/results.py:2957`, `frontend/src/api/simulation.ts:388` and
  `LostLoadTab.tsx:21` still say "every bus" — stale; the code is right.)
- **No per-load-carrier attribution.** One slack per bus, `carrier="load_shedding"`. Where
  a bus hosts several `Load`s with different carriers, the single slack cannot say which
  was shed. *Mostly neutralised by the electricity-only scope (§4.3), but it blocks any
  future per-carrier target.*
- **Links and storage get no slack at all.** A cyclic-SoC or link-capacity infeasibility
  stays an infeasible LP.
- **`lost_load_cost_meur` was always 0.0** — read from `n.meta["last_lost_load"]`, which
  nothing writes. v1's "already reconciled against the objective" was false. *Fixed on
  `claude/fix-lost-load-cost-and-custom-attr-drop` (`8e2f98d`).*
- **Two divergent lost-load numbers exist.** `solver_service.py:4477` computes
  `total_mwh * voll` **unweighted** ("assumes hourly snapshots"); `:2452` computes it
  **snapshot-weighted per period**. Under tsam representative snapshots these disagree.
  **Phase 0 must pin which is canonical**, or the target will not mean what it says.
- **The scarcity diagnosis is not an effects taxonomy.** A per-(bus, snapshot) explanation
  of high dual prices, gated at 2000 €/MWh and capped at 200 rows
  (`routers/results.py:2784-2790`); `transmission` is the residual "couldn't explain it"
  bucket.
- **TimeSeriesManager is not a multi-weather-year seam.** `_user_ts` is keyed
  `(component, attribute, column)` (`routers/network.py:2172`) with no year dimension;
  `PUT /timeseries/{component}/{attribute}` **replaces the entire attribute frame**; and
  it is a **foreground module global** that background solves deliberately skip
  persisting. Holding N coincident years needs N project copies or a new dimension in
  `_user_ts` **and** the netCDF layout.
- **Buses already carry `country`** (`models/schemas.py:37`), so the per-zone ceiling needs
  no new grouping attribute — but it defaults to `""` and nothing enforces it, so on a
  GUI-built network it is usually empty (§5.1).
- **The VOLL slack is special-cased in 30 non-test places** across ten production files,
  several of which exclude it from price-setting and load-flow. Adding a second slack tier
  without first centralising that test is the highest-risk mechanical change in the
  feature (§4.4).
- **"Severity is already computed" was half true.** The full array is in the pickle, but
  `GET /results/lost_load` reads `_state` (**foreground only**), and
  `_compute_lost_load_summary` collapses to totals and **caps per-bus rows at 24**. **No
  shed-hours metric exists anywhere** — and §5.1 makes it the headline reported number.

---

## 7. Constraints on implementation

### 7.1 The queue cannot express a sweep

`EnqueueRequest` is `{project_id: str}` (`routers/solve_queue.py:36-37`) and `_run_job`
reads config off the context (`solve_queue.py:389`). **There is no way to enqueue "same
project, `Ē` = X."** Either add a per-job variant payload, or materialise N scenario
projects first — but `create_scenario` snapshots the **foreground in-memory network**, so
N points means N sequential (mutate foreground → save → branch) cycles that clobber
whatever the user is editing.

*The "target first" decision defers this entirely.* The core loop is one solve; only the
on-demand curve and the shed-hours bisection need it. The bisection is the cheaper
forcing function — 5–8 solves — and should drive the design of the variant payload.

Also: the queue is **abortable but not resumable** (`_jobs`/`_order`/`_q` are in-memory,
so a restart loses the sweep with no partial-progress record), and `clear_finished` is
**global across orgs** and super-admin-gated.

### 7.2 Do not drive the sweep through the HTTP API

Every write to `/api/network/*` triggers `_push_undo_snapshot()` — a full
`export_to_netcdf()` round-trip that "can take seconds on a large network"
(`main.py:592-613`) — **and clears all dispatch results, resetting the lifecycle to
`idle`** (`main.py:658-693`). **The driver must mutate `ctx.network` in-process.**

The real limits on parallelising solves (not HDF5, per §3.2): foreground module globals
(`_user_ts`, `PyPSAService._active`, `routers.simulation._state`); one results slot per
project directory; and a **documented PyInstaller + multiprocessing incident**
(`desktop/gui.py:51-86`, 2026-08-03) where a `resource_tracker` helper re-executed the
bundle and took the single-instance lock.

### 7.3 The ENS cap cannot be user code

`_compile_extra_functionality` **hard-refuses unless `PYPSA_GUI_ALLOW_USER_CODE=1`**
(`solver_service.py:1458-1494`) — off by default because it is an unsandboxed in-process
`exec()`. The cap must be a first-class wrapper alongside `_wrap_with_curtailment_cost`.

---

## 8. UX / UI assessment

v2 described this as "a new Results tab plus a frontier chart". That was wrong. Below is
the corrected picture, and what the "target first" decision saves.

### 8.1 The information-architecture problem

The app's model is **project → solve → results**. A sweep is **project → N solves →
aggregate results**. That has no slot in the current IA: a sweep is not a result, it is a
*study that produces* results.

**"Target first, curve on demand" dissolves this for v1.** The core loop is one solve
against one target — which fits the existing IA exactly. Only the optional curve needs a
new home, and by then the shed-hours bisection will already have forced a minimal
multi-solve substrate. **Recommendation:** put the on-demand curve behind an action on
the existing Scenarios surface (which already branches projects and enqueues solves)
rather than inventing a "Studies" area. Revisit only if the curve becomes the primary
workflow.

### 8.2 Surfaces required

| Surface | Precedent in the app | v1 scope | Verdict |
|---|---|---|---|
| Reliability target input | `voll` field, `SolverSettings.tsx:1667` | **Required** | Small — one new section following an existing pattern |
| Per-zone ceiling input + empty-`country` warning | None; zones are not surfaced anywhere in the UI today | **Required** | Small table keyed on `country`, but it makes zones a user-visible concept for the first time |
| DSR tier config (price, volume, per-bus opt-in) | None | **Required** | New; needs a per-bus opt-in surface and a double-count warning where a flexible asset already exists |
| FOR/MTTR per asset | `curtailment_cost` is the exact precedent | **Required** | ~60 mechanical edits (6 touch points × 2 attrs × 5 components) |
| Per-carrier defaults library | None | **Required** | New, small |
| FMEA worksheet, **editable cells** | **None** — all six result tables are read-only | **Required** | **The single biggest build** |
| Achieved-vs-target readout | None | **Required** | Small |
| Provenance / fidelity badges | None | **Required** | Cross-cutting, touches every number |
| Sweep configurator + progress | Solve-queue UI exists but is per-project FIFO | **Deferred** | New, when the curve ships |
| Frontier explorer | None | **Deferred** | New, when the curve ships |

### 8.3 The worksheet is the hard part

`useFilterableTable.tsx` is a 180-line **sort + substring-search hook** plus a search box
and a sortable `<th>`. It has no column definitions, no cell rendering, no pagination —
and **no editable cells**. Six hand-written read-only tables already exist; the worksheet
will be a seventh and gets only free sorting from the hook.

The worksheet must be a genuine editing surface, because class D rows and the mitigability
column are expert-entered, and those edits must persist with the project and survive a
re-solve (the computed rows regenerate; the manual ones must not be wiped). **There is no
precedent for a persisted, user-edited results table in this codebase.** That is the
component to prototype first, because it carries the schedule risk.

CSV export is free — `downloadCSV` in `pages/results/shared.tsx`, used by six tabs.

### 8.4 Wiring cost for a new Results tab

Five coupled edits in `pages/Results.tsx`: the `ResultsTab` union (`:34`), `VALID_TABS`
(`:39`), the `TABS` array (`:55`), the render switch (`:465`), **and the exhaustive
`Record<ResultsTab, CompareTab>` at `:72`**, which will not compile until `fmea` maps to a
member of `CompareView`'s `Tab` union — alias to `'overview'`, as `asset` does.

### 8.5 Honest summary

Under "target first", the analysis and the UI are roughly balanced, and the feature is
**one new surface plus a settings area**, not three new surfaces. v4's decisions grew that
settings area — the target section now carries zone ceilings and a DSR tier alongside the
system cap, and **zones become a user-visible concept for the first time** — but none of
it changes the shape of the build. The editable
worksheet remains the schedule risk and has no precedent. The deferred curve is where the
IA question and the sweep machinery live, and deferring it is what makes v1 tractable.

---

## 9. Module layout

```
backend/services/adequacy/          # `services/adequacy/` verified free
  taxonomy.py      # the four classes + mode catalogue
  occurrence.py    # FOR/MTTR attrs, per-carrier defaults, consistency validator
  copt.py          # convolution, leave-one-out importance, rounding increment
  target.py        # ENS cap wrapper + shed-hours bisection + coherence warning
  sweep.py         # class B/C driver — in-process on ctx.network, never over HTTP
  criticality.py   # f_i * S_i, timing expectation, multiplicity/CCF handling
  worksheet.py     # IEC 60812 rows, computed + persisted manual rows, CSV export
  engines/
    lp_proxy.py
    pras_export.py     # optional — must take PyPSAService.get_netcdf_io_lock()
    antares_export.py  # optional — via antares-craft
```

`pras_export.py` writes `.pras` **HDF5**; v1 proposed adding a second HDF5 producer to a
process it had just called HDF5-fragile. It must take the same lock every other HDF5 path
takes.

---

## 10. Provenance contract

```
AdequacyReport {
  engine:   "lp_proxy" | "copt" | "pras" | "antares"
  fidelity: "deterministic_scenario" | "analytic_convolution" | "sequential_mc"
  target:   { basis: "energy" | "shed_hours",
              system: { cap, achieved_ens_mwh, achieved_shed_hours },
              zones:  [ { zone, cap, achieved_ens_mwh, binding: bool } ],
              binding: "system_cap" | "zone_cap" | "voll",
              zone_field_populated: bool }
  metrics:  { ens_mwh, shed_hours, lole_hours?, eue_mwh?,
              confidence_interval?, n_samples?, time_basis }
  cost:     { total_system_cost, excludes_shed_cost: true, period_basis }
  inputs:   { weather_years, voll: { default, by_segment? }, seed?, assumptions_hash,
              outage_rate_bases: { FOR: n, EFORd: n } }   # what fed the COPT
  energy:   { involuntary_mwh, demand_response_mwh }      # only the first is unserved
  per_mode: [ FailureModeResult ]     # own provenance
  frontier: [ TradeoffPoint ]         # own provenance; absent unless the curve was run
}
```

`target.binding` is what §5.5 requires: the user must be able to see whether the system
cap, a zone ceiling or VoLL shaped the plan. `zone_field_populated` exposes the empty-
`country` degeneracy of §5.1 rather than letting a ceiling look enforced when it is not.
`cost.excludes_shed_cost` is asserted in the payload so a consumer cannot accidentally
plot a self-referential curve. `energy` splits the two slack tiers so no consumer can
re-merge DSR into unserved energy by accident, and `outage_rate_bases` records the FOR /
EFORd mix behind any COPT metric.

**No number produced by Phases 0–4 may be compared to a statutory standard.** The LP proxy
understates (perfect foresight, one realisation); the COPT screening is storage-blind and
network-free. The UI must say so at the point of display, not in a footnote.

---

## 11. Phasing

| Phase | Content | Ships |
|---|---|---|
| 0 | Outage-rate attributes (value + **basis** + MTTR) + defaults + consistency validator; **pin the canonical lost-load number**; **add a shed-hours metric** (none exists); **centralise the slack test behind `SLACK_CARRIERS` across all 30 sites**; `AdequacyReport` contract | Nothing user-visible |
| 1 | Two-tier slack (`demand_response` / `load_shedding`); system ENS cap **+ per-zone ceilings** as first-class objective wrappers; target input UI; achieved-vs-target readout incl. shed-hours; empty-`country` and VoLL/cap coherence warnings | **The core loop — set a target, get a plan** |
| 2 | COPT: convolution, screening LOLE/EUE, leave-one-out criticality for class A; side-by-side with the LP proxy | The FMECA ranking — **zero extra LP solves** |
| 3 | The worksheet UI — computed rows + persisted manual class-D rows + mitigability; provenance badges; CSV export | **The formal deliverable** |
| 4 | Class B (**Link** outages + existing SCLOPF); class C (bundled climate years + multi-year storage) | Full taxonomy coverage |
| 5 | Shed-hours bisection; the on-demand cost-vs-availability curve; sweep substrate | The trade-off curve |
| 6 | *Optional:* PRAS / Antares exporters; capacity credit / ELCC | Regulatory-grade validation |

**Reordered from v2** by the "target first" decision: the curve moves from Phase 3 to
Phase 5, and the sweep substrate (§7.1) moves with it. Phases 0–3 need **one solve per
run** and no sweep machinery at all.

**Phase 0 is not small.** Data persistence for custom attributes is genuinely free —
PyPSA 1.x accepts custom columns, and the netCDF round-trip and `GET` serialisation need
no work. The cost is elsewhere: **five Pydantic schemas** (`extra="ignore"` silently drops
undeclared fields on POST/PUT), roughly **60 mechanical frontend edits**, and pinning the
canonical lost-load number.

One Phase 0 hazard is already closed: `_merge_partial_update` used to drop custom columns
on any partial PUT, so `forced_outage_rate` would have been one stray request from silent
erasure. Fixed on the branch above.

### Still out of scope, and worth a decision later

Capacity credit / ELCC — the actual currency of a capacity-vs-availability conversation,
derivable from the COPT as equivalent firm capacity at constant LOLE. Cheap once Phase 2
exists.

The three items v3 left unresolved — cap geometry, DSR accounting and VoLL structure — are
now decided (§2) and folded into Phases 0–1. What remains genuinely open is whether the
second slack tier is per-bus or per-`(bus, load)`, which §5.5 argues should be settled
during Phase 1 rather than retrofitted.

---

## 12. Open-source landscape

- **[PRAS](https://nrel.github.io/PRAS/)** (NREL, Julia) — **modified MIT (Expat)**, so
  v1's outstanding licence risk is closed. Sequential Monte Carlo with storage and
  multi-region transfer limits, plus **analytical convolution methods** that partly
  duplicate our COPT. `.pras` HDF5 makes the exporter a clean seam.
- **[PRAS-Linkage](https://globalpst.org/wp-content/uploads/2_MSeatle_Multi-system-co-modelling-1.pdf)**
  — Python bridge from capacity-expansion output into PRAS. Direct prior art.
- **[Antares Simulator](https://github.com/AntaresSimulatorTeam/Antares_Simulator)**
  (RTE, MPL-2.0 since v9.0) — behind ENTSO-E's TYNDP and RTE's adequacy report. Python
  API [antares-craft](https://pypi.org/project/antares-craft/). File-level copyleft
  imposes nothing on us for writing input files.
- **[PyPSA-Earth Monte Carlo](https://pypsa-earth.readthedocs.io/en/latest/monte_carlo.html)**
  — an `uncertainties` + Latin-hypercube config pattern in our own ecosystem; the cheapest
  model for the sweep config schema when Phase 5 arrives.
- **IEEE RTS-96 / RTS-GMLC**, NERC GADS class averages, ENTSO-E ERAA availability
  assumptions — occurrence data. **Pin whether the schema means FOR or EFORd**: GADS "FOR"
  is service-hours-based, adequacy studies use demand-based EFORd, and they differ
  materially for units with reserve-shutdown hours.
- **[`reliability`](https://pypi.org/project/reliability/)** — only if fitting FOR/MTTR
  from a customer's own failure history.
- **FMEA-specific OSS** ([`fmeca`](https://github.com/benranderson/fmeca),
  [`FMECA`](https://github.com/pythonasset/FMECA),
  [`LLMRiskAnalyzer`](https://github.com/YuchenXia/LLMRiskAnalyzer)) — assessed, not worth
  a dependency. Take the IEC 60812 schema; compute the numbers ourselves.
- **PyPSA SCLOPF** — **not** an external item. Already shipped (§6.2).
