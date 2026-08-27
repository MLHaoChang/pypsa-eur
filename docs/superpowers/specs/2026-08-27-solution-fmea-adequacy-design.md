# Design — Solution FMEA & Capacity-vs-Availability Trade-off (v2)

**Status:** assessment / design, revised after adversarial review. No feature code written.

**Supersedes v1** (commit `d445f83`). Two independent reviews — one on the reliability
and optimisation mathematics, one verifying every claim against the codebase — found
v1's central technical proposal wrong and roughly a third of its "this already exists"
inventory overstated. This revision records what survived, what did not, and why.

**Goal (unchanged):** systematically enumerate how a solved investment plan can fail to
serve load, rank those failure modes by model-computed criticality, and expose the
capacity-expansion vs availability trade-off as a navigable frontier.

**Verdict (revised): feasible, but v1 understated the cost.** The severity *data* exists
at full resolution in `results_state.pkl`; every API view of it is lossy, the € roll-up
was broken, and the sweep substrate v1 assumed it could ride does not accept per-job
variants. The feature is real; Phase 0 is bigger than "a schema tweak".

---

## 1. Decisions taken

| Question | Decision |
|---|---|
| Deliverable shape | Model-computed occurrence/severity in a formal worksheet |
| Rigour | LP-derived proxy acceptable; heavy Monte Carlo not required to ship |
| Runtime | Desktop-safe by default; PRAS/Antares handoff optional |
| Failure modes | All four classes |
| **Headline availability metric** | **Both LP proxy and COPT, side by side, neither headline.** Their divergence is itself the diagnostic |
| **Worksheet conformance** | **IEC 60812 FMECA — €/yr criticality is the ranking. No RPN, no Action Priority** |
| **Two pre-existing bugs** | **Fixed separately** on `claude/fix-lost-load-cost-and-custom-attr-drop`, off `master` — not carried by this feature |
| **Class C data** | **Fund it** — bundle reference climate years so the class works out of the box |

---

## 2. What v1 got wrong

Recorded because the errors are instructive, and because a reader of v1 needs to know
which parts not to build.

### 2.1 The COPT proposal was circular (v1 §7)

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
genuine. Worse, v1 §7 called it "probabilistic, not proxy" while v1 §8 correctly warned
that perfect-foresight LP output understates LOLE — a direct self-contradiction, on a
number built from that very output.

**Correct construction (§6 below):** COPT over dispatchable thermal only, applied to a
residual curve netting **only exogenous must-take** generation at its given availability
— no storage, no imports, no LP dispatch decisions — with VRE entered as **multi-state
capacity**, not a deterministic subtraction.

### 2.2 The affordability argument was built on the wrong constraint (v1 §3)

v1 asserted solves are serialized because netCDF/HDF5 is process-global thread-unsafe,
and concluded Monte Carlo is "categorically out of reach… forced by the process model".

Thread-unsafety forbids **threads, not processes**. The codebase's own docstring
(`services/solve_queue.py:28-30`) says the HDF5 hazard is handled by `_netcdf_io_lock`
around I/O only; `_run_job` holds no HDF5 lock across `run_simulation`
(`solve_queue.py:427-431`), and the LP itself (linopy → HiGHS) touches no HDF5.
Sequential dispatch is a design choice.

The real blockers on parallelism are named in §4.2. The Monte Carlo conclusion survives
— but on **runtime** grounds (each LP takes minutes; 10³–10⁴ replications is 3–4 orders
of magnitude away, and 8-way parallelism buys 8×), not on the process model. v1 also
strawmanned SMC as replications *of the investment LP*; PRAS and Antares run a
lightweight dispatch evaluation per replication, which is exactly why they are fast.

### 2.3 The contingency sweep was the wrong primary engine (v1 §6, §12)

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
unit. This **collapses the class-A sweep to zero solves** and inverts v1's phase order.

### 2.4 `s_nom → 0` is a modelling bug for AC lines (v1 §4)

