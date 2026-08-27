# Design — Solution FMEA & Capacity-vs-Availability Trade-off

**Status:** assessment / design. Not yet an executable plan; no code written.

**Goal:** Add a *solution FMEA* capability to PyPSA Studio — systematically enumerate
how a solved investment plan can fail to serve load, rank those failure modes by
model-computed criticality, and expose the **capacity expansion vs availability
(LOLE/EUE)** trade-off as a frontier the user can navigate.

**Verdict: feasible, and cheaper than it looks — the severity half already exists.**
The tool already prices unserved energy (VOLL slack generators), reports it per bus and
per carrier, and can run unattended multi-run sweeps. What is missing is the
*probability* half (no failure-rate data anywhere in the schema), the sweep driver, and
the worksheet surface.

---

## 1. Scope, as confirmed with the requester

| Question | Decision |
|---|---|
| Deliverable shape | **Both** — model-computed occurrence/severity, rendered into a conforming IEC 60812 / AIAG-VDA worksheet with editable columns and export |
| Rigour of the availability number | **LP-derived proxy is acceptable** (Tiers 1–2); heavy Monte Carlo not required to ship |
| Runtime | **Both** — must work in the packaged desktop build with no new runtimes; PRAS/Antares handoff is an *optional* power-user path |
| Failure modes in scope | **All four classes** — generation forced outage/derating, transmission/link outage, correlated weather+demand extremes, and fuel/cyber/human |

**Resolved tension.** "LP proxy is fine" and "optional heavy engine" only coexist if the
results contract is engine-agnostic from day one. That costs little now and is the
single thing that makes the optional path possible later, so it is treated as
mandatory in Phase 0 even though the proxy is what ships.

---

## 2. What already exists (do not rebuild)

| Capability | Location | Why it matters here |
|---|---|---|
| VOLL slack generators per bus (`__voll_{bus}`, carrier `load_shedding`) | `backend/services/solver_service.py:4405` | Unserved energy is *priced*, not infeasible. **Severity is already computed.** |
| Lost Load results tab, per-bus + per-carrier | `frontend/src/pages/results/LostLoadTab.tsx` | EUE breakdown UI already shipped |
| `lost_load_cost_meur` in economics totals, per period | `solver_service.py:2452`, `routers/compare.py` | €-denominated effect, already reconciled against the objective |
| Scarcity diagnosis (`load_shedding` / `thermal_peaker` / `transmission` / `unattributed`) | `frontend/src/api/simulation.ts:335` | A proto *effects* classification already exists — extend it, don't invent a second one |
| Sequential unattended solve queue (FIFO, abortable, per-project context) | `backend/services/solve_queue.py` | The sweep driver rides this instead of spawning solves |
| Compare tab / `compare-state` | `backend/routers/compare.py` | Diffing two plans is solved |
| Time-series manager | `frontend/src/pages/TimeSeriesManager.tsx` | The seam for loading multiple weather years (failure class C) |

### Naming collision — read this before writing code

`backend/services/failure_taxonomy.py` classifies **solver** failures
(`infeasible`, `unbounded`, `time_limit`). It has nothing to do with **asset**
failures. Do not extend it and do not import from it. New work lives under a distinct
namespace: `backend/services/adequacy/`.

---

## 3. The one real architectural constraint

Solves are **strictly serialized** — one at a time, because netCDF/HDF5 is
process-global thread-unsafe (see the design note at the top of `solve_queue.py`).

Consequence: **sequential Monte Carlo adequacy is categorically out of reach through
the LP solve path.** A credible SMC run is 1,000–10,000 replications × 8760 h. Any
number of that kind must come from a separate, non-LP engine. This is *why* the tiering
below exists — it is not a preference, it is forced by the process model.

The corollary is a budget: the sweep must stay in the tens-to-low-hundreds of solves.
Section 6 gets it there.

---

## 4. Failure-mode taxonomy — all four classes

