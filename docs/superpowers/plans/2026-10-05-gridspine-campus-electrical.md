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
- **Who buys the PCC switchgear** (owner decision, 2026-10-05): a study setting, `pcc_switchgear`, costed to the campus by default. Set to the grid operator, the PCC bus is never a need, a failing PCC rating is reported and not bought, and the choice is written to `campus_invest_scope.json`.

**The driver.** `invest_campus(run_dir, library=None, ...)` needs a prepared and ranked run, recomputes part one with the same profile and power factor, and writes `campus_investment.csv`, `campus_cost.csv`, `campus_invested.yaml`, `campus_compliance_invested.csv`, `campus_invest_history.csv` and (added) `campus_invest_dispatch.csv`. `size_campus` is unchanged.

**On the solved Data Center Energy Hub template** (one period, pf 0.95 agreement, shipped library): 1 × 63 MVA 132/33 kV, 3 × 25 MVA 33/11 kV. With N-1 on, a multi-unit group must survive one unit out: one survivor would carry 38.9 MVA × 1.2 = 46.7 MVA, which a 40 MVA unit cannot. The library (extended to 31.5 and 40 MVA at 33/11 kV) has no single 33/11 kV unit that large, so three units of 25 MVA are the cheapest that pass, switchgear 31.5 kA at 132 kV (2 bays), 25 kA at 33 kV (11 bays) and 11 kV (5 bays), no compensation: €6.89 M capex, €610 k a year. Every check with measures passes, re-solved; no escalation was needed.

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

### C11 as built (2026-10-05, margin rule 2026-10-06)

**Where.** `gridspine/static/campus_milp.py` (`select_assets_milp`), and `invest_campus(..., method="least_cost" | "milp")`, default `least_cost`. The MILP path writes C8's investment files from its own result, plus `campus_milp_history.csv` and `campus_milp_comparison.csv`. C8 gained `open_candidates`, `study_from`, `with_measures`, an `inspect` hook on `solve_cases`, and returns its final `state`, the warm start; its behaviour is unchanged.

**The choices the plan left open:**
- **Candidates.** C8's generators with a requirement of zero: "none" and every combination for the reactive need, every transformer size in 1..3 units (a redundant group stays redundant), every cable section in 1..3 runs, every switchgear rating. When C8's point is AC-feasible, a candidate dearer on its own than C8's whole set is pruned. Rated buses and cables that are not needs are held, not bought.
- **Dispatch.** Per hour, held in every case, as in C8's re-check. Inverters as C8's dispatch would use them; capability an **inscribed** octagon with vertices on the axes (a hard limit the AC check cannot repair; at most 7.6 % of S lost, none at P = 0). C8's inverter Q is clipped into it for the warm start. STATCOM Q per hour within the chosen units; integer steps per bank and reactor candidate and hour.
- **Constraints**, per hour and case class (intact; the worst outage of each group): voltages against the profile's bands, the PCC band (intact), transformer loading and the sizing rule with the margin (ratings inside `g`, so a candidate's rating is exact), cable current, Ik'' and ip per period. Each scaled to 1 % of its limit (0.01 pu for voltages), with a slack at a penalty ten times a bound on any set's cost.
- **Sensitivities: the Jacobian**, rebuilt at the converged point from pandapower's internal `Ybus` (`dSbus_dV`; the stored `J` is from the last Newton iterate). One sparse solve per hour and case gives every quantity per Mvar at each control bus; checked against a finite-difference AC re-solve to 1e-4. A bank step enters as `q x V^2`.
- **Discrete candidates: finite differences**, one AC solve per transformer or cable candidate, hour and case at the current dispatch, with IEC 60909 per period; compensation changes no load flow at zero dispatch, so it enters through its dispatch (and, with a STATCOM, its fault level, cached). Switchgear ratings enter exactly; its cost is per bay, the bays a linear function of the other choices, with exact products of binaries.
- **The loop.** beta x1.5 when violated in AC (cap 8), x0.7 with more than 5 scale units of slack (floor 0.25). rho > 0.75 doubles Delta (cap 4 x Q_ref), rho < 0.25 halves it and rejects, keeping the point and its linearisation. Delta starts at 2 x Q_ref (a full swing), floor Delta_0 / 64. A tie-break of 1e-3 per Mvar moved keeps the dispatch put. Stop: an accepted AC-feasible point whose cost moved less than 1 a year; the MILP returning the current point; Delta at its floor; 20 iterations. A trial whose campus does not converge gets a no-good cut.
- **A deviation, flagged.** The trust region binds only while every need keeps its current candidate. Literally applied, a rejected step that changed a candidate halves Delta around the old point, and the cheaper candidate can become unreachable. That was shown on a case the margin rule below has since closed; on every case tried after it, the literal and the relaxed trust region give the same result and count, so this is now a precaution without a demonstrating test (its mutation survives). **Owner decision (2026-10-06): C11 confirmed as built, this relaxed trust region included.** The surviving mutation is recorded here as a known, accepted gap rather than dead code.
- **The result** is the best AC-feasible point seen; C8's own re-check (`_failures`) is the judge. If none is cheaper than C8 by more than 1 a year, C8's result is returned, flagged.
- **The sizing margin is judged on C8's dispatch** (owner, 2026-10-06: no, keep the 20 % margin; it may not be met with dispatched inverter Q). Every point the loop solves is also re-solved with the dispatch C8's re-check gives the same assets (`reactive_need(residual=False)`: inverters, STATCOMs, steps, only as far as the PCC band needs). The margin constraint reads those flows, with no coefficient on the MILP's Q, and the AC judge sizes the transformers from them (`size_transformers`; `_failures` itself sizes from whatever flows it is given, so before this the MILP's own dispatch was used). The MILP's extra Q still serves the 100 % loading, the PCC band and the voltages. A candidate's effect on the margin is a finite difference with that dispatch re-run (a compensation candidate's only at hours where C8's dispatch reaches the compensation). The sizing the margin was judged by is returned (`sizing`), and the linearisation error per trial and constraint (`lin_errors`).