Under PyPSA's KVL/cycle formulation the line's reactance still enters the cycle
constraints, so you get a zero-capacity-but-still-present branch: susceptance handling
degenerates, PTDFs are unchanged, and the answer is silently wrong. A line outage
requires **removing** the branch so PyPSA recomputes `SubNetwork` cycles — or using the
LODF/contingency machinery, which this repo already ships (§3.2).

### 2.5 Smaller corrections

- **λ naming.** `8760·FOR/MTTR` is arithmetically correct as expected events per year,
  but it is the **cycle frequency**, not the failure rate `1/MTTF` that `λ` conventionally
  denotes. They differ by `(1−FOR)` — immaterial at FOR 0.05, 30% at FOR 0.3. Rename `f_i`.
- **MTTR cancels.** `C_i = f_i·S_i = (8760·FOR_i/MTTR_i)·(MTTR_i·d) = 8760·FOR_i·d`.
  The criticality is `unavailability × annual hours × damage rate`, and the entire
  answer rests on **where in the year the outage window is placed** — a free parameter
  v1 never specified. Combined with v1's "restrict to peak-risk hours", it asserts every
  event lands on the annual peak, overstating criticality by the peak-to-mean damage
  ratio.
- **Group rows were a category error.** v1's "one row per (carrier × region)" inherits a
  per-unit `f_i` while zeroing a whole group — a common-cause event whose independent
  probability is `Π p_i ≈ 0`. Inflates `C_i` by orders of magnitude.
- **"EENS" was not an expectation.** What the LP produces is ENS for **one** weather
  year, one availability realisation, with perfect foresight. The "E" requires an
  expectation over outage states and weather years.