| Class | How it is represented | Occurrence from | Severity from |
|---|---|---|---|
| **A. Generation forced outage / derating** | `p_max_pu → 0` (full outage) or `× derate` for the asset, held for its MTTR window | Per-asset `forced_outage_rate` + `mttr_hours`; defaults by carrier | Fixed-capacity operational re-solve → ΔEUE, Δshed-hours, Δcost |
| **B. Transmission / link outage** | `s_nom` / `p_nom` → 0 for the branch | Per-branch FOR (per-circuit, optionally scaled per km) | Same re-solve path |
| **C. Correlated weather + demand extreme** | Swap the **entire** weather year / stress profile — availability *and* load together | Empirical frequency of the stress year (e.g. 1-in-20) | Same re-solve path |
| **D. Fuel / cyber / human-operational** | Qualitative worksheet row; optionally a model-backed proxy (e.g. zero every gas generator in a region) | **Expert-entered S/O/D** | Expert-entered, or the proxy re-solve where one is defined |

**Class C cannot be done with independent outage draws** — that is the whole point of
it. Dunkelflaute, drought-hit hydro and cold snaps hit supply and demand *together*,
so the correlation must come from real coincident weather-year data, swapped in
wholesale as a deterministic scenario. This fits the Tier 1 sweep machinery unchanged;
what it needs is **data** (multiple coincident weather years of `p_max_pu` + load),
not a new solver mode.

**Class D is why the formal worksheet wrapper earns its keep.** These modes are not
quantifiable from the network. They live as manual rows alongside the computed ones,
which is exactly the hybrid the requester asked for.

---

## 5. The two numbers the feature produces

### 5.1 Criticality (the FMECA number)

For failure mode *i*:

```
lambda_i  [1/yr]  = 8760 * FOR_i / MTTR_i          # expected events per year
S_i       [EUR]   = dEUE_event * VoLL + d(operating cost)
C_i       [EUR/yr]= lambda_i * S_i                 # criticality
```

`C_i` is a genuine FMECA criticality number in money, not an ordinal guess — which is
the substantive upgrade over a workshop FMEA. The classic worksheet columns are
derived *from* it: bin `lambda_i` and `S_i` onto 1–10 log scales to populate
Occurrence and Severity, so RPN / Action Priority exist alongside the €/yr for readers
who expect the standard table. Detection has no natural power-system meaning; it is
either left expert-entered or replaced by a **mitigability** column (is there reserve,
an alternative path, a restart option). Both renderings come from one computation.

### 5.2 The capacity-vs-availability frontier

Two parameterisations, deliberately both:

- **VOLL sweep** — re-run the expansion with VOLL over ~10–15 values. Free today: VOLL
  is already a solve knob and lost load is already a reported result. Each point gives
  (investment cost, EUE, shed-hours). The economic optimum is where marginal capacity
  cost equals `VoLL × marginal EUE avoided` — the ACER / ENTSO-E reliability-standard
  logic, reproduced from the model rather than assumed.
- **EENS-cap sweep** — add a global expected-unserved-energy cap and sweep it. Needs a
  new constraint, but answers the question a regulator actually asks: *what does it
  cost to hit this standard?*

**An EENS cap is linear. An LOLE (hour-count) cap is not** — it needs binaries and
turns the LP into a MIP. Constrain EENS; report LOLE as a diagnostic.

---

## 6. Making the sweep affordable

Naive cost is ~60–100 solves (30–60 asset groups + 5–10 weather years + ~12 frontier
points). Three reductions, all standard practice, bring that within an overnight queue
run:

1. **Contingency sweep re-solves the operational problem only** — capacity fixed,
   extendability off. It is a dispatch question, not an investment question. This is
   the single biggest saving.
2. **Group assets before sweeping.** One row per (carrier × region) or per unit class,
   not per unit. Per-unit resolution is available on demand for the top-ranked rows.
3. **Restrict the contingency re-solve to peak-risk hours** — e.g. the top few hundred
   net-load hours — rather than all 8760. Full-horizon confirmation runs only for rows
   that rank high.