**What it finds.**
- **The joint optimum** (test campus, one dark hour, 25 MW at pf 0.85): C8 takes the cheapest transformer, 40 MVA at 14 %, whose reactive loss puts the PCC out of its band, then a 4 Mvar bank: 95.6 k a year. The MILP takes the 50 MVA, 10 % unit alone: 75.1 k, the brute-force minimum over all 24 combinations, solved independently in the test. Two iterations. It does not lean on Q for the margin (dark hour; C8's dispatch with T_B alone needs nothing).
- **No margin from inverter Q** (38 MW, pf 0.85): a 50 MVA unit holds the margin only with about 15 Mvar from the battery; C8's dispatch gives none, so the MILP keeps C8's 63 MVA (flagged fallback, one iteration, the 50 MVA never proposed). Before the owner's rule it bought the 50 MVA.
- **Adaptive against fixed rate.** On an outage relieved by the battery's Q (two 40 MVA units, N-1 sizing off, 38 MW: the margin holds on the intact share with C8's dispatch, the survivor needs Q to stay within 100 %), both rates reach the pair (81.9 k against C8's 88.8 k) in **4 iterations each**; beta grows on the survivor's loading. In a scan of 33 more cases (outage cases at 30-44 MW, voltage cases on 300-3000 MVA grids with 2-10 km cables; many end at C8's choice in one or two iterations) the counts were equal in 31, and adaptation took one iteration more in 2. **With the margin loophole closed, no case was found where adaptation saves an iteration.** The earlier 5 against 9 depended on the margin being met with the battery's Q.
- **The Data Center hub** (one period, 14 hours, pf 0.95 agreement, shipped library): **1 x 63 MVA 132/33 kV, as C8**, everything else as C8: 610.3 k a year, flagged fallback, 1 iteration, 500 s (the reference dispatch is re-run for every candidate). Before the owner's rule the MILP took 50 MVA (574.3 k) on 1.8-3.8 Mvar of inverter Q.

**Mutations.** 30 by hand. The first 28 (beta and Delta directions, caps and the rho thresholds, the best-point choice, three sensitivity signs, the polygon, the one-per-need constraint, the fallback, the back-off sign, the rejected point, the free switch, the step sign, the bay products, the PCC scope, the margin, the feasibility judge): 24 caught at once, tests added for 4. After the margin rule, two more: **the margin using dispatched Q** (both the constraint and the judge on the MILP's flows, with Q coefficients: caught) and the judge alone on the MILP's flows (caught once the sizing used is returned). Re-run on the new code, the tests written for the closed loophole no longer caught rho inverted, the margin dropped and the polygon; rho now has a unit test, the 38 MW test asserts the 50 MVA is never proposed, and the polygon test moved to the outage case. The free switch survives (above).

### Order, PRs and agents

| PR | increments | implementer |
|---|---|---|
| 1 | C7 + C8 (engine) | C7: Sonnet (patterned data and loader); C8: Opus (engineering judgement) |
| 2 | C9 (panel, routes, chat tools) | Sonnet; Haiku for mechanical updates (snapshots, route inventory, packaging list) |
| 3 | C10 (upload and extraction) | Opus for the schema, prompt and upload safety; Sonnet for the form |
| 4 | C11 (MILP loop) | Opus |

The coordinator writes each brief from this plan and reviews every diff. It also runs the gates and mutation checks before anything is pushed.

## Part three: joining the one investment engine (plan note, 2026-10-06, no code yet)

The coordinating session passed on a request, which it says the owner approved on 2026-10-06: there should be **one investment implementation** in pypsa-gui. The references are:
- `docs/superpowers/plans/2026-10-05-one-investment-engine-two-faces.md`;
- ADR-0004;
- "Investment language" in `pypsa-gui/CONTEXT.md`.

The campus asset library (C7), the least-cost pick (C8) and the MILP (C11) join it as a third participant. Nothing here changes the open PRs (#74, #79, #83, #84).

**No code for D1 or D2 until** the engine's PRs #81 and #85 and the asset-schema PR #78 have merged. Words follow CONTEXT.md: *Overnight cost* (not capex), *Generic default* and *illustrative*.

### D1: one source for equipment cost data

- **gridspine stays free of pypsa-gui imports.** `campus_assets.yaml` remains the engine's input format and the test fixture library.
- **On the pypsa-gui side**, `campus_electrical_service` seeds a project's campus library from the **generic defaults pack** (`services/library/defaults_pack`, PR #85), not from the shipped YAML. The fields map one to one:

  | campus library | defaults pack |
  |---|---|
  | `capex_eur` | Overnight cost, lump per unit |
  | `capex_eur_per_km` | Overnight cost, per km |
  | switchgear `capex_eur` | Overnight cost, per bay |
  | `opex_frac` | FOM share |
  | `lifetime_a` | lifetime |
  | `measured` / `datasheet` / `assumed` | provenance; `assumed` = illustrative |

- **The rows themselves** (transformers, cables, capacitor banks, shunt reactors, STATCOMs, switchgear) are added to the pack. The engine session owns the pack, so the rows are agreed with it and it adds them, or approves a PR that does.
- **The discount rate and price year** come from the project (its solver config or FinanceInputs `currency_year`), not from a library-level 0.07. The engine API is unchanged: `select_assets` already receives the library as data, and the service writes the project's rate into the library it hands over. The library's own `discount_rate` remains as a stand-alone default for gridspine-only use.
- **Ratings and impedances stay in the campus library.** They are electrical data, not cost, and the pack has no field for them.

### D2: the picks feed the investment case

- **What is produced.** A solved campus study produces **extra owner assets**. Each item chosen by C8 (or C11) becomes one owner asset with its upfront parts, in the asset-schema `UpfrontPart` shape:
  - name;
  - Overnight cost × units (× km for a cable, × bays for switchgear);
  - lifetime;
  - FOM share.

  Each asset also carries its investment period, its need (for example "transformer GRID_IMPORT") and its provenance. Existing (sunk) items are left out, and unresolved needs are never emitted.
- **Who does what.** The engine session adds the input hook: a list of extra owner assets read by `results/finance_case.build_finance_case`. This side only produces the list, from the run directory (`campus_investment.csv` with `campus_invest_scope.json`), through one function in `campus_electrical_service`. **Do not edit `services/finance` or `results/finance_case.py`.** The shape is agreed with the engine session before any code.
- **Consistent with the owner's decision of 2026-10-05.** Electrical cost is still not fed back into the capacity expansion. The PyPSA project is untouched; the equipment only joins the cash flows, NPV and report, next to the batteries and PV.

### File ownership (one-engine plan §5)

- **Ours:** `gridspine/*` and `campus_electrical_*`.
- **Shared hot files:** `App.tsx`, `layout/*`, `uiStore.ts`, `chat_tools*.py`, `main.py`, `pypsa-gui.spec`, `tool-error-kinds.json`. Additive edits only, and merge master before each PR.

## Part three as built (2026-10-07)

### Owner decisions (2026-10-07)

- **Grid-code review, a quote not found on its stated page:** warn and still allow Confirm (today's behaviour). The reviewer is the check; real documents often have small page offsets.
- **The MILP in the panel:** as a run option ("Joint optimisation (MILP, slow)") executed as a background job with progress and cancel, its result shown beside the least-cost one. Planned as C12.

The campus side of part three is built on `feat/gridspine-campus-ic-seam`. Words follow `pypsa-gui/CONTEXT.md` ("Investment language"): *Overnight cost*, *Generic default*, *illustrative*, *upfront part*. The engine's files (`services/finance/*`, `services/results/finance_case.py`, `services/library/*`, `services/asset_schema/*`, `models/finance.py`) are untouched.

### D1a: done. The discount rate and the price year come from the project

- **Rate.** When the run invests, the library handed to `invest_campus` has its `discount_rate` replaced by the **project's saved solver config** `discount_rate`, if the project's saved solver config file (`solver_config.json`) **states** a `discount_rate`. Without that file, or when the file has no `discount_rate` key (the loader's 0.07 default is not a rate the project stated), the library's own value is kept and labelled as the library's. The replacement is tagged `assumed`, with a note saying where it came from. A project rate outside [0, 1) is a 422 that names it; nothing is annualised on it.
- **Price year.** The project's finance inputs are not a file of their own: they are `SolverConfig.finance`, the dict `PUT /api/simulation/finance` validated, kept in the same `solver_config.json`. If it carries a `currency_year`, that is the price year reported, and `price_year_mismatch` is true when it differs from the library's. **No money is converted.** The library's costs stay in the library's price year and no escalation is applied. The panel says so.
- **The record.** `results.cost_basis` is `{discount_rate, discount_rate_from: "project solver config" | "asset library", price_year, price_year_from: "project finance inputs" | "asset library", library_price_year, price_year_mismatch, currency}`. It is written with the run (`run/campus_cost_basis.json`) and cleared with the other investment files, so it describes the run and not the project as it is now. `get_investment` (the copilot's summary) carries it too.
- **Staleness.** The engine's copy, rate included, is what `run/campus_assets_used.yaml` keeps, byte for byte. The results are stale when the library a run would hand over now differs from that snapshot (so a change of the project's discount rate makes them stale) **or** when the cost basis differs (so a change of the finance inputs' `currency_year` does too, since the basis is shown). A solver-config change that touches neither (the solver name, say) does not. A library or a solver config that cannot be read now counts as stale: a rerun would not reproduce the run.
- **Panel.** One plain line in the investment section: "Annualised at 7 % (from asset library); costs in 2026 money (from asset library)." When the project states another year, the line still names the library's year (those are the costs' money) and a warning says the project states another, with no escalation applied.

### D2 producer: done. The chosen equipment as extra owner assets

`campus_electrical_service.extra_owner_assets(project) -> list[dict]`, read only, from the last investment run: `campus_investment.csv`, `campus_assets_used.yaml` (the library the run bought from, not one saved since). Who owns the PCC switchgear (`campus_invest_scope.json`) is already settled in the table: with `grid_operator` the engine writes no row for it, so no entry appears. `GET /api/campus-electrical/{name}/owner-assets` returns the list, and `get_state` carries `owner_assets_count`. It is `[]` when the project has no investment run.

**One entry per purchased item that is neither existing nor unresolved.** This is the proposed contract for the engine session's hook in `build_finance_case`, which this branch does not touch:

```json
{
  "name": "transformer GRID_IMPORT TR_132_33_63",
  "need": "transformer GRID_IMPORT",
  "kind": "transformer",
  "library_id": "TR_132_33_63",
  "units": 2,
  "invest_period": 2030,
  "upfront_parts": [
    {"name": "investment", "upfront": 5000000.0, "lifetime": 40.0, "fom_share": 0.015}
  ],
  "currency": "EUR",
  "price_year": 2026,
  "provenance": "assumed",
  "illustrative": true
}
```

- `upfront_parts` mirrors `asset_schema.access.UpfrontPart` (`name`, `lifetime`, `fom_share`) but carries the **total** Overnight cost in `upfront`, not `upfront_per_unit`: these items have no per-MW sizing variable. The total is units × the library's Overnight cost, × km for a cable (`capex_eur_per_km`), and the units of switchgear are its bays.
- `price_year` is the library's: the money the cost is in. The project's money year, when it differs, is in `results.cost_basis`.
- `provenance` is the source tag of the cost in the library (`measured`, `datasheet` or `assumed`); `illustrative` is true when it is `assumed`. The shipped library tags every cost `assumed`, so every entry is illustrative today.
- A reactive need can give two entries (a capacitor bank and a reactor), each with its own investment period.

### Checks

**Tests.** 34 service cases (D1a and D2, most on hand-made run directories with known units, km and bays, plus three real runs), 3 router cases and 4 panel tests. The route inventory (`route_inventory_phase0.txt`) gains exactly the one new route; `/api/chat/workflows`, which the app has and the file lacks, is another PR's and was not added.

**Mutations.** 20 by hand on the service: the rate override ignored, a wrong source label, an out-of-range rate not refused, the cost basis not cleared with the investment, the snapshot not the engine's copy, the price-year mismatch flag (always false, always true when a year is stated), the project's year never used, existing items included, unresolved items included, units not multiplied, cable km not multiplied, switchgear bays not multiplied, the empty-run handling, `illustrative` always true, the provenance taken from the wrong tag, lifetime and FOM share swapped, the owner assets priced from the library as it is now instead of the run's, the count always 0, and both halves of the staleness test. 19 were caught at once. One was not by the new tests: the byte comparison of the library in the staleness test. For a rate change it is redundant, since the cost basis changes with the rate and the other half catches it; for a library edit the older library-staleness tests catch it. Three more on the panel line (the year shown, the mismatch warning, the rate format), all caught.

### D1b: a proposal for the pack's owner. Nothing is added to the pack

Seeding the campus library from the generic defaults pack (D1) needs rows the pack does not have. This is what the campus library's cost rows would become in `defaults_pack/versions/<date>/values.csv`. It is **a proposal for the pack's owner**: this branch adds nothing to the pack.

**These costs are unsourced placeholders.** Every figure in the shipped campus library is tagged `assumed` and was not checked against a price list, a datasheet or a quote. The pack's other rows are sourced (technology-data v0.14.0, Danish Energy Agency). Putting the campus rows beside them would make an order-of-magnitude guess look like a sourced default, so every row must carry `illustrative = true` and the source "assumed — placeholder, replace with vendor quotes".

| campus library field | kinds | pack `key` (per library entry) | `technology` | `part` | `basis` | `parameter` | `unit` |
|---|---|---|---|---|---|---|---|
| `capex_eur` | transformers (29) | `campus.transformer.132_33.63mva.overnight` | `campus.transformer.132_33.63mva` | `investment` | `per_unit` | `overnight` | `EUR/unit` |
| `capex_eur` | capacitor banks (9), shunt reactors (9), STATCOMs (9) | `campus.capacitor_bank.33kv.10mvar.overnight` (likewise `campus.shunt_reactor…`, `campus.statcom…`) | `campus.capacitor_bank.33kv.10mvar` | `investment` | `per_unit` | `overnight` | `EUR/unit` |
| `capex_eur_per_km` | cables (12) | `campus.cable.33kv.al240.overnight` | `campus.cable.33kv.al240` | `investment` | `per_km` | `overnight` | `EUR/km` |
| `capex_eur` | switchgear (13) | `campus.switchgear.132kv.31p5ka.overnight` | `campus.switchgear.132kv.31p5ka` | `investment` | `per_bay` | `overnight` | `EUR/bay` |
| `opex_frac` | all 81 | `….fom_share` | as above | `investment` | as above | `fom_share` | `share/year` |
| `lifetime_a` | all 81 | `….lifetime` | as above | `investment` | as above | `lifetime` | `years` |

Common columns: `illustrative` true; `source` "assumed — placeholder, replace with vendor quotes"; `currency` EUR; `currency_year` the library's `price_year` (2026, which differs from the DEA rows' 2020, the same mismatch D1a flags); `range_low`, `range_high` empty; `price_basis` left to the owner (the campus library does not say whether it is real or nominal). That is 81 entries × 3 parameters = 243 rows.

For the pack's owner:
- The loader's `basis` is `per_MW | per_MWh | per_km` today, with `_BASIS_UNIT` mapping each to its overnight unit. `per_unit` and `per_bay` would be new.
- The keys above follow the brief, `<technology>.<parameter>`, whereas the existing rows are `<technology>.<part>.<parameter>` (`solar-utility.investment.overnight`). The owner may prefer `<technology>.investment.overnight`.
- A new pack version directory and its pinned hash are needed; a shipped version is never edited.
- Ratings and impedances stay in the campus library (D1).

## C12 as built (2026-10-07): the MILP in the panel, as a background job

Owner decision 2026-10-07 (above). Branch `feat/gridspine-campus-milp-job`, cut from `feat/gridspine-campus-ic-seam`.

- **Engine.** `select_assets_milp` and `invest_campus(method="milp")` take `progress(iteration, max_iter, summary)`, called once per history row (the warm start is 0), and `should_stop()`, asked before every iteration and between the finite-difference solves of a linearisation (the warm start's included, where the Data Center spends most of its 500 s). A stop ends the loop with `stop == "cancelled"`; the result is built as at any other stop (the best AC-feasible point, or C8's, flagged with "cancelled after n iteration(s): …") and the files are still written. Without them the loop is unchanged; the least-cost pick refuses them.
- **The job** (`campus_electrical_service`), in the report job's shape: a record per project (`state` running|done|cancelled|failed, `iteration`, `max_iter`, `best_cost`, `c8_cost`, `stop`, `message`, `error`), served without `thread` and `stop_event`; one job at a time in the process (409 for any project while one runs); the stop event is the loop's `should_stop`. The records live in the service module rather than the session's solver state, since a campus job belongs to a project, not to the session's network. In the request: the settings are checked as `run` checks them (and must invest), the slot is claimed, the least-cost run is made unless a fresh one with the same settings exists, and `campus_electrical/run_milp/` is made afresh from its inputs (prepared and ranked campus, the library it bought from, its cost basis and investment) with their hashes. A refusal on the way frees the slot and leaves no record. The thread reads and writes only `run_milp/`: no NetCDF, no PyPSA lock. The least-cost files in `run/` are never written by it.
- **State.** `get_state.milp = {status, results: {investment, cost, compliance_invested, history, comparison, summary, fallback, stop} | null, stale}`. Results appear once the summary is written (last). Stale when the least-cost run is, or when it has been made again with other settings or other results (the copied files' hashes against `run/` now).
- **Routes.** `POST /api/campus-electrical/{name}/milp` (202, the run settings), `GET …/milp` (the record, `null` when none since the backend started), `POST …/milp/cancel` (404 when none). Starting and cancelling check the edit lock.
- **Panel.** "Joint optimisation (MILP, slow — minutes)" beside "Run study", enabled once a least-cost run has bought assets; while it runs, "Iteration i of n · best €x/a vs least cost €y/a" and Cancel, polled every 1.5 s like the report job. A "Joint optimisation" block under Investment: its cost against least cost, the comparison per need, its purchases, fallback and cancelled labels, the iteration history (collapsed), the owner's margin rule and the placeholder costs.
- **Copilot.** `campus_get_milp`, read only (status, summary, comparison, stale, notes). No tool starts or cancels the job.
- **Measured.** The real MILP as a job on the backend tests' mini hub: about 19 s, 1 iteration, converged.
