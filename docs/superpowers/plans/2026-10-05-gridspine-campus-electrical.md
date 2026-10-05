# gridspine: from a campus investment decision to its electrical design

Written before the code, as every gridspine increment is. It will be amended
where building it proves it wrong. This plan **replaces** the
frequency-nadir increment, which was dropped: there is to be no dynamic
modelling for now (owner, 2026-10-05).

## The goal

A campus or energy hub (data-centre load, BESS, PV, gensets) is sized by
the capacity-expansion model in pypsa-gui: assets and hourly dispatch per
investment period. The next question is electrical:

1. **Which transformers** the campus needs, and their MVA.
2. **How much reactive power** it needs at the point of common coupling
   (PCC). Of that, how much the BESS and PV inverters can supply, and how
   much must be **compensation** (a STATCOM or capacitor bank).
3. **Whether the PCC complies with the grid code** chosen for the study.

This is answered with **AC load flow at a small set of critical hours**,
not dynamics.

## Owner decisions (2026-10-05)

| question | decision |
|---|---|
| Where the campus electrical network comes from | **Both.** Auto-generate a default single line from the solved project, which the user can edit, **and** accept a user-supplied single line. |
| Which years to study | **Each investment period**, using one representative year per period. |
| German grid codes (VDE-AR-N 4110/4120, not freely available) | **EU codes only for now.** VDE is added later. |
| PCC voltage for the test case | **Both 110 kV and 20 kV.** |

**Consequence of decisions 3 and 4, recorded so it is not forgotten.**
- The EU codes (RfG/DCC) set steady-state voltage bands only from 110 kV
  upward.
- DCC Art. 15 binds transmission-connected demand facilities. A campus
  connected at 20 kV is distribution-connected, and its real requirements
  come from national codes (VDE-AR-N 4110 in Germany).
- So, until VDE is added:
  - the 20 kV case runs on the existing `assumed` ±10 % band;
  - its PCC reactive requirement is a profile setting tagged `assumed`;
  - the report says so on every such row.

## What exists today (surveyed 2026-10-05)

**The hub model.** The *Data Center Energy Hub* template is a three-bus
network: `grid` at 132 kV, `dc_mv` at 33 kV and `it_bus` at 11 kV. It carries
the IT, cooling and office loads, gensets, PV, a UPS battery, and candidate
gensets and BESS. Its PCC is tagged `eh_poc` with `eh_sk_mva`.
- Its grid connection and site transformer are **PyPSA Links**: no
  impedance and no reactive power.
- It solves **one representative 168 h week**.
- The solver supports investment periods, but no multi-period campus
  template exists yet.

**gridspine.**
- The analysis engines are generic: AC load flow, contingency screening,
  IEC 60909, the capacity search and the connection checks all take any
  pandapower `net`.
- But every driver loads the **IEEE 39-bus case** (`load_case39_res`),
  and these are case39-specific:
  - the 60 Hz constant;
  - the case name;
  - the unit templates;
  - a rule that every sgen is wind or solar;
  - the grid-import model of the PCC.
- There is **no storage unit kind**.
- **PCC reactive power is never read**: `LFResult` keeps only the slack P.
- Transformer loading is already checked in every screen.

**The link between them.** `set_dispatch_source({"from_project": …})`
requires an identity map between the project's generators and case39's
registry. A hub project fails that check, and its Links and StorageUnits
are ignored.

## Increments

Each increment is its own PR, with the same discipline as before:
- tests first, worked by hand or against an independent oracle;
- mutations;
- full gates;
- a browser run for anything the user sees.

### C1: the campus electrical network