The sweep must be **resumable and abortable**; the FIFO queue is already both, so the
driver should enqueue jobs rather than manage its own concurrency.

---

## 7. Getting a real LOLE without Monte Carlo

Worth calling out because it changes what is achievable inside the "LP proxy is fine"
budget: for the **generation-adequacy** part of class A there is a classical analytic
route that needs no simulation at all.

Build a **Capacity Outage Probability Table** by convolving the independent two-state
(FOR) distributions of every unit, then apply it to the residual load-duration curve
the LP already produces. That yields genuine LOLP / LOLE / EUE — probabilistic, not
proxy — in milliseconds of numpy. No Julia, no new runtime, ships in the desktop bundle.

Its limits must be stated as loudly as its benefits: COPT × LDC handles **energy-limited
resources (storage) and transfer limits badly or not at all**, and it is chronology-free.
That is precisely the gap PRAS and Antares exist to fill. So:

- **COPT/LDC** → the headline generation-adequacy LOLE, everywhere, free.
- **PRAS / Antares export** → the storage-and-network-honest number, optional.
- **LP sweep** → severity, criticality ranking, and the cost frontier.

## 8. The honesty contract

The repo already has a "trustworthy numbers" workstream; this feature must not
regress it. Every adequacy metric crossing the API carries its provenance:

```
AdequacyReport {
  engine:   "lp_proxy" | "copt" | "pras" | "antares"
  fidelity: "deterministic_proxy" | "analytic_probabilistic" | "sequential_mc"
  metrics:  { eue_mwh, lole_hours, lolp, ... }   # each tagged with its fidelity
  per_mode: [ FailureModeResult ]
  frontier: [ TradeoffPoint ]
}
```

The UI labels a proxy number as a proxy. **A deterministic, perfect-foresight LP on a
single weather year systematically understates LOLE** — perfect foresight pre-positions
storage against events no real operator can see coming, and one weather year misses the
tail entirely. Tiers 1–2 give a defensible *relative* ranking and the *shape* of the
frontier. They do not give a number that should be compared against a statutory 3 h/yr
standard, and the UI must not let a reader believe otherwise.

---

## 9. Proposed module layout

```
backend/services/adequacy/
  taxonomy.py      # the four failure classes + mode catalogue
  occurrence.py    # FOR/MTTR attributes, per-carrier default library
  sweep.py         # contingency + frontier sweep driver (enqueues onto solve_queue)
  copt.py          # capacity outage probability table -> LOLP/LOLE/EUE
  criticality.py   # lambda * severity -> C_i, plus the ordinal S/O/D binning
  worksheet.py     # IEC 60812 / AIAG-VDA row assembly, computed + manual
  engines/
    lp_proxy.py    # default, ships everywhere
    pras_export.py     # optional: .pras HDF5 writer
    antares_export.py  # optional: study writer via antares-craft
```

Frontend: a new Results tab (`FMEA`) plus a frontier chart. The worksheet is a
filterable table — reuse `useFilterableTable.tsx`, do not write a third table.

---

## 10. Open-source landscape

**Leverage:**