- **The ACER conflation.** v1's first-order condition (`marginal capacity cost = VoLL ×
  marginal EUE avoided`) is correct for `min C(x) + VoLL·E(x)`, but ACER's reliability
  standard is `RS = CONE/VoLL` in **LOLE hours/yr**, not EUE. They coincide only if the
  marginal peaker displaces exactly its nameplate in every loss-of-load hour.
- **Severity double-counting.** `S_i = ΔEUE·VoLL + Δopex` double-counts if `Δopex` is
  taken as Δ(objective): the slack generators dispatch at marginal cost `voll` and their
  cost already lands in the economics under `carrier == "load_shedding"`. `Δopex` must
  **exclude** that carrier.
- **PRAS licence risk is closed** — modified MIT (Expat), permissive. Note PRAS also
  ships analytical convolution methods, so a DIY COPT partly duplicates it.

---

## 3. What actually exists — corrected inventory

### 3.1 Verified, usable

| Capability | Location | Note |
|---|---|---|
| VOLL slack generators | `services/solver_service.py:4405` | **Load-bearing buses only** — see §3.3 |
| Lost Load results tab | `frontend/src/pages/results/LostLoadTab.tsx` | Per-carrier + per-bus, reuses the sort/search hook |
| Per-bus × per-snapshot shed array | captured `solver_service.py:4467-4477`, persisted `routers/projects.py:1574-1580` | **Full resolution, in `results_state.pkl`** — the real severity source |
| Solve queue | `services/solve_queue.py` | FIFO, abortable, per-project context |
| `compare-state` | `routers/compare.py:71` | One project, from disk; A/B diff is client-side |
| SCLOPF / N-1 contingencies | `solver_service.py:167-200, 3706-3770`; UI `SolverSettings.tsx:399-509` | **Already shipped** — see §3.2 |
| Scenario branching | `POST /{base}/scenarios` `routers/projects.py:2299`; `ScenariosPanel.tsx` | Already enqueues onto the solve queue |
| Representative periods (tsam) | `services/time_aggregation_service.py` | Cached per `(period, cfg-fingerprint)` |
| Capacity freezing | `_freeze_period_capacities` `solver_service.py:4911+` | Myopic internal; **no user-facing flag** |

### 3.2 v1 listed SCLOPF as an external dependency to adopt. It is already shipped.

`SolverConfig` carries `sclopf`, `sclopf_include_all_lines`,
`sclopf_include_all_transformers`, `sclopf_voltage_threshold_kv`, `sclopf_extra_lines`,
`sclopf_extra_transformers`, `sclopf_scope`. `resolve_branch_outages` is a full
contingency-set resolver, wired into both the full-horizon and myopic paths, with
preflight validation and a complete frontend surface.

**Failure class B therefore has a solver path and a UI already.** The two real gaps,
which v1 missed by proposing to reinvent it:

- `resolve_branch_outages` covers **Line and Transformer only — not Link.** HVDC and
  power-to-X link outages genuinely need a `p_nom → 0` path. That is the class-B work,
  and it is narrower than v1 assumed.
- **SCLOPF is incompatible with `transmission_losses=True`** — hard-blocked at preflight
  (`services/validation_service.py:624-629`). Any FMEA run wanting both is refused.

### 3.3 Overstated or wrong in v1

- **VOLL slacks are not "per bus, all buses".** `solver_service.py:4426-4430` builds
  `load_bus_set` from `n.loads["bus"]` and skips every bus not in it — deliberately, with
  a documented rationale (a slack on a transit bus lets the LP manufacture energy at VOLL
  price). **Consequence: a contingency that starves a transit bus produces LP
  infeasibility, not a priced ΔEUE** — which breaks the "unserved energy is priced, not
  infeasible" premise for exactly the sector-coupled modes classes A and B care about.
  The sweep driver must classify infeasible contingencies as a distinct outcome, not
  discard them.
  (Note: the docstrings at `routers/results.py:2957`, `frontend/src/api/simulation.ts:388`
  and `LostLoadTab.tsx:21` still say "every bus" — they are stale; the code is right.)
- **No per-load-carrier attribution.** One slack per bus, `carrier="load_shedding"`.
  Where a bus hosts several `Load`s with different carriers — which this codebase
  supports — the single slack cannot say which load was shed. FMEA severity *per load*
  is not available.
- **Links and storage get no slack at all.** A cyclic-SoC or link-capacity infeasibility
  stays an infeasible LP.
- **`lost_load_cost_meur` was always 0.0.** `routers/compare.py` read the capture from
  `n.meta["last_lost_load"]`, which **nothing in the backend ever writes**. v1's claim
  "€-denominated effect, already reconciled against the objective" was false. *Fixed on `claude/fix-lost-load-cost-and-custom-attr-drop`
  (commit `8e2f98d`), off `master`; not this feature's work.*
- **Two divergent lost-load numbers exist.** `solver_service.py:4477` computes
  `total_mwh * voll` **unweighted** ("assumes hourly snapshots"); `:2452` computes it
  **snapshot-weighted per period**. Under representative snapshots — which this repo
  supports via tsam — these disagree. **Phase 0 must pin which is canonical**, or the
  frontier will not match the objective.
- **The scarcity diagnosis is not an effects taxonomy.** It is a per-(bus, snapshot)
  explanation of high LP dual prices, gated at 2000 €/MWh and hard-capped at 200 rows
  (`routers/results.py:2784-2790`); `transmission` is the residual "couldn't explain it"
  bucket. Useful as a hint, not a classification to extend.
- **TimeSeriesManager is not a multi-weather-year seam.** `_user_ts` is keyed
  `(component, attribute, column)` (`routers/network.py:2172`) with no year dimension;
  `PUT /timeseries/{component}/{attribute}` **replaces the entire attribute frame**; and
  it is a **foreground module global** that background solves deliberately skip
  persisting. Holding N coincident years needs either N project copies or a new
  dimension in `_user_ts` **and** the netCDF layout. This is Phase 0 work, not a data
  procurement note.
- **"Severity is already computed" was half true.** The full array exists in the pickle,
  but `GET /results/lost_load` reads `_state` (**foreground only** — a queued background
  solve's capture never appears there), and `_compute_lost_load_summary` collapses to
  totals and **caps per-bus rows at 24**. **No shed-hours / LOLE metric exists anywhere**
  in the backend.
- **`useFilterableTable` is not a table.** It is a 180-line sort + substring-search hook
  plus a search box and a sortable `<th>`. No column defs, no cell rendering, **no
  editable cells** — which the worksheet requires. Six hand-written tables already exist;
  you will write a seventh and get free sorting. (CSV export *does* exist: `downloadCSV`
  in `pages/results/shared.tsx`.)

---

## 4. Constraints the sweep driver must respect

### 4.1 The queue cannot express a sweep

`EnqueueRequest` is `{project_id: str}` (`routers/solve_queue.py:36-37`) and `_run_job`
reads config off the context (`solve_queue.py:389`). **There is no way to enqueue "same
project, VOLL=3000" or "same project, generator G forced out."** v1's "the sweep driver
rides the queue" is not implementable as written. Two options:

1. add a per-job variant payload to the queue, or
2. materialise N scenario projects first via `POST /scenarios` and enqueue those.

Option 2 uses shipped machinery, but `create_scenario` snapshots the **foreground
in-memory network**, so building N points means N sequential (mutate foreground → save →
branch) cycles that clobber whatever the user is editing. Neither option is free; pick
one in Phase 0.

Also: the queue is **abortable but not resumable** — `_jobs`/`_order`/`_q` are in-memory
only, so a backend restart mid-sweep loses it, with no partial-progress record. And
`clear_finished` is **global across orgs** and super-admin-gated, so a 60–100-job
overnight sweep sits in a shared queue an ordinary user cannot clean up.

### 4.2 Do not drive the sweep through the HTTP API

Every write to `/api/network/*` triggers `_push_undo_snapshot()` — a full
`export_to_netcdf()` round-trip that "can take seconds on a large network"
(`main.py:592-613`) — **and clears all dispatch results, resetting the solver lifecycle
to `idle`** (`main.py:658-693`). A per-contingency sweep driven over HTTP pays a netCDF
export *and* a results wipe per mutation. **The driver must mutate `ctx.network`
in-process.**

The real limits on parallelising solves (not HDF5, per §2.2) are: foreground module
globals (`_user_ts`, `PyPSAService._active`, `routers.simulation._state`); one results
slot per project directory, so N variants need N directories regardless; and a
**documented PyInstaller + multiprocessing incident** (`desktop/gui.py:51-86`, dated
2026-08-03) where a `resource_tracker` helper re-executed the bundle and took the
single-instance lock. Subprocess parallelism in the packaged build is possible but has
a scar.

### 4.3 The EENS cap cannot be user code

There is an `extra_functionality` seam, but `_compile_extra_functionality` **hard-refuses
unless `PYPSA_GUI_ALLOW_USER_CODE=1`** (`solver_service.py:1458-1494`) — off by default
because it is an unsandboxed in-process `exec()`. The cap must be a first-class wrapper
alongside `_wrap_with_curtailment_cost` (`solver_service.py:2783`).

---

## 5. Failure-mode taxonomy

| Class | Representation | Occurrence | Severity |
|---|---|---|---|
| **A. Generation forced outage / derating** | COPT state, **not** an LP re-solve | `forced_outage_rate` + `mttr_hours`; multi-state for derating | Analytic leave-one-out on the COPT (§6) |
| **B. Transmission / link outage** | **Existing SCLOPF path** for Line/Transformer; new `p_nom → 0` for **Link** only | Per-branch FOR | LP re-solve — genuinely needed here |
| **C. Correlated weather + demand extreme** | Whole-year swap: availability *and* load together | Empirical frequency of the climate year | LP re-solve per year |
| **D. Fuel / cyber / human-operational** | Qualitative worksheet row; optional model-backed proxy | Expert-entered | Expert-entered, or the proxy re-solve |

Class C is funded (§1): bundle a reference climate-year set so it works out of the box,
rather than degrading to whatever a user uploads. Note this needs the §3.3
multi-year storage work first — the data is useless without somewhere to put it.

Class D remains the reason the formal worksheet exists: these modes are not quantifiable
from the network and must sit beside the computed rows without pretending to be them.

**Not yet modelled, and each materially affects the answer:** planned/maintenance
outages (excluded from FOR by construction, yet dominant in real unavailability),
load-forecast uncertainty, and interconnector availability — the single most contested
input in European adequacy studies.

---

## 6. The two numbers, corrected

### 6.1 Criticality — IEC 60812 FMECA, no RPN

```
f_i  [1/yr]  = 8760 * FOR_i / MTTR_i        # cycle frequency, not failure rate
S_i  [EUR]   = E_t[ΔEUE | outage starts at t] * VoLL + Δopex_excl_load_shedding
C_i  [EUR/yr]= f_i * S_i
```

Three things v1 left undefined and this version pins:

- **`S_i` is an expectation over event timing**, sampled across representative start
  times — not the damage at the annual peak. Equivalently and more cheaply, compute
  annual expected damage directly as `8760·FOR_i × mean hourly damage rate` and drop the
  `f×S` factorisation entirely.
- **Grouped rows carry a multiplicity**, not a group-sized occurrence: severity = removing
  **one** unit of the class, occurrence = per-unit `f_i`, row carries `N`. A genuine
  common-cause mode is a **separate row** with a β-factor-derived occurrence.
- **`Δopex` excludes the `load_shedding` carrier**, or `VoLL·ΔEUE` is counted twice.

**No RPN and no Action Priority.** €/yr criticality is the ranking. Severity and
Occurrence bins may be rendered for readers who expect the columns, but RPN from binned
ordinals is a *product of sums* and is not monotone in `C = f·S` — a mode 10× more
critical can rank lower, visibly, on the first worksheet. AIAG-VDA removed RPN in 2019
for exactly this reason, and its Action Priority is a lookup over all 1,000 S/O/D
combinations, so it is undefined without a real Detection column. Detection has no
natural power-system meaning; the worksheet carries a **mitigability** column instead
(is there reserve, an alternative path, a restart option).

### 6.2 Availability — both numbers, side by side

Per §1, neither is the headline. The tool shows:

- **LP proxy** — storage-aware and network-aware, perfect-foresight, single realisation.
  Right system, biased method.
- **COPT screening** — classical convolution over dispatchable thermal, applied to a
  residual curve netting only exogenous must-take generation, VRE as multi-state
  capacity. Defensible method, wrong system: storage-blind, chronology-free, network-free.

**Their divergence is the product.** A large gap means storage and network are carrying
the adequacy, which is precisely when the classical number misleads and when PRAS or
Antares is worth the export. Presenting one alone invites the reader to trust it.

COPT cost is `O(N · C/Δ)` over a **rounding increment Δ**, not `2^N` — so "fast" is
directionally right, but Δ must be named and its rounding bias handled (capacity
apportioned probabilistically to adjacent rounded states, or the table drifts).
Milliseconds holds for a national model (~300 units); full PyPSA-Eur (10³–10⁴ units) is
seconds-to-minutes, and **multi-area** COPTs with transfer limits are exponential in the
number of areas — which is the actual reason PRAS exists.

### 6.3 The frontier

**Primary parameterisation: the ENS cap.** Linear (`Σ_t w_t Σ_b p_voll,b,t ≤ Ē`), samples
the frontier uniformly by construction, and answers the regulator's question.

**Secondary: the VOLL sweep**, as a *validation* that recovers the cap's shadow price —
not as an independent frontier. v1 presented the two as interchangeable. They are
Lagrangian duals and coincide **only if the value function `C*(Ē)` is convex**. This repo
breaks that: `solver_service.py:939-953` switches to **MILP with a MIP gap** when
`committable` generators are present, so `C*` is non-convex, the VOLL sweep **skips** the
unsupported portions, and returned points may not be on the frontier at all. The
VOLL-frontier path must be **disabled or warned** under unit commitment or a nonzero MIP
gap. The VOLL sweep is also **degenerate**: VoLL only changes the solution at
breakpoints, so 10–15 samples typically recover far fewer distinct points.

**An LOLE hour-count cap is not merely "a MIP".** It is 8760×|B| binaries with a big-M
whose LP relaxation is ~0 (`y_t = shed_t/M`) — numerically hopeless. The tractable
alternatives, both linear and both giving a conservative bound, are a per-hour shed cap
alongside the ENS cap, or a CVaR tail constraint on hourly shed.

**Naming:** the constrained quantity is **ENS (scenario)** everywhere — API, UI,
worksheet. Not EENS. And in a multi-period run `investment_period_weightings.objective`
discounts VOLL×ENS in the objective, so the "economic optimum" is an **NPV** optimum, not
the annualised one a reliability standard implies. Criticality and the frontier must both
state which period they refer to.

---

## 7. Provenance contract

```
AdequacyReport {
  engine:   "lp_proxy" | "copt" | "pras" | "antares"
  fidelity: "deterministic_scenario" | "analytic_convolution" | "sequential_mc"
  metrics:  { ens_mwh, shed_hours, lole_hours?, eue_mwh?,
              confidence_interval?, n_samples?, time_basis }
  inputs:   { weather_years, voll_eur_per_mwh, seed?, assumptions_hash }
  per_mode: [ FailureModeResult ]     # own provenance
  frontier: [ TradeoffPoint ]         # own provenance
}
```

Additions over v1, each closing a way the report could mislead:

- **`confidence_interval` / `n_samples`** — an SMC LOLE without a CI is not reportable,
  so v1's `sequential_mc` tier was unusable as specified.
- **`time_basis`** — LOLE in hours/yr vs days/yr differ by ~24×.
- **`weather_years` / `voll` / `seed` / `assumptions_hash`** — without these, two reports
  are not comparable and the Compare tab will silently diff incomparable numbers.
- **`lolp` dropped** as an annual scalar — it is per-hour/per-state, or it is `LOLE/8760`.
- **`per_mode` and `frontier` carry separate provenance** — they are different analyses.

**No number produced by Phases 0–4 may be compared to a statutory standard.** The LP
proxy understates (perfect foresight, one realisation); the COPT screening is
storage-blind and network-free. The UI must say so at the point of display, not in a
footnote.

---

## 8. Module layout

```
backend/services/adequacy/          # `services/adequacy/` verified free
  taxonomy.py      # the four classes + mode catalogue
  occurrence.py    # FOR/MTTR attrs, per-carrier defaults, consistency validator
  copt.py          # convolution, leave-one-out importance, rounding increment
  sweep.py         # class B/C driver — in-process on ctx.network, never over HTTP
  criticality.py   # f_i * S_i, timing expectation, multiplicity/CCF handling
  worksheet.py     # IEC 60812 rows, computed + manual, CSV export
  engines/
    lp_proxy.py
    pras_export.py     # optional — must take PyPSAService.get_netcdf_io_lock()
    antares_export.py  # optional — via antares-craft
```

`pras_export.py` writes `.pras` **HDF5**; v1 proposed adding a second HDF5 producer to a
process it had just called HDF5-fragile without noticing. It must take the same lock
every other HDF5 path takes.

Frontend: a new `fmea` Results tab is five coupled edits in `pages/Results.tsx` — the
`ResultsTab` union, `VALID_TABS`, the `TABS` array, the render switch, **and the
exhaustive `Record<ResultsTab, CompareTab>` at `:72`**, which will not compile until
`fmea` maps to a member of `CompareView`'s `Tab` union (alias to `'overview'`, as `asset`
does).

---

## 9. Phasing (reordered — COPT before the sweep)

| Phase | Content | Ships |
|---|---|---|
| 0 | FOR/MTTR attributes + defaults + validator; pin the canonical lost-load number; multi-weather-year storage; decide the sweep substrate (queue variant payload vs N scenario projects); `AdequacyReport` contract | Nothing user-visible |
| 1 | **COPT**: convolution, screening LOLE/EUE, leave-one-out criticality for class A | The FMECA ranking — **zero LP solves** |
| 2 | LP proxy availability + the side-by-side comparison with COPT | The divergence diagnostic |
| 3 | Frontier: ENS cap as a first-class wrapper; VOLL sweep as dual validation | The trade-off chart |
| 4 | Class B (**Link** outages + existing SCLOPF), class C (bundled climate years), class D manual rows; the worksheet UI | The formal deliverable |
| 5 | *Optional:* PRAS / Antares exporters | Regulatory-grade validation |

**Phase 0 is not small.** Data persistence for custom attributes is genuinely free —
PyPSA 1.x accepts custom columns, and the netCDF round-trip and `GET` serialisation
require no work. The cost is elsewhere: **five Pydantic schemas** (`extra="ignore"`
silently drops undeclared fields on POST/PUT), roughly **60 mechanical frontend edits**
across `PropertiesPanel.tsx` and `propertyDocs.ts` (6 touch points × 2 attributes × 5
components), plus the multi-year storage work and the sweep-substrate decision.

One Phase 0 hazard is already closed: `_merge_partial_update` used to drop custom
columns on any partial PUT, so `forced_outage_rate` would have been one stray request
away from silent erasure. That is fixed on the branch above; Phase 0 inherits a working
custom-attribute path rather than having to discover this the hard way.

### Deliberately still out of scope

Capacity credit / ELCC — the actual currency of a capacity-vs-availability conversation,
and derivable directly from the COPT as equivalent firm capacity at constant LOLE. It is
the most obvious Phase 6, and it is cheap once Phase 1 exists.

Also unresolved and worth a decision before Phase 3: whether the ENS cap is **global or
per-bidding-zone** (a global cap lets the optimiser concentrate all shedding in one zone
while satisfying it — exactly what a per-zone standard forbids); whether **DSR is counted
as unserved energy** (the `__voll_` slack currently conflates voluntary response with
involuntary curtailment); and whether VoLL is **single or segment-differentiated** (ACER
requires segment-weighted).

---

## 10. Open-source landscape

Unchanged from v1 except where review corrected it:

- **[PRAS](https://nrel.github.io/PRAS/)** (NREL, Julia) — **modified MIT (Expat)**, so
  v1's outstanding licence risk is closed. Sequential Monte Carlo with storage and
  multi-region transfer limits, plus **analytical convolution methods** that partly
  duplicate our COPT. `.pras` HDF5 format makes the exporter a clean seam.
- **[PRAS-Linkage](https://globalpst.org/wp-content/uploads/2_MSeatle_Multi-system-co-modelling-1.pdf)**
  — Python bridge from capacity-expansion output into PRAS. Direct prior art.
- **[Antares Simulator](https://github.com/AntaresSimulatorTeam/Antares_Simulator)**
  (RTE, MPL-2.0 since v9.0) — behind ENTSO-E's TYNDP and RTE's adequacy report. Python
  API [antares-craft](https://pypi.org/project/antares-craft/). File-level copyleft
  imposes nothing on us for writing input files.
- **[PyPSA-Earth Monte Carlo](https://pypsa-earth.readthedocs.io/en/latest/monte_carlo.html)**
  — an `uncertainties` + Latin-hypercube config pattern in our own ecosystem; the
  cheapest model for the sweep config schema.
- **IEEE RTS-96 / RTS-GMLC**, NERC GADS class averages, ENTSO-E ERAA availability
  assumptions — occurrence data. **Pin whether the schema means FOR or EFORd**: GADS
  "FOR" is service-hours-based, adequacy studies use demand-based EFORd, and they differ
  materially for units with reserve-shutdown hours.
- **[`reliability`](https://pypi.org/project/reliability/)** — only if fitting FOR/MTTR
  from a customer's own failure history.
- **FMEA-specific OSS** ([`fmeca`](https://github.com/benranderson/fmeca),
  [`FMECA`](https://github.com/pythonasset/FMECA),
  [`LLMRiskAnalyzer`](https://github.com/YuchenXia/LLMRiskAnalyzer)) — assessed, not worth
  a dependency. Take the IEC 60812 schema; compute the numbers ourselves.
- **PyPSA SCLOPF** — **not** an external item. Already shipped (§3.2).