- **`gridspine.ingest.campus`** builds a pandapower `net` from a campus
  description:
  - the PCC bus with an `ext_grid` that carries the **grid operator's
    Sk''max/min and R/X** (not case39's derived values);
  - transformers with MVA, kV, uk %, P_cu and vector group;
  - MV and LV buses;
  - cables or lines;
  - assets as typed units: `load`, `bess`, `pv`, `wind` and `genset`, each
    with a P rating, an inverter MVA or machine data, and a Q capability.
- **A campus description file** (YAML), validated like the unit templates.
  Every value is tagged `measured | datasheet | assumed`.
- **Auto-generation from a solved pypsa-gui project.**
  - Each hub bus becomes a bus at its `v_nom`.
  - Each `eh_role` grid-import or transformer Link becomes a transformer,
    sized from the optimised `p_nom_opt`, with typical uk % and losses for
    its MVA class (tagged `assumed`).
  - Generators, StorageUnits and Loads become typed units, rated from
    `p_nom_opt` / `e_nom_opt`.
- **User-supplied single line.** The same YAML, written or edited by the
  user. The generated file *is* that YAML, so "edit the default" and
  "supply your own" are one path.
- **Generalising the study.** A study gets a network source (`case39` or a
  campus file), recorded in the run manifest so that every later step
  rebuilds the same net. The case39 constants move into that source.
- The `storage` unit kind, with signed P (charging is negative), in the
  registry and the dispatch contract.

### C2: the hourly results, per investment period

- A producer reads the solved project's `network.nc` and writes, per
  investment period (one representative year each):
  - asset P from `generators_t.p`, `storage_units_t.p` and the Link flows;
  - the loads;
  - grid import and export at the PCC, from the grid-import Link.
- It maps by the names in the campus file, **not** by an identity with
  case39.
- The hour index carries the period. A study holds all periods and reports
  per period.
- A test case: the Data Center hub with two or three investment periods
  (years 1, 10, 20), assets added between them.

### C3: critical hours for a campus

- New ranking criteria, selected when the study is a campus:
  - peak PCC import;
  - peak PCC export;
  - peak transformer loading (from a DC estimate: P per transformer);
  - high generation with low load (the voltage-rise hour);
  - peak load while the BESS charges;
  - peak reactive demand.
- These replace inertia and IBR share, which mean little behind a PCC.
- The same top-k-per-criterion union and tie-spreading as today, k = 3 by
  default, so roughly 10–20 hours per period.

### C4: sizing at the critical hours

At each selected hour, AC load flow intact plus N-1 on the transformers
(when there are two or more). The study reports:

- **Transformers.** Required MVA:
  - the peak apparent power through each transformer, intact and N-1;
  - against its rating, with a margin (default 20 %, tagged `assumed`);
  - the next standard size up (a configurable list).
- **Reactive power at the PCC.**
  - The PCC Q and cos φ at each hour.
  - The Q the code requires there, from the chosen profile.
  - The Q the inverters can supply, from their capability curves:
    BESS and PV at their MVA, with P priority.
  - The **shortfall, which is the compensation in Mvar**, sized to the
    worst hour, split into inductive and capacitive.
- **Voltages.** Every bus against its band, intact and N-1.
- **Short circuit.** IEC 60909 at the PCC and the MV busbars, against an
  equipment duty the user enters (kA), as a switchgear check.
- **Losses.** Transformer and cable losses at each hour, which feed back
  into the economics.

All of this goes to `campus_sizing.csv` in the run and in the bundles,
and onto the panel.

### C5: selectable grid codes, per study

- A study picks a **region and code**, stored in its config:
  - for now, the existing **EU RfG/DCC (CE)** profile;
  - with the profile structure extended to carry:
    - the PCC reactive requirement as a **Q(P) / cos φ range**, per
      demand and generation, instead of one number;
    - the voltage-change limit;
    - the PCC voltage band.
- Every limit keeps its clause and `code`/`assumed` tag, so a new country
  is a data change, not a code change.
- **VDE-AR-N 4110 (MV) and 4120 (HV)** are the next profiles, deferred
  until their texts can be checked (owner decision 3).
- The PCC compliance report has, per check:
  - pass or fail;
  - the worst hour and period;
  - the value against the limit;
  - the clause;
  - the tag.

### C6: panel, copilot and report

- A *Campus electrical* section with:
  - the network source (generate from project, or upload or edit the YAML);
  - the periods;
  - the critical hours;
  - the sizing table (transformers, compensation, voltages, short circuit);
  - the compliance table.
- Read and write chat tools, as in increments 9 and 10.
- The bundle carries the campus YAML and the sizing table, for the
  engineer's detailed design.

## Out of scope, and ledgered

- Dynamics: fault ride-through, RoCoF, frequency.
- Harmonics and flicker.
- Protection coordination.
- Cable thermal sizing beyond the loading check.
- Earthing.
- VDE limits, until the texts are checked.

## Order and first step

C1 comes first, because everything else needs a campus network. Its first
deliverable is the campus YAML contract and builder, tested against a
hand-built two-transformer campus. That campus's load flow and short
circuit are checked against pandapower solved directly. Auto-generation
from the hub template follows in the same increment.

## C1 as built (2026-10-05)

**Stage A: `gridspine.ingest.campus`.**
- Builds the campus description into a pandapower net:
  - the PCC as an `ext_grid` with the grid operator's Sk''max/min and R/X;
  - transformers, buses and cables;
  - units of kind load, bess, pv, wind or genset.
- Every number carries a tag, as in the unit templates.
- An impossible or untagged campus is refused at load.
- Units are PQ injections. A genset runs in power-factor control, so it is
  not a voltage-controlling `gen`. For IEC 60909 it is screened as a current
  source of 1/x''d (ledgered).
- Tests:
  - The build solves identically to the same network written directly in
    pandapower.
  - The PCC Ik'' and peak current come out by hand from Sk'' and R/X.
  - 12 mutations, all caught.

**Stage B: `gridspine.producers.campus.draft_campus`.**
- Drafts the campus file from a solved hub network.
- Transformers are sized from the optimised MW at pf 0.95, rounded up to
  the next R10 size, with typical impedance by size class.
- Assets are typed by carrier. Unbuilt assets are skipped and listed.
- Every supplied value is tagged `assumed`.
- All three pypsa-gui hub templates draft, build and converge.
- One real bug was found and fixed: a NaN `eh_poc` marked a second PCC.
- 16 mutations, all caught.

**Amended.** The plan's "generalising the study" step is replaced by a
**separate campus driver**, arriving with C2. The case39 year study does
transmission work a campus does not need: unit commitment, N-2, and PSS/E
bundles.
- Rebuilding it would put the existing studies at risk for no gain.
- The campus driver takes:
  - the campus file;
  - the solved project.
- It writes:
  - the hourly tables per period;
  - the campus ranking;
  - the sizing results.
- Its manifest records both inputs, so every later step rebuilds the same
  network.
- The `storage` unit kind arrives with the campus dispatch tables in C2.

## C2–C5 as built (2026-10-05)

**C2. Hourly results** (`producers/campus.campus_hourly`, `schema/campus.py`,
`drivers/campus_study.py`).
- Units are found by their `pypsa_name`.
- The hourly table carries signed injections per investment period: loads,
  battery charging and electrolysers are negative. An asset not built in a
  period has status 0.
- The PCC table carries the model's own import, from the Links touching the
  PCC.
- Real-data bug: a saved network drops all-zero time series. A solved
  project's idle asset now reads as 0 MW rather than "unsolved".
- `prepare_campus` validates before it writes, and records both inputs by
  sha256.

**C3. Critical hours** (`ranking/campus.py`). The criteria are:
- PCC import and export;
- total consumption;
- PV and wind output;
- the flow through each transformer group, taken as |net injection
  downstream| (lossless, radial; parallel units form one group; a meshed
  group gets no estimate).

The top-k union per criterion is taken within each period, reusing the
case39 tie spreading. A criterion that never occurs selects nothing.

**C4. Sizing.**
- **(a) Transformers** (`static/campus_flow.py`). Every selected hour is
  solved by AC load flow, intact and with each parallel unit out. The
  required rating per unit is max(intact share, N-1 survivor) × (1 +
  margin), rounded up to the next standard MVA.
  - Margin: 20 %, assumed.
  - N-1: on by default.
- **(b) Reactive power** (`static/campus_reactive.py`). The PCC exchange is
  held within ±q_frac × P_ref.
  - q_frac is the DCC 0.48 by default, or tan(acos(pf)) from the study's
    connection agreement.
  - P_ref is `p_connection_mw`, else the peak import.
  - The inverters cover the need first, up to √(S² − P²). Only a battery
    offers Q at 0 MW; an idle genset or a dark PV does not.
  - The rest becomes compensation at the low-voltage side of the PCC
    transformer, in Mvar, capacitive or inductive, iterated onto the band
    edge.
- **(c) Short circuit** (`static/campus_sc.py`). IEC 60909 max and min,
  per period, with the installed units energised. Each bus is judged
  against its `ik_rated_ka`:
  - Ik'' must not exceed the rating;
  - the peak must not exceed 2.5× the rating at 50 Hz, or 2.6× at 60 Hz
    (IEC 62271-1 ratio, assumed).

**C5. Compliance** (`static/campus_compliance.py`). There is one row per
check:
- the PCC reactive band;
- the PCC voltage (code band);
- the campus voltages (design limit, assumed);
- transformer loading;
- switchgear.

Each row gives the status as is and with the recommended measures. Voltages
show "not re-checked" when a measure is recommended. `list_grid_codes()`
lists the profiles a study can use.

**On the solved Data Center Energy Hub template** (168 h, one period):
- 14 critical hours were selected.
- The import is capped at 40 MW every hour.
- The 132/33 kV transformer carries 43 MVA. It needs 52 MVA with the
  margin, so the recommendation is 63 MVA, against the 50 MVA drafted.
- The PCC reactive power is 14.5–16.8 Mvar:
  - inside the EU band (±19.2 Mvar), so no compensation is needed;
  - outside a power-factor-0.95 agreement (±13.1 Mvar), which the units
    cover (3.3 Mvar), so again no compensation is needed.
- Fault levels are 2.7, 9.2 and 23 kA at 132, 33 and 11 kV. The template
  has no switchgear ratings, so the switchgear check is "not rated".

**Left: C6.**
- The panel section.
- The chat tools.
- Backend routes: draft from the project; save and edit the campus file;
  prepare, rank and size.
- A browser run.

## C6 as built (2026-10-05)

**Backend.** `services/campus_electrical_service.py` keeps a hub project's
study in `<project dir>/campus_electrical/`: the editable
`campus_input.yaml`, the draft notes, the settings and the engine's run.
It offers:
- `get_state`, including a `stale` flag from the engine manifest's hashes;
- `draft`, which will not overwrite the user's edits unless asked;
- `save_campus`, which validates by building the campus;
- `run`, which prepares, ranks and sizes.

It refuses with these codes:

| case | code |
|---|---|
| a project of another kind | 409 |
| no saved network, an unsolved network, or bad settings | 422 |
| a campus file over 1 MB | 413 |
| a build without the engine | 503 |

The gridspine imports are guarded, like `gridspine_service`'s.
`routers/campus_electrical.py` serves `/api/campus-electrical` with
GET, `/draft`, `PUT /campus` and `/run`. Drafting and running go to the
threadpool, and writes check the edit lock.

**Frontend.** `CampusElectricalPanel` covers:
- the campus file: draft from project, edit, save, draft again (with a
  confirm);
- the settings: hours per criterion, connection-agreement power factor,
  grid code, margin, N-1;
- the results: compliance as is and with measures, transformers,
  compensation, short circuit, critical hours, and a stale warning.

It states that the study is steady-state and not a certificate.

**Copilot.** Three tools:
- `campus_get_study` (read);
- `campus_draft_campus` (write);
- `campus_run_study` (write).

**Owner request: a Studies group.** Planning → dynamics, Campus electrical
and Reports moved out of Simulation into their own STUDIES sidebar section,
with its own icon in the collapsed strip.

**Browser run** (Data Center template, solved with HiGHS):
- open the project, then Studies → Campus electrical;
- draft from the project, and run at power factor 0.95, which takes 2.9 s;
- every table renders, and match the engine's numbers;
- a campus with sk_min above sk_max is refused inline, naming the field.

One cosmetic fix came from the run: a cut name no longer ends in `_`.

## Part two: investing in the electrical assets (owner, 2026-10-05)

Written before the code. Part one *recommends* a transformer size and a
compensation rating. Part two makes the electrical assets an **investment
decision**, with the same economics the capacity expansion uses:

- capex, fixed opex and lifetime, annualised;
- chosen from a library;
- re-checked by AC load flow at every critical hour, so that voltages, flows
  and the grid code all hold with the chosen assets in place.

### Owner decisions (2026-10-05, second round)

| question | decision |
|---|---|
| How the assets are chosen | **Both.** A least-cost pick, AC-checked, comes first. A MILP follows, run in a loop with adaptive convergence to absorb the linearisation error. |
| Whether electrical cost feeds back into the hub's capacity expansion | **Report alongside**, as electrical capex, opex and annualised cost per period, next to the hub's system cost and as a total. The PyPSA project is not changed. |
| How a grid code is given | **Upload the document.** The copilot drafts a profile, the user reviews and saves it, and nothing is used before then. Until a real code is supplied, a **generic `assumed` profile** is used. |
| Where the asset library lives | A **shipped default**, plus a **per-project copy** editable in the panel. |
| Agents | Coordination by the session model; implementation is delegated to subagents, with the model chosen per task. |

### How a grid code reaches the study (recommendation, accepted)

The easiest route is to **upload the PDF**. The copilot drafts a profile from it:

1. **The upload.** It goes to the project (`campus_electrical/grid_codes/`), never to the repository. Licensed texts such as VDE stay with their licensee.
2. **The draft.** The copilot reads the document and drafts a profile in the same schema as `grid_codes.yaml`:
   - each limit carries the clause, the page and a verbatim quote;
   - each value is tagged `extracted`, a new tag.
3. **Validation.** The draft passes the same loader as a shipped profile, so an impossible band or a missing clause is refused with the field named.
4. **Review.** The user reviews it in a form, side by side with the quote, and confirms each limit; a confirmed limit becomes `code`. A limit left unconfirmed stays `extracted` and is shown as such on every report row.
5. **Without a key.** The extraction needs `ANTHROPIC_API_KEY` (or `PROBE_ANTHROPIC_KEY` mapped onto it at launch) in the environment settings. A session sees a new environment variable only when it starts. Without one, the form alone works: the user types the limits in.

### C7: the asset library and the generic grid code

- `gridspine/templates/data/campus_assets.yaml` is the shipped default. Every value is `{value, source}`, as in a campus file. The catalogue:

  | kind | entries |
  |---|---|
  | transformers | hv/lv kV, MVA, vk %, vkr %, no-load losses kW, i0 %, in R10 sizes at 132/33, 110/20, 33/11, 20/0.4 kV |
  | cables | kV, cross-section, R, X and C per km, rated kA; capex per km |
  | capacitor banks | kV, Mvar, number of switched steps |
  | shunt reactors | kV, Mvar |
  | STATCOMs | kV, ±Mvar, losses % |
  | switchgear | kV, rated Ik'' and peak kA; capex per bay |

  Each entry carries `capex_eur`, `opex_frac` (fixed opex per year as a fraction of capex) and `lifetime_a`. The library has one `discount_rate`, plus its currency and price year.
- **Costs.** Every cost is tagged `assumed`, with a note saying it is an order of magnitude to be replaced by quotes. No price is presented as sourced unless it was checked against a text.
- `gridspine/templates/campus_assets.py` holds:
  - `load_asset_library(path=None)`, which validates units, tags, positive ratings and unique ids, and refuses unknown fields;
  - `annuity(r, n)`, the same formula as pypsa-gui's `solver_service._annuity`, which a test checks;
  - `annualised_cost(entry, library)`.
- **`generic_assumed` profile** in `grid_codes.yaml`, every limit tagged `assumed`:
  - PCC voltage 0.90–1.10 pu at every kV;
  - PCC reactive band ±tan(acos 0.95) = 0.329 × P_ref;
  - RVC 3 %;
  - an optional `campus_voltage` band of 0.95–1.05 pu.

  A profile may now carry `campus_voltage`. Without it, the existing behaviour holds: the code bands are used as an `assumed` design limit.

### C8: least-cost selection, AC-checked

For each **need** the study finds, the candidates come from the library, at the right voltages, and are ranked by annualised cost (capex annuity + opex):

| need | candidates |
|---|---|
| a transformer group | n units × size; with N-1, the survivors must carry the group |
| the reactive gap, capacitive and inductive | capacitor banks (steps), shunt reactors, STATCOMs, and combinations that cover both directions |
| an overloaded cable | the next cross-section up, or a parallel run (a new `cable_loading` check) |
| an under-rated bus | a switchgear bay rating, × the number of bays at that bus |

**The loop:**
1. Take the cheapest combination.
2. Apply it to the campus net.
3. **Re-solve AC at every critical hour and case.**
4. Escalate a need to its next-cheapest candidate while any check fails.

This closes part one's "voltages not re-checked": the *with measures* column is now a real AC result.

**Timing.** An asset is invested in the first period that needs it, and carried to the end of its life.

**Outputs:**
- `campus_investment.csv`: asset, need, period, units, capex, opex, annualised cost;
- `campus_cost.csv`: the annualised electrical cost per period;
- the compliance table, re-solved with the chosen assets.

### C8 as built (2026-10-05)

**The campus file** (`ingest/campus.py`).
- An optional `compensation` list: `name`, `bus`, `kind` (`capacitor_bank`, `shunt_reactor`, `statcom`), a tagged `q_mvar`, `steps` for a bank, optional `library_id` and `pypsa_name`.
  - A capacitor bank is a pandapower `shunt` with a **negative** `q_mvar` per step (pandapower's load convention, checked by solving), `max_step = steps`, starting at step 0.
  - A reactor is a one-step positive shunt, also starting off.
  - A STATCOM is a controllable `sgen` at 0 MW, ±`q_mvar`; for IEC 60909 a current source of 1.2× its rating (the drafted inverter figure, ledgered). Shunts feed no fault current.
- `existing: true` and `library_id` on a transformer or cable; `parallel` runs on a cable.
- A file without these builds as before.

**The dispatch** (`static/campus_reactive.py`). Inverters, then STATCOMs (continuous), then the fewest bank or reactor steps that bring the PCC into the band (the closest count when none does); the continuous sources then back off onto the band edge, never past zero. What is still missing is part one's residual, or, with `residual=False`, nothing: the PCC is where the real equipment puts it. `solve_cases(setpoints=...)` holds the dispatch in every case, N-1 included; every case reports the cable loading.

**The selector** (`static/campus_invest.py`, `select_assets`). The choices the plan left open:
- **Needs.** Every transformer group; the reactive gap (with the sizing margin); every cable over 100 %; every bus over its switchgear rating **and every unrated bus with library switchgear at its voltage**. A cable or bus first failing in a re-check becomes a need then.
- **Candidates.** Transformers: n = 1..3 units of one size; intact share and (n ≥ 2, N-1 on) the survivors' share within the rating with the margin; a redundant group stays n ≥ 2; existing adequate units are a zero-cost "keep". Reactive: at most one entry of each kind, 1..3 units each, in the five combinations of the brief (under 200 with the shipped library). Cables: 1..3 runs of one section carrying the worst current with the margin. Switchgear: every rating that passes `judge`, times the **bays**: each transformer unit and cable run at the bus, each unit and compensation entry there, and the grid at the PCC.
- **Check → need.** Transformer loading over 100 % **or the sizing rule on the re-solved flow** → that group; cable loading → that cable; PCC reactive → the reactive need, to the next candidate that adds Mvar in the short direction; a voltage → the reactive need, but if that does not shrink the excursion the need is unresolved, naming the tap change (out of scope); switchgear → that bus. A re-solve that does not converge stops the loop as an unresolved "load flow".
- **Stop.** All checks pass; a need runs out (the state with its last candidate is re-checked once more, so the report is an AC result); non-convergence; 25 iterations.
- **Timing.** Transformers from the first period; a compensation item from the first period the final re-check dispatches it; a cable from the first period it is overloaded; switchgear from the first period its rating fails, or the first period if it had none. Costed while `invest <= period < invest + lifetime`; no replacement. Unresolved needs are priced but not summed.
- Fault levels in the re-check energise every STATCOM in every period (conservative).

**The driver.** `invest_campus(run_dir, library=None, ...)` needs a prepared and ranked run, recomputes part one with the same profile and power factor, and writes `campus_investment.csv`, `campus_cost.csv`, `campus_invested.yaml`, `campus_compliance_invested.csv`, `campus_invest_history.csv` and (added) `campus_invest_dispatch.csv`. `size_campus` is unchanged.

**On the solved Data Center Energy Hub template** (one period, pf 0.95 agreement, shipped library): 1 × 63 MVA 132/33 kV, 3 × 25 MVA 33/11 kV (no 33/11 unit above 25 MVA in the library, and three keep N-1), switchgear 31.5 kA at 132 kV (2 bays), 25 kA at 33 kV (11 bays) and 11 kV (5 bays), no compensation: €6.89 M capex, €610 k a year. Every check with measures passes, re-solved; no escalation was needed.

**Mutations.** 39 by hand across the campus file, the dispatch and the selector. 26 were caught at once and one, mistyped in the first run, when re-run; tests were added for the 10 survivors (the trim back onto the edge, a saturated STATCOM, the intact margin, an existing pair short of the margin, the grid's bay, the sizing-rule escalation, the directional reactive escalation, the dispatch held in the re-solve, the reactive margin, the STATCOM's fault current), and all are caught now. Two are equivalent: `<` against `<=` on two equal non-zero step distances (an exact float tie), and the early break once the PCC is in the band (the scan cannot then find a better count).

### C9: panel

- **The library editor.** It edits the per-project copy, starting from the shipped default, with a reset.
- **The investment table and cost summary.** The electrical cost per period is shown next to the hub's system cost from the solved project, and as a total.
- **Chat tools**, which read and edit the library and read the investment.
- **The bundle** carries the library used and the investment table.

### C10: grid-code upload and extraction

- **The upload route.** It takes a PDF only, with a size budget, and stores the file in the project.
- **The extraction.** A service function calls the Anthropic API with the document and a strict output schema; the copilot tool calls the same function.
- **The draft.** It is validated by the profile loader and saved as a *draft* profile.
- **The review form.** Limits are confirmed one by one, with the quote shown beside each.
- **Profiles listed.** Project profiles appear next to the shipped ones in the grid-code picker.
- **Tests.** They mock the API. A live run happens in a session that has the key.

### C11: MILP with successive linearisation

- **Variables:**
  - binaries for each discrete option (transformer n × size, STATCOM size, cable size, switchgear rating);
  - integers for capacitor and reactor steps;
  - continuous inverter Q per hour and case, inside a polygon of its capability circle.
- **Linearisation**, per hour and case, around the current AC point:
  - for continuous Q: sensitivities of bus voltage, PCC Q and branch loading, from the load-flow Jacobian;
  - for each discrete option: its effect, by finite difference. One AC solve per option and hour is affordable, because options are few.
- **Objective:** minimum annualised cost, built in linopy and solved with HiGHS (both already in the environment).
- **The loop**, with an adaptive convergence rate:
  1. Solve the MILP.
  2. Apply the result, and re-solve AC at every hour and case.
  3. Measure, per constraint, the **linearisation error**: AC minus linear.
  4. **Tighten each limit** by a back-off β × that error. β grows while a violation persists and relaxes when there is ample slack.
  5. **Bound the change in continuous Q** by a trust region Δ:
     - Δ doubles when the predicted and actual changes agree (ratio ρ near 1);
     - Δ halves when they disagree.
  6. Re-linearise, and go back to 1.
- **When it stops.** It stops when the AC check passes and the cost changes by less than a tolerance, or after a maximum number of iterations.
- **The least-cost result (C8)** is the warm start and the upper bound.
- **What is reported:** the iteration history (cost, worst violation, worst linearisation error, Δ, β), and MILP against least-cost.

### Order, PRs and agents

| PR | increments | implementer |
|---|---|---|
| 1 | C7 + C8 (engine) | C7: Sonnet (patterned data and loader); C8: Opus (engineering judgement) |
| 2 | C9 (panel, routes, chat tools) | Sonnet; Haiku for mechanical updates (snapshots, route inventory, packaging list) |
| 3 | C10 (upload and extraction) | Opus for the schema, prompt and upload safety; Sonnet for the form |
| 4 | C11 (MILP loop) | Opus |

The coordinator writes each brief from this plan and reviews every diff. It also runs the gates and mutation checks before anything is pushed.