- **[PRAS](https://nrel.github.io/PRAS/)** (NREL, Julia) — the reference sequential
  Monte Carlo adequacy engine. Native LOLE/EUE, storage, multi-region transfer limits,
  ships RTS-GMLC as a test system, and has a defined `.pras` HDF5 format, so an
  exporter is a clean seam with no linking. Cost: a Julia runtime — unacceptable as a
  hard dependency for the PyInstaller desktop build, hence *optional export only*.
  **Licence not verified; confirm before depending on it.**
- **[PRAS-Linkage](https://globalpst.org/wp-content/uploads/2_MSeatle_Multi-system-co-modelling-1.pdf)**
  (SESIT / UVic) — a Python bridge from capacity-expansion output into PRAS. Direct
  prior art for the CEM→adequacy handoff proposed here; read before designing the
  exporter.
- **[Antares Simulator](https://github.com/AntaresSimulatorTeam/Antares_Simulator)**
  (RTE, **MPL-2.0** since v9.0) — the engine behind ENTSO-E's TYNDP and RTE's French
  adequacy report. Sequential Monte Carlo, LOLE/LOLD/ENS at European scale, and the most
  regulator-credible option in Europe. Python API:
  **[antares-craft](https://pypi.org/project/antares-craft/)**. MPL-2.0 is file-level
  copyleft; writing *input files* for it creates no obligation on our source.
- **[PyPSA-Earth Monte Carlo](https://pypsa-earth.readthedocs.io/en/latest/monte_carlo.html)**
  — an `uncertainties`-config + Latin-hypercube sampling pattern already in our own
  ecosystem. Cheapest thing to imitate for the parametric sweep config schema.
- **[PyPSA contingencies / SCLOPF](https://docs.pypsa.org/latest/user-guide/optimization/contingencies/)**
  — N-1 security for network *flows*. Complements adequacy and is the natural engine
  for failure class B where deliverability, not supply, is the binding issue.
- **IEEE RTS-96 / RTS-GMLC** and NERC GADS class averages — the standard open sources
  for the FOR / MTTF / MTTR data class A needs. ENTSO-E's ERAA availability assumptions
  are the European equivalent.
- **[`reliability`](https://pypi.org/project/reliability/)** (Python) — Weibull / ALT
  fitting and repairable-systems models. Useful only if we later want to *fit* FOR and
  MTTR from a customer's own failure history instead of using book values.

**Assessed and rejected as dependencies:** the FMEA-specific open source is thin.
[`benranderson/fmeca`](https://github.com/benranderson/fmeca),
[`pythonasset/FMECA`](https://github.com/pythonasset/FMECA) (a Streamlit RCM+FMECA app
— a reasonable *UI* reference given our `gui_streamlit/` history) and
[`LLMRiskAnalyzer`](https://github.com/YuchenXia/LLMRiskAnalyzer) are all small
projects. FMEA software is ~90% a structured table plus scoring rules. Take the
**schema** from IEC 60812 / AIAG-VDA and compute the numbers ourselves; adding a
dependency here buys nothing and constrains the worksheet.

---

## 11. Risks and gaps

1. **No failure-rate data exists in the schema today.** `forced_outage_rate` and
   `mttr_hours` are new attributes on generators, storage, links and branches, plus a
   per-carrier default library and UI fields. This is Phase 0 and it is unavoidable.
2. **Class C needs multi-year coincident weather + load data**, which the tool does not
   ship. Without it, class C degrades to whatever stress profiles the user uploads.
3. **Perfect-foresight optimism** (see §8). Optionally mitigated by running the
   contingency re-solve rolling-horizon rather than full-foresight.
4. **Sweep runtime** is overnight-scale on a national model and impractical on a full
   PyPSA-Eur network without the §6 reductions.
5. **Licence verification** outstanding for PRAS.
6. **Naming collision** with `failure_taxonomy.py` (§2).

---

## 12. Proposed phasing

| Phase | Content | Ships what |
|---|---|---|
| 0 | FOR/MTTR data model, default library, failure-mode catalogue, `AdequacyReport` contract | Nothing user-visible; unblocks everything |
| 1 | Contingency sweep driver + criticality ranking (classes A, B) | The ranked FMECA table |
| 2 | COPT/LDC analytic adequacy | A *real* generation-adequacy LOLE/EUE, desktop-safe |
| 3 | Frontier: VOLL sweep, then EENS cap | The capacity-vs-availability trade-off chart |
| 4 | FMEA worksheet UI — computed rows + manual class-D rows, S/O/D + AP, export | The formal deliverable |
| 5 | *Optional:* PRAS and Antares exporters | Regulatory-grade validation path |

Phases 1–3 are independent of 4 and can be validated head-first through the API.
Phase 5 is genuinely optional and should not gate the rest.
