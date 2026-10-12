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

### Reference costs (2026-10-12)

The owner decided "Reference costs from sources". The research is in `docs/superpowers/research/2026-10-12-campus-asset-reference-costs.md`. Its values are a complete library beside it (`…-reference-costs.yaml`), **as a proposal, not shipped**.

- **Why not shipped.** The investment case's defaults pack (2026-10-07) transcribes the shipped costs, and its parity test fails on any price change. The pack is the IC session's, so adopting the values needs its new pack version first. The research note writes out that change.
- **Sources.** Capex comes from the Swedish regulator Ei's standard values for grid assets 2024–2027, in 2022 SEK, converted at the ECB 2022 rate and Eurostat's euro-area producer price index for C27.1 (electric motors, generators, transformers and distribution apparatus; `sts_inpp_a`, 2021=100, 108.6 → 119.8) to **2025 EUR**: 1 SEK (2022) = 0.10378 EUR (2025). That covers transformers at all four voltage pairs, MV cables, switchgear bays and capacitor banks. O&M shares and the 40-year lifetime come from the Danish Energy Agency's technology catalogue for energy transport.
- **Coverage.** 49 of 81 capex, 72 of 81 O&M shares and 54 of 81 lifetimes are cited.
- **Still `assumed`, with a note each:** MV shunt reactors and STATCOMs (only 380 kV figures exist), compensation lifetimes, the price step between short-circuit ratings, and sizes outside the source's range.
- **New tag `literature`.** The loader accepts it on costs and the discount rate only; a rating is copied into the campus file, whose tags it would break. `extra_owner_assets` reports it as `provenance: literature`, `illustrative: false`, which is the pack's meaning of illustrative: "not taken from a cited source".
- **Owner decisions (2026-10-12, after the first draft).**
  - Escalate with the equipment producer price index (C27.1, euro area) instead of the HICP. The first draft's combined factor was 0.10368; it is now 0.10378, so every converted value moved by at most a few hundred euro.
  - Keep cables priced for rural, normal ground.
- **The Data Center hub, C8 re-run** (same inputs; the shipped library reproduces the table above exactly). The same purchases are made: 1 × 63 MVA 132/33 kV, 3 × 25 MVA 33/11 kV, and switchgear 31.5 kA at 132 kV (2 bays), 25 kA at 33 kV (11) and 11 kV (5). The cost is **EUR 6.71 M capex (6,706,100) and EUR 604 k a year (603,610)**, against EUR 6.89 M and EUR 610 k. With the first draft's HICP index it was 6,700,200 and 603,079. The 132/33 kV unit is 34 % cheaper and the three 33/11 kV units 42 % dearer; switchgear is 17 % cheaper. The template has no cables and needs no compensation, so those prices were not exercised.

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

`campus_electrical_service.extra_owner_assets(project) -> list[dict]`, read only, from the last investment run: `campus_investment.csv`, `campus_assets_used.yaml` (the library the run bought from, not one saved since). Who owns the PCC switchgear (`campus_invest_scope.json`) is already settled in the table: with `grid_operator` the engine writes no row for it, so no entry appears. `GET /api/campus-electrical/{name}/owner-assets` returns `{"assets": [...], "source_hash": str | None, "stale": bool}` (`stale` is the results-stale flag of `get_state`), and `get_state` carries `owner_assets_count`. The assets are `[]` and `source_hash` is `None` when the project has no investment run.

**One entry per purchased item that is neither existing nor unresolved**, in the contract below: agreed with the IC session, owner-approved 2026-10-07. The IC session adds the type (`services/finance/case.py ExtraOwnerAsset`) and the `extra_assets` hook in `build_finance_case`; the investment-case route calls `campus_electrical_service.extra_owner_assets` and refuses a stale study. This branch adds neither. The top-level keys of an entry are exactly the type's fields, so the IC can build it with `ExtraOwnerAsset(**{k: d[k] for k in fields})`; everything else is under `meta`.

```python
@dataclass(frozen=True)
class ExtraOwnerAsset:                  # the IC's, not ours
    name: str                       # unique in the case, e.g. "campus:TR_132_33_40#1"
    kind: str                       # transformer | cable | capacitor_bank | shunt_reactor | statcom | switchgear
    basis: str                      # lump | per_km | per_bay
    quantity: float                 # units (lump), units x length_km (per_km), bays (per_bay)
    parts: tuple[UpfrontPart, ...]  # asset_schema.access.UpfrontPart; upfront_per_unit is per basis unit
    build_year: int                 # the campus invest_period
    source: str                     # "campus_study"
    source_hash: str                # sha256[:16] of the solved campus study that chose it
```

```json
{
  "name": "campus:TR_132_33_63#1",
  "kind": "transformer",
  "basis": "lump",
  "quantity": 2.0,
  "parts": [
    {"name": "investment", "upfront_per_unit": 2500000.0, "lifetime": 40.0, "fom_share": 0.015}
  ],
  "build_year": 2030,
  "source": "campus_study",
  "source_hash": "3f9a1c5e07b2d864",
  "meta": {
    "need": "transformer GRID_IMPORT", "library_id": "TR_132_33_63", "units": 2, "length_km": null,
    "currency": "EUR", "price_year": 2026, "provenance": "assumed", "illustrative": true
  }
}
```

- `name` is `campus:<library_id>#<k>`, k the 1-based occurrence of that library id among the entries, in the table's order: unique in the list.
- `kind` is the investment table's item kind, mapped explicitly (`IC_KIND`): `transformer`, `cable`, `capacitor_bank`, `shunt_reactor`, `statcom` and `switchgear` are the six kinds the engine writes, and the IC's vocabulary is the same six; any other kind is a 422 that names it.
- `basis` and `quantity`: a cable is `per_km` with quantity units × `length_km`; switchgear is `per_bay` with the table's units, which are the bays; everything else is a `lump` with quantity = units.
- `parts` has one part, `investment`. `upfront_per_unit` is the library's Overnight cost **per basis unit** (`capex_eur_per_km` for a cable, `capex_eur` otherwise), not the total: `quantity × upfront_per_unit` is the total, the figure the first draft carried as `upfront`. `lifetime` and `fom_share` are the library's `lifetime_a` and `opex_frac`. Money is in the library's currency and price year; no escalation.
- `build_year` is the investment period. `source` is `"campus_study"`.
- `source_hash` is `campus_electrical_service.campus_study_hash(project)`: the first 16 hex characters of a sha256 over `run/campus_investment.csv`, `run/campus_assets_used.yaml`, `run/campus.yaml` and, when present, `run/campus_cost_basis.json`, in that order, each as its file name, a NUL byte and its bytes. The same for every entry of a run; `None` without an investment run. It changes when the campus, the library, the rate or the run does. The IC refuses a stale study (its owner default B) and a `build_year` after the case COD (default C).
- `meta.price_year` is the library's: the money the cost is in. The project's money year, when it differs, is in `results.cost_basis`.
- `meta.provenance` is the source tag of the cost in the library (`measured`, `datasheet` or `assumed`); `meta.illustrative` is true when it is `assumed`. The shipped library tags every cost `assumed`, so every entry is illustrative today.
- A reactive need can give two entries (a capacitor bank and a reactor), each with its own investment period.

### Checks

**Tests.** 35 service cases for D1a and D2 in the IC's shape (most on hand-made run directories with known units, km and bays, plus the real runs), 3 router cases and 4 panel tests; 352 campus cases in the gate. The route inventory (`route_inventory_phase0.txt`) gains exactly the one new route; `/api/chat/workflows`, which the app has and the file lacks, is another PR's and was not added.

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

`campus_electrical_service.extra_owner_assets(project) -> list[dict]`, read only, from the last investment run: `campus_investment.csv`, `campus_assets_used.yaml` (the library the run bought from, not one saved since). Who owns the PCC switchgear (`campus_invest_scope.json`) is already settled in the table: with `grid_operator` the engine writes no row for it, so no entry appears. `GET /api/campus-electrical/{name}/owner-assets` returns `{"assets": [...], "source_hash": str | None, "stale": bool}` (`stale` is the results-stale flag of `get_state`), and `get_state` carries `owner_assets_count`. The assets are `[]` and `source_hash` is `None` when the project has no investment run.

**One entry per purchased item that is neither existing nor unresolved**, in the contract below: agreed with the IC session, owner-approved 2026-10-07. The IC session adds the type (`services/finance/case.py ExtraOwnerAsset`) and the `extra_assets` hook in `build_finance_case`; the investment-case route calls `campus_electrical_service.extra_owner_assets` and refuses a stale study. This branch adds neither. The top-level keys of an entry are exactly the type's fields, so the IC can build it with `ExtraOwnerAsset(**{k: d[k] for k in fields})`; everything else is under `meta`.

```python
@dataclass(frozen=True)
class ExtraOwnerAsset:                  # the IC's, not ours
    name: str                       # unique in the case, e.g. "campus:TR_132_33_40#1"
    kind: str                       # transformer | cable | capacitor_bank | shunt_reactor | statcom | switchgear
    basis: str                      # lump | per_km | per_bay
    quantity: float                 # units (lump), units x length_km (per_km), bays (per_bay)
    parts: tuple[UpfrontPart, ...]  # asset_schema.access.UpfrontPart; upfront_per_unit is per basis unit
    build_year: int                 # the campus invest_period
    source: str                     # "campus_study"
    source_hash: str                # sha256[:16] of the solved campus study that chose it
```

```json
{
  "name": "campus:TR_132_33_63#1",
  "kind": "transformer",
  "basis": "lump",
  "quantity": 2.0,
  "parts": [
    {"name": "investment", "upfront_per_unit": 2500000.0, "lifetime": 40.0, "fom_share": 0.015}
  ],
  "build_year": 2030,
  "source": "campus_study",
  "source_hash": "3f9a1c5e07b2d864",
  "meta": {
    "need": "transformer GRID_IMPORT", "library_id": "TR_132_33_63", "units": 2, "length_km": null,
    "currency": "EUR", "price_year": 2026, "provenance": "assumed", "illustrative": true
  }
}
```

- `name` is `campus:<library_id>#<k>`, k the 1-based occurrence of that library id among the entries, in the table's order: unique in the list.
- `kind` is the investment table's item kind, mapped explicitly (`IC_KIND`): `transformer`, `cable`, `capacitor_bank`, `shunt_reactor`, `statcom` and `switchgear` are the six kinds the engine writes, and the IC's vocabulary is the same six; any other kind is a 422 that names it.
- `basis` and `quantity`: a cable is `per_km` with quantity units × `length_km`; switchgear is `per_bay` with the table's units, which are the bays; everything else is a `lump` with quantity = units.
- `parts` has one part, `investment`. `upfront_per_unit` is the library's Overnight cost **per basis unit** (`capex_eur_per_km` for a cable, `capex_eur` otherwise), not the total: `quantity × upfront_per_unit` is the total, the figure the first draft carried as `upfront`. `lifetime` and `fom_share` are the library's `lifetime_a` and `opex_frac`. Money is in the library's currency and price year; no escalation.
- `build_year` is the investment period. `source` is `"campus_study"`.
- `source_hash` is `campus_electrical_service.campus_study_hash(project)`: the first 16 hex characters of a sha256 over `run/campus_investment.csv`, `run/campus_assets_used.yaml`, `run/campus.yaml` and, when present, `run/campus_cost_basis.json`, in that order, each as its file name, a NUL byte and its bytes. The same for every entry of a run; `None` without an investment run. It changes when the campus, the library, the rate or the run does. The IC refuses a stale study (its owner default B) and a `build_year` after the case COD (default C).
- `meta.price_year` is the library's: the money the cost is in. The project's money year, when it differs, is in `results.cost_basis`.
- `meta.provenance` is the source tag of the cost in the library (`measured`, `datasheet` or `assumed`); `meta.illustrative` is true when it is `assumed`. The shipped library tags every cost `assumed`, so every entry is illustrative today.
- A reactive need can give two entries (a capacitor bank and a reactor), each with its own investment period.

### Checks

**Tests.** 34 service cases (D1a and D2, most on hand-made run directories with known units, km and bays, plus three real runs), 3 router cases and 4 panel tests. The route inventory (`route_inventory_phase0.txt`) gains exactly the one new route; `/api/chat/workflows`, which the app has and the file lacks, is another PR's and was not added.

**Mutations.** 20 by hand on the service for the first producer: the rate override ignored, a wrong source label, an out-of-range rate not refused, the cost basis not cleared with the investment, the snapshot not the engine's copy, the price-year mismatch flag (always false, always true when a year is stated), the project's year never used, existing items included, unresolved items included, units not multiplied, cable km not multiplied, switchgear bays not multiplied, the empty-run handling, `illustrative` always true, the provenance taken from the wrong tag, lifetime and FOM share swapped, the owner assets priced from the library as it is now instead of the run's, the count always 0, and both halves of the staleness test. 19 were caught at once. One was not by the new tests: the byte comparison of the library in the staleness test. For a rate change it is redundant, since the cost basis changes with the rate and the other half catches it; for a library edit the older library-staleness tests catch it. Three more on the panel line (the year shown, the mismatch warning, the rate format), all caught.

For the IC shape, 18 more by hand, all caught: the name counter (always 1, global instead of per id), the basis of a cable and of switchgear, the quantity of a cable and of switchgear bays, `upfront_per_unit` as the total, the hash (file order, the campus file left out, the cost basis left out, no NUL framing, 15 characters), the stale flag always false, a kind dropped from the map, an unknown kind passed on, `meta` fields at the top level (two ways), `build_year`, `source_hash`.

### D1b: a proposal for the pack's owner, in the IC's pack conventions. Nothing is added to the pack

Seeding the campus library from the generic defaults pack (D1) needs rows the pack does not have. This is what the campus library's cost rows would become in `defaults_pack/versions/<new date>/values.csv`, in the conventions the IC session set for the pack. It is **a proposal**: this branch adds nothing to the pack. **The IC session transcribes these rows itself unless it is asked otherwise.**

**These costs are unsourced placeholders.** Every figure in the shipped campus library is tagged `assumed` and was not checked against a price list, a datasheet or a quote. The pack's other rows are sourced (technology-data v0.14.0, Danish Energy Agency). Putting the campus rows beside them would make an order-of-magnitude guess look like a sourced default, so every row carries `illustrative = true` and the source `"assumed (gridspine campus_assets.yaml placeholder)"`.

| campus library field | kinds | `technology` (per library entry) | `part` | `basis` | `parameter` | `unit` |
|---|---|---|---|---|---|---|
| `capex_eur` | transformers (29) | `transformer.132_33.40mva` | `investment` | `lump` | `overnight` | `EUR/unit` |
| `capex_eur` | capacitor banks (9), shunt reactors (9), STATCOMs (9) | `capacitor_bank.33kv.10mvar` (likewise `shunt_reactor…`, `statcom…`) | `investment` | `lump` | `overnight` | `EUR/unit` |
| `capex_eur_per_km` | cables (12) | `cable.33kv.al240` | `investment` | `per_km` | `overnight` | `EUR/km` |
| `capex_eur` | switchgear (13) | `switchgear.132kv.31p5ka` | `investment` | `per_bay` | `overnight` | `EUR/bay` |
| `opex_frac` | all 81 | as above | `investment` | as above | `fom_share` | `share/year` |
| `lifetime_a` | all 81 | as above | `investment` | as above | `lifetime` | `years` |

The `basis` is the one the producer reports (`lump | per_km | per_bay`), so a pack row and an extra owner asset price the same unit. The `technology` is the library entry's id in the IC's dotted form (`TR_132_33_40` is `transformer.132_33.40mva`, ratings kept in the name), and the key is `<technology>.investment.<parameter>`, as the existing rows are.

Common columns: `illustrative` true; `source` "assumed (gridspine campus_assets.yaml placeholder)"; `currency` EUR; `currency_year` the library's `price_year` (2026, which differs from the DEA rows' 2020, the same mismatch D1a flags); `range_low`, `range_high` empty; `price_basis` left to the owner (the campus library does not say whether it is real or nominal). That is 81 entries × 3 parameters = 243 rows.

For the pack's owner:
- **A NEW pack version** directory and its pinned hash are needed. The shipped version (2026-10-05) is never edited.
- The loader's `basis` is `per_MW | per_MWh | per_km` today, with `_BASIS_UNIT` mapping each to its overnight unit. `lump` and `per_bay` would be new.
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
- **Checks.** 35 mutations by hand (engine 6, service 17, routes 3, panel 9): 33 caught at once; two survived and got tests (the best cost reported as the trial's, on the outage case where a cheaper trial fails AC; the staleness hashes, on a least-cost run made again alike on another library). Holding the NetCDF lock in the thread was first caught only by a hang; every job test now checks the lock is free at teardown.

## Part four: adding to existing transformers, replacement at end of life, tap changers (plan, 2026-10-12, no code yet)

Written before the code, as every gridspine increment is, and amended as built. The owner approved three engine follow-ups on 2026-10-12. They are numbered C13 to C15.

C13 here is **not** the reference-costs work. Another agent is replacing the asset library's placeholder costs on its own branch (the "reference-costs branch"), and that branch owns the values in `campus_assets.yaml`. Nothing below changes a value already in that file. C13 and C15 add **new optional fields** to the library and to the campus file. Their shipped data goes in a separate data commit after the reference-costs branch has merged (open question 16).

**Sources.** Only two outside sources were read for this plan, both on 2026-10-12. Neither is a standard.
- **pandapower 3.1.2's built-in transformer standard types** (`pandapower.available_std_types(net, "trafo")`, in the environment). They give:
  - an on-load tap changer on the HV side of ±9 steps of 1.5 % for every HV/MV type (380/110, 220/110, 110/20 and 110/10 kV);
  - ±2 steps of 2.5 % on the HV side for the 20/0.4 and 10/0.4 kV types;
  - vector group YNd5 at 110/20 kV and Dyn5 at 20/0.4 kV.
- **A transformer vendor's application note on paralleling** (Rex Power Magnetics, "Transformer Paralleling: Principles and Practical Considerations"). It says:
  - voltage ratios should match "within about 0.5 %";
  - per-unit impedances should match "typically within 7.5 %";
  - the phase displacement must be the same: "a Dyn11 transformer cannot be paralleled with a Dyn1 unit";
  - all units must be on the same tap position.

  The note cites no standard. IEC 60076-8 (the application guide that covers parallel operation) and IEC 60076-1 (whose tolerance table is the usual source of the 0.5 % ratio and 7.5/10 % impedance figures) were **not** read: they are sold, and a search found only previews and forum quotations. So every tolerance below is tagged `assumed`, with the reading above as its note. The plan does not claim it follows IEC 60076-8.

### Owner decisions (2026-10-12)

| increment | the owner's approval, as passed on by the coordinating session |
|---|---|
| C13 | "add units next to an existing transformer group": offer, as candidates, k new units in parallel with the existing ones, costed for the new units only, alongside replacing the whole group, so that least cost picks |
| C14 | "replacement at end of life": re-buy an asset, new or existing, when its lifetime ends within the study horizon |
| C15 | "on-load tap changer (OLTC) optimisation": let the study set transformer taps per critical hour and case |

The choices the approval leaves open are the numbered questions at the end. They will be asked as multiple choice. Each has a recommendation, and this plan is written to the recommendation.

On 2026-10-12 the owner accepted the recommended option on every question ("Accept all recommended"), and asked for C13 to start now, with C14 and C15 to follow after it.

### What exists today (surveyed 2026-10-12, on `feat/gridspine-campus-milp-job` at e671de35e)

**Transformer groups (C8).**
- A group is the transformers between the same two buses (`campus_flow.trafo_groups`).
- When every unit is `existing` and adequate, keeping the group is a zero-cost candidate. Otherwise the group is replaced whole by n = 1..3 units of one library size (`campus_invest._trafo_candidates`, docstring: "adding units next to the existing ones is not offered").
- The sizing rule assumes identical units:
  - the need is `total intact S / n` against the **smallest** rating in the group;
  - the N-1 term is the largest survivor's S (`campus_flow.size_transformers`).

  A group of mixed units is therefore judged wrongly today, whether the file supplies it or C13 builds it: the unit with the lower impedance carries more than its share.
- Neither the library nor the campus file has a vector group or tap data. `ingest.campus` builds every transformer at the neutral tap, and pandapower's tap columns (`tap_side`, `tap_neutral`, `tap_min`, `tap_max`, `tap_step_percent`, `tap_pos`, `tap_changer_type`) stay empty.

**Timing and cost (C8).**
- An item is invested once, in its `invest_period`, and costed at each period label `p` with `invest <= p < invest + lifetime`. Replacement is not modelled.
- Existing items cost nothing. A kept group gives a `keep` row with no library id.
- An existing cable that is adequate, or a rated bus that holds, is not a need, so it has no row at all.
- The run does not record how many years a period spans. `prepare_campus` writes only the period labels, and `campus_pcc.csv`'s `weight` is the hour's snapshot weighting.

**The investment case (IC) side, read only.**
- `services/finance/replacements.py` has its own replacement rule. Under `replacement_rule = "part_lifetimes"`, it re-buys every owner asset's part with a finite lifetime in year `cod_year + floor(k·L + 0.5) − 1`.
- The D2 contract says the IC refuses a `build_year` after the case COD (its default C).
- `extra_owner_assets` skips existing and unresolved rows. On master the IC does not yet read the list (no `extra_assets` hook was found in `services/results/finance_case.py`).

**Voltages (C8, C11).**
- In the model the PCC voltage is the `ext_grid` set-point `vm_pu`, which is fixed. No campus measure can move it.
- A campus voltage out of its band escalates the reactive need. If that does not shrink the excursion, the need is reported unresolved with "a tap change or voltage control may be needed (out of scope)" (`test_a_voltage_compensation_cannot_lift_is_unresolved_with_the_tap_change_named`).
- Compensation is dispatched to the PCC Q band, not to a voltage (`campus_reactive.reactive_need`).

### C13: adding units next to an existing transformer group

**Goal.** A group with existing units that fails (too small with the margin, or overloaded) gets new candidates: keep its existing units and add k new ones in parallel. Only the new units are costed. These candidates sit beside the replace-whole candidates in one list ranked by annualised cost, so least cost picks between them.

**Design.**

1. **Which groups.** Any group with at least one `existing: true` member.
   - The existing members are kept as they are.
   - Members that are not existing are planned units still to be bought. An add candidate drops them, as replace-whole does today.
   - A group with no existing member gets no add candidates: there is nothing to add to.
   - A fully existing, adequate group still has "keep" first. The add candidates follow it, for a check that escalates the group (a voltage, say).
2. **Candidates.**
   - For every compatible library entry and k = 1..`MAX_UNITS` (3), the candidate is "keep m existing + k × entry".
   - Its `annual` is k × `annualised_cost(entry)`.
   - It passes C8's adequacy pre-filter using the load-sharing estimate in 4.
   - The `Candidate` gains `keep: tuple[str, ...]`, the names of the kept existing members; it is empty for every other candidate.
   - The redundancy rule holds: a group that had two or more units keeps two or more.
3. **Compatibility for parallel operation.** An entry is offered only when it passes every rule below. Each tolerance is a field of a new frozen `ParallelRules` (default values tagged `assumed`, with the sources above as notes) and is reported in `campus_invest_scope.json`.

   | rule | test | default | basis |
   |---|---|---|---|
   | same voltages | the library's hv_kv and lv_kv equal the group's bus voltages (as for replace-whole) | exact | C8's matching rule |
   | voltage ratio | the no-load ratio at the current tap positions, `vn_hv(1+t·s_hv)/vn_lv`, within ±`ratio_tol` of each existing unit's | 0.5 % | vendor note, "within about 0.5 %"; assumed |
   | impedance | \|uk_new − uk_old\| / uk_old ≤ `uk_tol` against each existing unit | 10 % | open question 1; the vendor note says 7.5 %; assumed |
   | phase displacement | the same vector group clock number | equal | vendor note; open question 2 when a group is unknown |
   | tap changers | both with tap data: same `tap.side`, step within 0.1 percentage point, the new range covering the existing range; an existing unit with an OLTC and a new unit without one is refused (it cannot follow); neither with tap data: the ratio rule alone | as stated | the units must run on the same tap position (vendor note); the 0.1 point is assumed |

   The rating ratio is **not** filtered (open question 3). Unequal sizes with equal per-unit impedance share in proportion to their ratings, and the AC check below judges each unit against its own rating.

4. **Load sharing.**
   - **Pre-filter (estimate).** Parallel units with the same ratio share the flow in proportion to `S_r,i / uk_i`. This ignores magnetising current and assumes equal X/R. The estimate is exact for the currents only when every unit has the same X/R and no magnetising branch. The intact share is `S_i = S × (S_r,i/uk_i) / Σ_j (S_r,j/uk_j)`. For N-1, each member is taken out in turn and the survivors share by the same rule. A candidate passes when every unit satisfies `S_i × (1 + margin) ≤ S_r,i` intact and, with N-1 on, in every outage.
   - **AC check (judge).** pandapower solves each unit with its own impedance, so the re-solve gives the real sharing, the circulating current of a small ratio difference included. The judge is per unit, against its own rating.
5. **Sizing per unit** (`campus_flow.size_transformers`).
   - Each unit's utilisation is `S_i × (1 + margin) / S_r,i`, taken over the intact case and (N-1 on, two or more units) every survivor in every outage. The group is adequate when the worst is ≤ 1.
   - New columns: `utilisation` (the worst) and `worst_unit`.
   - The existing columns are kept. `required_unit_mva` stays the identical-unit figure, so part one's report reads as before.
   - For identical units the result is the same as today's rule, because equal units share equally. A test pins that.
   - This also corrects part one's "as is" judgement of mixed groups that users already supply.
6. **Putting a choice on the file** (`_apply`). The kept members stay byte for byte, `existing: true` and `pypsa_name` included. The non-existing members are removed. k new units are added with canonical names (`TR1_3`, ...; `_names`), each with the library's values, `library_id` and, once given, `vector_group` and `tap`.
7. **Investment table.**
   - An add choice writes two rows under the same need:
     - a `keep` row: m units, existing, cost 0, status `kept`;
     - a `chosen` row: k units of the entry.
   - A new column `action` (`buy` | `add` | `keep` | `replace`) says which. `replace` is a whole group bought where existing units stood. C14 adds `renew`.
   - The reason column carries "added in parallel with TR1, TR2 (existing)" on the chosen row. With an unknown vector group it also says "vector group assumed compatible (not given)" (open question 2).
8. **Timing** (open question 4). Units added to a fully existing group are invested in the **first period in which the existing units alone fail** in part one's as-is study (per-period adequacy, from the same flows). A replace-whole of a fully existing group moves to that period too. A group with planned members is still bought in the first period, since it is needed to energise the campus.
9. **Switchgear bays.** Every added unit is a bay at both of its buses, and `_bays` counts them from the file. C8 still ranks transformer candidates by their own cost and prices the bays through the switchgear need after the choice; that is a known limit of C8 that C13 does not change. The MILP prices bays exactly (C11).
10. **The MILP (C11).**
    - `open_candidates` returns the add candidates too, with C13's compatibility rules but without the adequacy pre-filter: adequacy is the linearised constraints' job.
    - Each add candidate is one more binary option. Its effect is a finite difference, as for any transformer candidate: the campus is rebuilt with the existing units plus k new ones and solved at every hour and case, so the AC sharing is in the linearisation.
    - The upper-bound pruning applies unchanged.
    - `ts` and `tl` already take the worst unit against its own rating per group (`ts_values`, `_constraints`). The one change is the scale: it becomes 1 % of the **smallest** rating in the group, instead of the first unit's rating seen, so the scale is the same whatever order the units come in.
    - Expect more finite-difference solves: compatible entries × 3 per existing group. The progress count shows them.
11. **The IC producer.** It needs no change. Kept rows are existing and skipped. Added rows are ordinary purchases. `meta` gains `action` (an additive key under `meta`; the top-level keys are unchanged).

**Worked example**, worked by hand here and to be confirmed by the solve in a test.
- The case is `test_existing_transformers_short_of_the_margin_are_replaced_not_kept`: two existing 40 MVA units at uk 12 %, one survivor carrying about 38.5 MVA.
- Today the pair is replaced by 2 × T63, at 2.4 M€ overnight.
- With C13 and a compatible T20 (uk 12 %), "keep 2 + add 1 × T20" passes:
  - intact, the shares are 38.5 × 40/100 = 15.4 MVA on each 40 and 7.7 MVA on the 20;
  - with one 40 out, the 40 carries 25.7 MVA, × 1.2 = 30.8 ≤ 40, and the 20 carries 12.8 MVA, × 1.2 = 15.4 ≤ 20;
  - with the 20 out, each 40 carries 19.25 MVA, × 1.2 = 23.1 ≤ 40.
- That test's library holds only a T63 at uk 12 %, which is compatible. "Keep 2 + add 1 × T63" (1.2 M€) therefore beats the replacement, and **the test's expected outcome changes**.
  - It is **not weakened**. It keeps its assertion, that a pair short of the margin is not kept as is. Its library gains a T63 that fails a compatibility rule (uk 15 %, 25 % off), so it still shows replace-whole when nothing can be added.
  - A new test shows the add winning when the T63 is compatible.
  - The plan records this change so that a reviewer sees it was intended.

**Data and schema.**
- Library transformers gain two optional fields:
  - `vector_group`, a tagged string, e.g. `{value: Dyn11, source: assumed}`. The tag loader accepts a string only for this field, checked against the usual notation (HV letters `Y|YN|D|Z|ZN`, LV letters `y|yn|d|z|zn`, clock 0–11).
  - `tap`: `{changer: on_load | off_circuit, side: hv | lv, neutral, min, max, step_percent}`. `step_percent` is tagged. The integers are whole numbers with `min ≤ neutral ≤ max`. C15 uses it. C13 defines and validates it, because the compatibility rule reads it.
- Campus-file transformers gain the same two optional fields, plus an optional `tap.pos` (default `neutral`). `build_campus` writes the tap columns into pandapower (`tap_changer_type = "Ratio"`). A file without them builds exactly as before.
- New: `ParallelRules(ratio_tol=0.005, uk_tol=0.10, step_tol_pct=0.1, unknown_vector_group="assume_compatible")`, passed by `invest_campus(..., parallel=ParallelRules())`. The panel does not expose it in C13: the defaults are written with the run and shown in the scope note.
- Nothing is added to the shipped YAML in C13's code PR (open question 16).

**Files.**
- `gridspine/templates/campus_assets.py`;
- `gridspine/ingest/campus.py`;
- `gridspine/static/campus_flow.py` (`size_transformers`);
- `gridspine/static/campus_invest.py` (`ParallelRules`, `_add_candidates`, `Candidate.keep`, `_apply`, `_investment`, timing, docstring);
- `gridspine/static/campus_milp.py` (`open_candidates` via C8, the `ts`/`tl` scale);
- `gridspine/drivers/campus_study.py` (passing the rules on; the docstring; the scope file);
- the panel's investment table (shows `action` and the reason; additive);
- tests: `tests/gridspine/test_campus_flow.py`, `test_campus_invest.py`, `test_campus_milp.py`, `test_campus_assets.py`, `test_campus.py`.

**Test oracles** (none runs the code under test).
- **Load sharing, by hand and against a direct net.** Two units, 40 MVA at uk 10 % and 63 MVA at uk 11 %, with vkr in the same proportion (equal X/R), pfe = i0 = 0 and equal ratios. By hand the 40 MVA unit takes 4.0/(4.0 + 5.727) = 41.1 % of S. A pandapower net written directly with `create_transformer_from_parameters` gives the same per unit to solver tolerance, and the campus build gives the same numbers as that net.
- **Per-unit sizing.** For identical units, the `size_transformers` table equals the identical-unit rule restated in the test. For a mixed group, the utilisation equals `max S_i(1+m)/S_r,i` read from the direct net.
- **Least cost by brute force.** For a small library (three sizes, two uk values), every candidate (replace-whole n × size, keep + k × size) is listed in the test and checked with its own AC solve and the per-unit rule. The cheapest that passes is what `select_assets` chooses.
- **Compatibility.** Each rule is tested on its boundary:
  - uk 11.0 % against 10.0 % (10 % off) is in; 11.01 % is out;
  - 110/21 kV against 110/20 kV (4.8 %) is out;
  - Dyn11 against Dyn5 is out;
  - an OLTC unit against an existing OLTC with step 1.25 % against 1.5 % is out;
  - an existing OLTC with a new unit without tap data is out;
  - an unknown vector group follows open question 2.
- **Timing.** Three periods, with the existing pair adequate in the first and failing from the second: the added unit is invested in the second, by hand from the hourly loads.
- **MILP.** On the test campus with an existing pair, the MILP's choice equals the brute-force minimum over every option, add candidates included, solved independently in the test. This extends the 24-combination test.

**Mutation targets.**
- each tolerance's `<` / `<=`;
- the share weights (`S_r/uk` against `S_r×uk`);
- the smallest rating used instead of each unit's own;
- the existing members dropped, or costed;
- k counted from 0;
- the redundancy rule;
- the vector-group clock compared as a string;
- the ratio compared at the neutral tap instead of the current one;
- the timing at the first period;
- the `keep` row left out;
- the MILP not given the add candidates;
- the scale from the first unit.

**Risks.**
- The pre-filter's sharing estimate can disagree with AC when X/R differ. The AC judge decides and an escalation follows, so the risk is an extra iteration, not a wrong result.
- Finite differences grow with the add candidates. On the Data Center hub (500 s today) the count is reported.
- Changing `size_transformers` changes part one's numbers for mixed groups. That is a correction, and it is named in the PR.

**Out of scope.**
- Removing or relocating existing units.
- Salvage value of a replaced group.
- Mixed groups with more than one new library type (k units of one entry only).
- Three-winding units.
- Circulating-current losses as a separate cost (they are in the AC losses but not priced).
- Separate pricing of an OLTC.
- Verification against IEC 60076-8.

**Estimated size.** Medium to large: about 450 lines of engine and 400 of tests, with one panel touch. One implementer session, Opus.

### C13 as built (2026-10-12)

Branch `feat/gridspine-campus-add-units`, cut from this plan's branch (PR #100's branch plus Part four) with master and PR #100's latest merged in.

**Where the plan proved wrong, and what was built instead.**
- **The worked example.** The units of `test_existing_transformers_short_of_the_margin_are_replaced_not_kept` are wound 110/21 kV on a 20 kV busbar, and every library entry at the group's voltages is 110/20 kV. Their no-load ratios are 5.0 % apart (|r_new − r_old| / r_old) against the 0.5 % allowed, so under the plan's own ratio rule nothing can be added, and **that test's outcome does not change**. It keeps every assertion; it now also asserts that no addition was offered and that the row's action is `replace`, and its docstring says why.
  - The plan's add example is shown on the same campus with the pair wound 110/20 kV (`rewound_spec`): `test_units_are_added_next_to_an_existing_pair_short_of_the_margin` (keep 2 + 1 × T63 at 1.2 M€, against 2 × T63 at 2.4 M€), and `test_an_existing_pair_with_only_an_incompatible_unit_in_the_library_is_replaced_whole` (a library holding only a T63 at 15 %, 25 % off).
  - The plan's "110/21 kV against 110/20 kV (4.8 %)" is 5.0 % with the deviation measured against the existing unit's ratio, which is how the rule is built.
- **The vector group on the campus file already existed** (island plan I1): an untagged string, `D|Y|YN` and `d|y|yn`, no zig-zag, assumed Dyn11 for the fault model when absent. C13 reuses it as it is. The library's new `vector_group` is tagged, in the same notation (no `Z|ZN`: the campus fault model has no zig-zag winding, so a library Z unit could not be built). "Not given" means absent from the file; the island build's assumed Dyn11 does not count. An added unit carries the library's vector group as the campus file's plain string; the library's tag stays in the library.
- The regex and the tap block's checks live in one place, `gridspine/schema/transformer.py` (`check_vector_group`, `check_tap`, `ratio`), used by the library loader and the campus build.

**The choices the plan left open.**
- **Taps in the compatibility rule.** The new unit is put at the existing units' offset from their neutral (the first existing unit with taps), so all run on the same position, and its ratio is compared there. An existing unit with an off-circuit tap and a new unit without taps: the ratio rule alone, at the existing position. A new unit with taps beside units without: at its neutral. An existing on-load changer with a new off-circuit one is refused (it cannot follow). Tap steps are compared to 1e-9 percentage point, so 1.6 % against 1.5 % is within the 0.1 point; uk and the ratio are compared exactly (11.0 % against 10.0 % is in).
- **The pre-filter's outage flow** is the group's largest total with one unit out in part one's flows (`_outage_total`); the intact flow is the largest intact total. Replacements keep their old pre-filter unchanged.
- **The redundancy rule** holds by construction for an addition (m ≥ 1 kept plus k ≥ 1 added), so it has no code of its own there.
- **Bought units carry the library's tap block and vector group** for replacements as well as additions, so C15 finds tap data on what the study buys. A unit's tap is written at the neutral plus its offset.
- **The table.** `action` is `buy`, `add`, `keep` or `replace`; `replace` also marks an existing cable re-conductored. The keep row of a fully kept group now carries its units (it said 0). An addition's chosen row says "added in parallel with TR1, TR2 (existing)", then "vector group assumed compatible (not given)" when that applies; an unresolved addition keeps its unresolved reason first.
- **Timing.** A period with no selected hour is skipped when looking for the first failing one; a group that never fails as described (an addition forced by a later re-check) is bought in the first period.
- **The MILP.** C8's choice is matched to the open candidates by items, existing and `keep`: a replacement "1 x T40" and the addition "keep TR1 + 1 x T40" have the same items, and without `keep` the warm start took the replacement (a test pins this). `select_assets_milp` and `invest_campus` take `parallel`.
- **Sizing.** `size_transformers` gives `utilisation` and `worst_unit`; `adequate` is every unit's `S x (1 + margin)` within its own rating plus 1e-9 MVA (the old tolerance). The re-check's failure text names the worst unit and its utilisation.
- **The IC producer** adds `meta.action` (None for a table written before C13); kept units are existing and stay out.
- **Panel.** The investment table has an Action column and, under the need, the row's reason (not for an unresolved row, whose reason the alert already lists); a line under the scope note states the rules, all assumed. The copilot's investment notes gain one line on additions and `scope.parallel`; `harness/catalogue.py` is unchanged.

**Tests** (oracles that run no code under test): the 41.1 % share by hand and in a direct pandapower net; per-unit utilisation from direct nets, mixed and three-unit; identical units against the old rule restated (five margin and N-1 settings); a brute force over every replacement and addition of a six-entry library, each solved directly with the per-unit rule (the cheapest is keep 2 + 1 × T20B at 10.8 %, which the selector buys with no escalation); the candidate list by hand from the S_r/uk estimate; every compatibility rule on its boundary; the timing by hand over three periods; the tap sign by hand (1/1.03 pu) and a tapped direct net; and the MILP joint case on an existing pair (adding T_A at 13 % needs a bank, adding T_B at 11 % does not; the brute force over 40 combinations and the MILP agree on T_B alone, C8 does not).

**The Data Center template**, re-run as in "C8 as built" on the same prepared and ranked run (one period, pf 0.95, shipped library), with the merged base and with this branch: the same 1 × 63 MVA 132/33 kV, 3 × 25 MVA 33/11 kV and switchgear, €6.891 M capex, €610.3 k a year; the invested file and the re-checked compliance are byte for byte the same. The only difference is the new `action` column (`buy` on every purchase). Its campus has no existing transformer, so no addition is offered.

**Mutations.** 50 by hand: 44 in the engine (loader 9, sizing 4, compatibility 12, candidates 8, timing 2, table and scope 4, MILP 4, driver 1), 5 in the panel, 1 in the IC producer. 45 were caught at once. Five survived the first run:
- the tap sign of the ratio (HV side) and a range not covering the existing maximum: tests added (`test_a_tapped_unit_is_compared_at_its_effective_ratio`, which also pins the LV side, a mutation then caught; a range case `-9..8`);
- the share weights `S_r x uk` for `S_r / uk`: the candidate list is now also checked by hand at 35.5 MW, where only the right weights refuse one 10.8 % unit;
- k counted from 0: the adequate pair's additions must be k = 1..3;
- the MILP's open set with the adequacy flag on: **equivalent**, because `open_candidates` passes zero flows, which pass any estimate.

All the others are caught now. The redundancy rule has no code of its own for additions (above), so it has no mutation.

**Gates.** `tests/gridspine -k "campus or island"` 1100 passed; the backend campus, chat-tool, bundle, IC-extra-asset, assistant-inventory and navigation tests 764 passed, 2 skipped (the live probe), and the 2 inventory failures on the first run were the stale copilot inventory, cleared by merging PR #100's inventory commit (12 passed); `tsc` clean and the full `vitest` 4212 passed.


### C14: replacement at end of life

**Goal.**
- Every purchase whose lifetime ends inside the study horizon is bought again, like for like, in the year it ends.
- An existing asset is bought again when its own life ends. That needs its age, so the campus file gains a commissioning year.
- The tables and the IC producer carry one row or entry per purchase.

**Design.**

1. **The horizon.**
   - Period `p` spans `[p, p + w_p)`. `w_p` is the project's `investment_period_weightings["years"]`, which `prepare_campus` records in the manifest as `period_years`.
   - Where a value is missing or not positive, `w_p` is the gap to the next period, and for the last period the previous gap. That case is flagged as assumed.
   - A single-period project has `w = 1` (open question 6): its horizon is one year, so nothing is replaced except an asset already past its life. Over a longer horizon the IC's own replacement rule takes over.
   - The horizon end is `H = last p + w_last`.
2. **Purchases.**
   - Purchase 1 is today's: in `invest_period`, at year `b_1 = invest_period`.
   - Purchase k + 1 is in year `b_{k+1} = b_1 + floor(k·L + 0.5)`. This is the IC's rounding of a non-whole lifetime (half up, `replacements.replacement_year`), but in the **first service year of the new unit**, as `build_year` is for a first purchase. The IC's year is one earlier, the last service year of the old unit with financial close a year before COD. That difference is named for the IC (below).
   - Purchases are made while `b < H`.
   - The same library entry and units are bought: like for like, sized for the worst period as today. No re-check is needed, because the electrical data are the same.
   - Unresolved rows, `none` and `not_needed` rows are never replaced.
3. **Existing assets.**
   - Kept transformers and existing cables are replaced. So are, under open question 9, existing transformers and cables that are not needs but carry a `library_id`; they get zero-cost `keep` rows so that their replacement can be costed.
   - An existing asset is re-bought at `commissioning_year + floor(L + 0.5)` and every L after.
   - L and the price come from its `library_id`'s entry. Without a `library_id` the replacement is reported as `unpriced` with a reason, like an unresolved need: listed, not summed.
   - An asset already past its life at the first period is re-bought in the first period, flagged `overdue` (open question 10).
   - **No commissioning year** (open question 5): the asset is assumed to be at mid-life at the first period, `commissioning_year = first period − round(L/2)`. This is `assumed`, said on the row (`commissioned_source = "assumed: mid-life at the first period"`), and listed in the run's notes.
4. **Cost per period** (`campus_cost.csv`).
   - `capex_eur(p)` is the overnight cost of every purchase with `p ≤ b < p + w_p`. For first purchases `b = p`, so the figure is today's.
   - A new column `capex_replacement_eur(p)` is the part of that from purchases k ≥ 2 and from existing assets' replacements.
   - `annualised_eur_per_a(p) = Σ_j A_j × |[b_j, b_j + L_j) ∩ [p, p + w_p)| / w_p`: each purchase's annuity weighted by the share of the period it is in service.
     - Whenever no purchase starts or ends inside a period, this equals today's point test at the label.
     - With like-for-like replacement it gives a flat cost from the first purchase to the horizon.
     - It differs from today only where a life now ends inside a period. Today that period is counted in full, or not at all, by the label. The tests pin both cases.
5. **Ranking.** Unchanged (open question 7). C8 and the MILP rank by annualised cost, which like-for-like replacement leaves the same. Keeping an existing unit is still ranked at zero, although it now carries a costed replacement later. The run notes and the plan ledger state this. A present-value ranking would be a later increment (it changes the MILP's objective).
6. **Investment table** (`campus_investment.csv`). It has one row per purchase. New columns:
   - `purchase`: 0 for a kept existing asset, 1 for the first buy, k for later ones;
   - `build_year`: the year bought;
   - `service_to`: `min(b + L, H)`;
   - `replaces`: the row's lineage predecessor, as `need|library_id|purchase`;
   - `commissioned`, `commissioned_source`: existing rows;
   - `action`: C13's, plus `renew`.

   `invest_period` stays the period that contains `build_year`. Old tables without these columns still read: the service falls back to `invest_period`.
7. **The MILP.** `select_assets_milp` writes C8's files through `_investment`, so it inherits the purchases. Its objective and `campus_milp_comparison.csv` (annual) are unchanged.
8. **The IC producer** (`campus_electrical_service.extra_owner_assets`).
   - One entry per purchase with k ≥ 1 that is neither existing nor unresolved. An existing asset's replacement is a purchase, so it does appear.
   - Top-level keys unchanged. `build_year` is the row's `build_year`. `name` keeps the counter rule `campus:<library_id>#<k>`, unique.
   - `meta` gains `purchase`, `lineage` (stable over one asset's purchases), `replaces` (the previous entry's `name`, or null), `service_to` and `commissioned_source`.
   - `campus_study_hash` is unchanged: the same files are hashed.

**What the IC side must know** (to agree with the IC session before code; open question 8).
- **Double counting.** Under `replacement_rule = "part_lifetimes"`, the IC re-buys every owner asset with a finite part lifetime. Campus entries that already list their replacements would then be replaced twice. The recommendation is that the IC does not generate replacements for an extra owner asset with `source == "campus_study"`, because its replacements are entries of their own. A campus entry's last purchase alive at the IC's horizon is still valued by `remaining_life_terms` as for any asset.
- **Build year after COD.** A replacement's `build_year` is after COD by design, which the IC's default C refuses. The IC needs to accept `build_year > COD` for entries with `meta.purchase ≥ 2`, or for campus entries generally.
- **The year convention.** A campus `build_year` is the first service year of the new purchase. The IC's generated replacement year is one year earlier. The IC escalates from `base_year` as for its own replacements; the campus does not escalate.
- **Horizons differ.** The campus horizon is the project's periods. The IC's is `analysis_years` from COD. Replacements the campus horizon misses but the IC horizon covers are not in the list. With open question 8's recommendation the IC adds nothing for them, so the IC should flag a campus asset whose `service_to` falls inside its analysis horizon with no successor entry (`campus_life_ends_unreplaced:<name>`). That flag is theirs to add.

**Data and schema.**
- Campus file: an optional tagged `commissioning_year` (a whole year, positive) on transformers, cables and compensation entries.
- Manifest: `period_years`, `{period: years}`, written by `prepare_campus` from the project's weightings, with a flag when assumed.
- Tables: the columns above.
- No library change. `lifetime_a` is already there.

**Files.**
- `gridspine/ingest/campus.py`;
- `gridspine/producers/campus.py` (the period weightings);
- `gridspine/drivers/campus_study.py` (manifest, horizon, notes, docstring);
- `gridspine/static/campus_invest.py` (`_investment` purchases, `keep` rows for non-need existing assets, cost per period);
- `pypsa-gui/backend/services/campus_electrical_service.py` (`extra_owner_assets`; `get_investment` summary);
- the panel's investment and cost tables (a purchases column, a replacement capex column; additive);
- tests: `tests/gridspine/test_campus_invest.py`, `test_campus_study.py`, `test_campus.py`, and the backend service tests.
- **Untouched:** `services/finance/*`, `results/finance_case.py`, `models/finance.py`.

**Test oracles.**
- **Purchases by hand.**
  - Periods 2030/2040/2050 with w = 10, so H = 2060. A 25-year asset bought in 2030 is bought again in 2055, inside period 2050.
  - A 40-year asset bought in 2030 is not bought again.
  - L = 12.5 from 2030 gives 2030, 2043 (12.5 rounds up) and 2055 (25.0).
  - An existing asset commissioned in 2000 with L = 40 is re-bought in 2040.
  - With no year and L = 40, at the first period 2030, it is assumed commissioned in 2010 and re-bought in 2050.
  - One commissioned in 1980 is overdue and bought in 2030.
- **Cost table by hand.** For the cases above, every `capex_eur`, `capex_replacement_eur` and `annualised_eur_per_a` is written out in the test. That includes a life ending mid-period (the share weighting) and the identity with today's numbers when nothing ends inside a period. The identity is checked against the old rule restated in the test.
- **Horizon.** A purchase exactly at `H` is not made, and one a year before is. A single-period project replaces only overdue assets.
- **IC entries.** On a hand-made run directory with known purchases:
  - one entry per purchase;
  - `build_year` per purchase;
  - names unique;
  - `meta.replaces` chains the lineage;
  - existing `keep` rows absent, their replacements present;
  - top-level keys exactly the eight of the contract.

**Mutation targets.**
- `b < H` against `b <= H`;
- the rounding (`round`, `ceil`, `floor` without +0.5);
- the replacement in the last service year;
- the capex in the period by label instead of by span;
- the share weighting dropped (the point test kept);
- the mid-life default as new, or as end of life;
- overdue assets kept as is;
- unresolved rows replaced;
- the IC `build_year` from `invest_period`;
- `meta.replaces` pointing to itself;
- a `keep` row emitted to the IC;
- the existing row's replacement left out.

**Risks.**
- The IC agreement (double counting, build year after COD) is needed before the producer changes. Until then the producer change waits, and the engine half can land alone.
- Projects whose period weightings are not set give assumed spans, and the flag must be visible on the panel.

**Out of scope.**
- A replacement of another size or type (a re-optimisation at end of life).
- Escalation of replacement prices (the money stays in the library's price year, as D1a).
- Salvage and residual value in `campus_cost.csv` (the IC values the remaining life).
- Switchgear and bus ratings without a library link.
- Ageing that changes electrical data.

**Estimated size.** Medium: about 250 lines of engine, 80 of service, 40 of panel, and 350 of tests. One session, Opus for the engine and contract, Sonnet for the panel.

### C14 as built (2026-10-12)

Branch `feat/gridspine-campus-replacement`, cut from C13's branch (`feat/gridspine-campus-add-units`) with master merged in. Built to the owner's answers (option (a) on every question). C15 (tap changers) was built in parallel on its own branch; C14 stays out of the re-check and the taps.

**Where the plan proved wrong, and what was built instead.**
- **A purchase serves until its successor starts, not for `[b, b + L)`.** With a non-whole lifetime the plan's `b + L` leaves gaps and overlaps between purchases (L = 12.5 from 2030: `[2030, 2042.5)`, then `[2043, 2055.5)` against a third bought in 2055), so the cost is not flat, which the plan says it is. `service_to` is therefore `min(b_{k+1}, H)`, with `b_{k+1}` the rounded year of the next purchase, made or not. For a whole lifetime the two are the same. A test pins the flat cost at L = 12.5 (`test_a_non_whole_lifetime_rounds_half_up_and_the_cost_stays_flat`).
- **pypsa gives `years = 1` when a project sets no weightings.** A multi-period project without weightings therefore has one-year spans with gaps between them (2030 spans 2030 only), not the "missing" case the plan's gap rule is for. That is what the project says, so it is taken as given. A renewal that falls into a gap (2037 between 2030 and 2040) is still made and listed, under the last period at or before it, counts in no period's capex and is counted in the annual cost of the spans it serves; the notes say so (`test_a_renewal_between_the_periods_spans_is_listed_and_counted_where_it_serves`). The gap rule applies where pypsa gives a weighting that is missing, NaN or not positive. A single-period network has no weighting and spans one year; a multi-period network with one period and a weighting uses the weighting.
- **One test's cost changed on purpose.** `test_an_existing_adequate_transformer_is_kept_at_zero_and_compensation_is_bought_when_first_needed` bought a 5-year bank in 2040 over 2030/2040/2050 and costed nothing in 2050. The bank is now bought again in 2045, 2050 and 2055 (spans of ten years, the gaps, H = 2060), so 2050 carries its annuity and each decade buys two. Its docstring says why; every other assertion stands.

**The choices the plan left open.**
- **Manifest.** `prepare_campus` writes `period_weightings` (the project's, `null` where none), `period_years` (the spans, `campus_invest.period_spans`) and `period_years_source` (`project`, `assumed: the gap to the next period`, `assumed: the gap from the previous period`, `single period: one year`). `invest_campus` passes `period_weightings` on; a run prepared before C14 has none and takes the gap rule, flagged.
- **Purchase numbers.** A first buy is 1, later ones 2, 3, ... An existing asset's keep row is 0 and its renewals 1, 2, ... (its first renewal is its first purchase in the study). `action` is `renew` for every renewal.
- **Lineage.** `replaces` is `lineage|purchase` of the row replaced. The lineage of a bought item is `need|library_id`, as the plan wrote; that of an existing asset is `need|<member names>`, because an addition may buy the same library id its kept units carry, and the two lineages must differ. The table has a `lineage` column.
- **Keep rows.** The existing members of a kept or added-to group are grouped by library id and commissioning year: identical units give one keep row (as C13), mixed ones one row per group, each renewed on its own life. An existing cable with a `library_id` that is not a need, and every compensation entry of the campus file with a `library_id` (the file has no `existing` flag for compensation, and C8 has always priced it at zero), get a keep row too (open question 9).
- **Unpriced.** Without a library entry neither the lifetime nor the price is known, so there is no year to list a renewal in. The keep row's reason says "replacement unpriced: no library_id" (or "library_id X is not in the library the run used") and the notes list it; nothing is summed.
- **Overdue.** The renewal row says "overdue: its life ended in Y, before the first period P; bought again in P", and later renewals run from P. A life ending exactly at the first period is not overdue.
- **Unresolved.** An unresolved need's rows, including a kept group that ends unresolved, are never renewed.
- **Notes.** `campus_invest_scope.json` gains `horizon`: `period_years`, `source`, `horizon_end` and `notes` (the ranking line, assumed spans, assumed commissioning years, overdue and unpriced replacements, renewals in no period's span).
- **Ranking.** `ranked_annual` (chosen and kept rows without the renewals) is what the MILP's `c8_cost` and the service's `_least_cost_total` read; the MILP's comparison leaves the renewals out. `select_assets_milp` takes `period_years` and passes it to C8; its own table is written on the same horizon.
- **The IC producer, default unchanged.** Checked in the IC code on master: `_extra_cod_flags` refuses any extra asset built after the case COD (`extra_asset_staged_build:<name>`, unconditionally, not a default), and `replacements._generated` re-buys every asset in `FinanceCase.assets`, the extra ones included, under `part_lifetimes`; its year is one earlier than the campus's; its horizon is `analysis_years`. Emitting the renewals would make a real IC case refuse or count twice. So `extra_owner_assets(project)` lists exactly what it did before C14 (the first purchases, same names, same `build_year`), with `meta` gaining `purchase`, `lineage`, `replaces`, `service_to` and `commissioned_source`; `extra_owner_assets(project, replacements=True)` (and `owner_assets_response(..., replacements=True)`) lists every purchase, renewals counted on after the first purchases so that their names do not move, each with its own `build_year`, `meta.replaces` the replaced entry's name (None for a first purchase and for an existing asset's first renewal). The IC change is handoff issue `.scratch/campus-ic-extra-owner-assets/issues/04-campus-replacements.md` (04 after 01-03 of `feat/gridspine-campus-ic-source`). For a single-period project the default list is also the right input: the campus horizon is one year, and the IC's own rule covers the asset's later life.
- **Panel.** The investment table has a Bought column (the build year; an existing asset's "in service since Y", flagged "(assumed)"), the cost table "of which bought again" when the run has it, and a line states the horizon, each span flagged when assumed, with the notes. The copilot's investment notes say how replacements are made and reported; `harness/catalogue.py` is unchanged, so the assistant inventory is too.
- **Campus file.** `commissioning_year` is tagged, positive and a whole year, on transformers, cables and compensation entries; it changes nothing in the net.

**Tests** (oracles that run no code under test): the purchase years by hand (L = 25, 40, 12.5, 12.4, 29 and 30 against H = 2060; half up, a purchase at H not made, one a year before made); existing assets by hand (2000 and L = 40 renewed in 2040; no year, mid-life 2010, renewed in 2050; 1980 overdue, bought in 2030; due exactly at the first period, not overdue; a single period renews only the overdue); the spans (given, missing, zero, NaN, single period); the cost table by hand on three decades for a new purchase (L = 25: flat annuity, capex in 2030 and 2050), a non-whole life (flat), an existing pair and cable (the cable renewed in 2035 gives half its annuity to 2030), a renewal in no period's span, and an existing compensation entry; the rule before C14 restated where nothing ends inside a period; unresolved rows never renewed; an addition renewing its kept and its new units on their own lives; the MILP on the joint case over 50 years (C8's cost and the comparison are the first purchases', the MILP's T_B renewed in 2070); the manifest from a weighted, a zero-weighted and a single-period network, and a run prepared before C14; the producer on a hand-made table (default list unchanged, with replacements one entry per purchase, names, build years, the `replaces` chain, keep rows absent, their renewals present, the eight top-level keys); the panel (Bought, the replacement column, the horizon line and notes, a run from before C14).

**The Data Center template**, re-run as in "C13 as built" on the same prepared and ranked run (one period, pf 0.95, shipped library), once on the run as it was and once prepared again (the manifest now says `period_weightings: {2030: null}`, `period_years: {2030: 1}`, "single period: one year"): **unchanged**, as expected with one period and nothing existing. The same 1 × 63 MVA 132/33 kV, 3 × 25 MVA 33/11 kV and switchgear, €6.891 M capex, €610.3 k a year, no renewal (the horizon ends in 2031); `campus_invested.yaml`, the re-checked compliance, the dispatch and the history are byte for byte C13's. The differences are the new columns (`purchase` 1, `build_year` 2030, `service_to` 2031, the lineage), `capex_replacement_eur` 0 and the scope's `horizon`.

**Mutations.** 50 by hand: 33 in the engine, the MILP and the driver, 9 in the producer, 8 in the panel. 44 were caught on the first run. Of the 6 others: three (the share weighting dropped, existing cables left out, a renewal keeping the existing flag) were caught by `test_kept_existing_assets_are_renewed_from_their_commissioning_year` once the runner's test selection included it; two (the ranked total and the least-cost total including the renewals) were not valid mutations as first written (an emptied `if` body, a syntax error counted as caught) and, rewritten, are caught; one (existing compensation left out) was a real survivor and got `test_an_existing_compensation_entry_with_a_library_id_is_kept_and_renewed`. All 50 are caught now.


### C15: on-load tap changer optimisation

**Goal.** Where a campus voltage is out of its band at a critical hour and case, the study sets the OLTC taps of the campus transformers to bring it back. It reports the taps used. It reports a failure when the tap range cannot fix the voltage, in place of today's "needs a tap change (out of scope)".

**Design.**

1. **Data.**
   - Uses C13's optional `tap` block on library and campus transformers. Only `changer: on_load` units are moved. An `off_circuit` tap stays at the file's `tap.pos` (open question 14).
   - Typical values for the shipped library, all `assumed`, in the data commit after the reference-costs branch merges (open question 16):

     | class | changer | side | range | step | note |
     |---|---|---|---|---|---|
     | 132/33, 110/20 kV | on_load | hv | −9..+9 | 1.5 % | pandapower 3.1.2 standard types for 110/20 kV (read 2026-10-12); 132/33 kV by analogy |
     | 33/11 kV | on_load | hv | −9..+9 | 1.5 % | by analogy with the HV/MV types; no 33/11 kV type was read |
     | 20/0.4 kV | off_circuit | hv | −2..+2 | 2.5 % | pandapower 20/0.4 kV types give ±2 × 2.5 %; that these are off-circuit is assumed (the types do not say) |

   - Drafted campus files (`producers/campus.py`) get no tap block. A drafted transformer stays fixed until the user adds data. The draft notes say so.
2. **Where taps act in the dispatch.**
   - **Order.** For each hour, today's reactive dispatch comes first: inverters, then STATCOMs, then bank or reactor steps, to the PCC Q band, with `residual=False`.
   - **Tap search.** Then, if a campus voltage is out of its band, the taps are searched with that Q dispatch held.
   - **Re-settle.** The reactive dispatch is then re-run with the new taps, because the transformers' reactive losses move.
   - **Repeat.** This repeats until the taps do not change, for at most `TAP_ROUNDS` = 3. If they are still moving at the cap (hunting), the round with the smallest worst excursion is kept, and the result is flagged.
   - **Why Q first.**
     - Q serves the PCC band, a grid-code limit, and the taps serve the campus voltages.
     - An hour whose voltages already pass keeps its file taps and gives exactly today's numbers. That is a regression property a test pins.
   - **Rejected: taps first.** It reaches the same fixed point but moves taps at hours that do not need it.
   - **Rejected: inverter volt-var to the voltages.** Out of scope.
3. **The search** (new `gridspine/static/campus_taps.py`).
   - **Top-down.** Groups with an OLTC are visited top-down from the PCC (radial order, breadth first). This is the usual time grading, upstream before downstream.
   - **Per group.** Within each group, from its current tap, the search walks one step at a time in the direction that reduces the worst excursion of the buses fed through that group. It stops when a step no longer reduces it, or at the range end.
   - **Parallel units.** They move together (master-follower): one tap position per group, which C13's compatibility rule makes meaningful.
   - **Objective** (open question 13). All voltages in band first. Among equal excursions, the fewest steps from neutral. No money is put on a tap move: the critical hours are not consecutive, so a move between them is not a real operation.
   - **Rejected: pandapower's `DiscreteTapControl` with `run_control=True`.**
     - Each controller holds one transformer's own bus within a band. It does not see the buses further downstream.
     - It has no master-follower rule for a parallel group. Units could end on different taps, with a circulating current.
     - It adds a controller loop inside the load flow whose iteration count would be one more thing to explain.
     - It stays a test oracle candidate instead.
4. **Cases** (open question 11). The taps are set **per hour and per case**, with the Q dispatch held from the intact case as today. An N-1 case is then the steady state after the outage once the tap changers have acted (minutes), with the site's Q set-points unchanged.
   - This departs from C8's "nothing re-dispatched in N-1" for the taps only. It is stated on the panel and in the compliance detail.
   - With option (b) of question 11, taps would be held across the cases like the Q dispatch.
5. **Grid-code view, and the PCC transformer.**
   - In this model the PCC voltage is the grid's set-point (`ext_grid`, fixed `vm_pu`). Taps of the PCC transformer move the campus side and, a little, the PCC Q, never the PCC voltage.
   - So a `pcc_voltage` failure is never sent to the taps. It is reported as "the grid set-point is outside the band: the operator's voltage, not a campus measure".
   - Who controls the PCC transformer's taps is a study setting, `pcc_tap_control`: `campus` (default) or `grid_operator`. This mirrors `pcc_switchgear` (open question 12).
     - When the campus owns the HV/MV substation, its OLTC is the site's, under whatever voltage-control mode the connection agreement sets. This is assumed, not read from a code.
     - With `grid_operator`, the PCC transformer stays at its file tap and is never searched.
   - The choice is written to `campus_invest_scope.json`.
6. **Failures.**
   - **Range exhausted.** A voltage still out of band after the search names the group, its tap at the range end and the bus, for example "TR1+TR2 at +9 (max), MV2 0.893 pu".
   - **No OLTC.** A group with no OLTC data is named as such: "TR3 has no on-load tap data".
   - **Escalation.** In C8 a voltage failure is seen after the taps (they act inside each re-check). It still escalates the reactive need. When that does not shrink the excursion, the need is unresolved with the tap reason above, in place of "(out of scope)".
   - The existing test's expected reason text changes accordingly. It is amended, not weakened: it still asserts unresolved and the voltage below 0.9. Its 30 % impedance unit gets no tap data, so the reason must say "no on-load tap data".
   - A new test gives the same unit an OLTC and asserts that the voltage is fixed by taps.
7. **Part one and the re-check.** `size_campus` (as is) solves at the file's taps, so part one is unchanged. `select_assets`'s re-check (with measures) searches the taps. So "as is" means the taps as described, and "with measures" means taps set by the study.
8. **The MILP** (open question 15).
   - Taps are **set by the same rule inside every AC evaluation**: `evaluate` and C8's reference dispatch for the margin, `reference`, both run the search.
   - The linearisation is then taken at fixed taps. The Jacobian sensitivities to Q are at the tapped point, and a tap change the rule makes between iterations is part of the linearisation error that beta backs off.
   - The finite differences per candidate run the rule too.
   - Rejected for now: **integer tap variables linearised** per group, hour and case (dV/dtap by finite difference). That would choose taps and Q jointly, but adds integers × hours × cases and a tap-sign sensitivity to verify. It is ledgered as a later increment if the fixed-tap loop is shown to miss a cheaper point.
9. **Outputs and setpoints.**
   - `apply_setpoints` gains `"trafo_tap": {name: pos}`, so a dispatch with taps can be re-solved like any other. `ReactiveResult.setpoints` carries it.
   - New file `campus_invest_taps.csv`: `period, hour, case, transformer, group, tap_pos, ratio_pct, moved_by`.
   - The compliance detail names the taps at the worst hour.
   - The panel gets a taps table (collapsed) and the `pcc_tap_control` setting.
   - `campus_get_study` reports how many hours moved a tap (read only, additive).

**Files.**
- new `gridspine/static/campus_taps.py`;
- `gridspine/static/campus_flow.py` (`apply_setpoints`);
- `gridspine/static/campus_reactive.py` (`setpoints` carries the taps; the dispatch itself is unchanged);
- `gridspine/static/campus_invest.py` (`_study` with measures, the reason text, scope);
- `gridspine/static/campus_milp.py` (`evaluate`, `reference`, `setpoints`);
- `gridspine/drivers/campus_study.py` (setting, file, docstring);
- `gridspine/ingest/campus.py`: `CAMPUS_LEDGER` gains "taps per hour and case after the OLTC acts; Q held"; the tap block itself is C13's;
- `pypsa-gui/backend/services/campus_electrical_service.py` and the route settings model (the new setting; additive);
- the panel;
- `harness/catalogue.py` (additive);
- tests: new `tests/gridspine/test_campus_taps.py`, `test_campus_invest.py`, `test_campus_milp.py`, backend and panel tests.

**Test oracles.**
- **The tap sign by hand.** One transformer, no load, pfe = i0 = 0, tap on the HV side. The LV voltage is `V_hv / (1 + t·s)`: at t = +2, s = 1.5 %, it is 1/1.03 = 0.97087 pu. A pandapower net written directly with `tap_pos` set gives the same. This pins the sign of a tap.
- **Brute force.** On test campuses with one and with two OLTC groups (19 and 19 × 19 = 361 positions per hour), every position is solved independently in the test. The search's worst excursion equals the brute-force minimum where the walk is exact (one group). With two groups, the search finds an in-band setting whenever brute force does, on every test hour, and reports unresolved exactly when brute force finds none.
- **Regression.** An hour with every voltage in band gives exactly today's flows, dispatch and investment table.
- **Re-solve.** The taps written in `campus_invest_taps.csv`, put on a directly written pandapower net with the written Q dispatch, give the reported voltages.
- **PCC.** With `grid_operator`, the PCC transformer's tap never moves, and a `pcc_voltage` failure is reported with the set-point reason, never sent to the taps.
- **MILP.** On a voltage case whose fix is a tap or a STATCOM, the MILP and C8 take the tap (zero cost). The brute-force minimum over options, with the tap rule applied to each in the test's own solve, is the same.

**Mutation targets.**
- The tap sign.
- The step in percent against a fraction.
- The tap side.
- Parallel units moved separately.
- The order: taps first, so passing hours move.
- The re-settle of Q left out.
- The hunting cap, and keeping the last round instead of the best.
- The neutral tie-break.
- The range bounds off by one.
- An off-circuit tap moved.
- `pcc_tap_control` ignored.
- A PCC voltage sent to the taps.
- Taps held in N-1 (against question 11's choice).
- The MILP's reference dispatch without taps.
- The setpoints dropped on re-solve.

**Risks.**
- **Run time.** A search per hour and case inside every MILP evaluation and finite difference adds up to about groups × steps × hours × cases load flows. On the Data Center hub (2 groups, 14 hours, 4 cases) the walk is short, but the finite differences multiply it. Measure it, and report it in the progress note.
- **Non-convergence at a range end.** Treated as a failed position, not as an error.
- **Semantics.** Taps acting in N-1 while Q is held is a stated modelling choice that a reviewer may question. The panel says it.

**Out of scope.**
- Tap-changer dynamics, time delays and the number of operations (wear).
- Line-drop compensation.
- Off-circuit tap optimisation (one setting for the whole study).
- Phase-shifting transformers.
- Voltage control by inverters (volt-var).
- The cost of an OLTC as a separate item.
- Taps on the grid operator's side of the PCC.

**Estimated size.** Large: about 450 lines of engine, 150 of service and panel, and 550 of tests. One to two sessions, Opus for the engine and the MILP.

### Order and dependencies

| step | what | depends on | notes |
|---|---|---|---|
| 0 | C12 (#100) merged | none | approved. Every increment below branches from master after it, because all three touch `campus_invest.py` and `campus_milp.py`, which #100's job runs |
| 1 | **C13** | step 0 | defines the transformer fields (`vector_group`, `tap`) that C15 reads, and the `action` column that C14 extends. Its engine PR does not touch the shipped YAML |
| 2 | **C14** | C13's table columns; the IC session's answer on double counting and build year (open question 8), asked when C13 starts | the engine half (purchases, cost table) can land before the IC answer; the producer change waits for it |
| 3 | **C15** | C13 (tap block, mixed groups tapped together) | independent of C14 in code (C15 touches the re-check, C14 the costing), so steps 2 and 3 can run in parallel after C13, with C15 rebasing over C14's table columns |
| data | vector groups and tap blocks in `campus_assets.yaml` | the reference-costs branch merged | one data commit, new fields only, values tagged `assumed` with the notes above. Until then the shipped library has no tap data, so C15 changes nothing on it, and C13 treats every vector group as unknown |

The reference-costs branch changes the costs, and so possibly which candidates win. It changes no code path here. The expected results in the C13–C15 tests are computed from test libraries, never from the shipped one, so the branches do not interfere.

**Gates per increment**, as before:
- tests first;
- the targeted files without the lock;
- the full `tests/gridspine` and the backend suite under the shared lock;
- `tsc` and `vitest` for panel changes;
- mutations by hand, with a table of caught and survived, and tests for survivors;
- a browser run for C14's and C15's panel changes.

### Open questions for the owner (multiple choice; recommended first)

**Answered (2026-10-12): the owner accepted the recommended option, (a), on all 16 questions.** The plan below is written to those options and stands as decided.

1. **C13, impedance tolerance for adding a unit in parallel.**
   - **(a) ±10 % of each existing unit's uk (recommended; assumed).**
   - (b) ±7.5 %, the vendor note's figure.
   - (c) No filter: the AC load sharing alone decides.
2. **C13, a vector group not given on either side** (true of the whole shipped library until the data commit).
   - **(a) Offer the candidate, flagged "vector group assumed compatible" on its row and in the report (recommended).**
   - (b) Offer it only when both groups are given and match.
3. **C13, a limit on the rating ratio of parallel units.**
   - **(a) None; each unit is judged against its own rating in AC (recommended).**
   - (b) At most 2:1 (assumed).
   - (c) At most 3:1 (assumed).
4. **C13, when units added to (or a replacement of) a fully existing group are bought.**
   - **(a) In the first period in which the existing units alone fail (recommended).**
   - (b) In the first period, as today.
5. **C14, an existing asset with no commissioning year.**
   - **(a) Assumed at mid-life at the first period, flagged `assumed` (recommended).**
   - (b) Assumed new at the first period.
   - (c) Never replaced, flagged.
6. **C14, the study horizon.**
   - **(a) The project's investment-period weightings; a single-period project spans one year, so only overdue assets are replaced there and the IC's rule covers its longer horizon (recommended).**
   - (b) A study setting, `horizon_years`.
   - (c) The IC's analysis horizon, read from the project's finance inputs.
7. **C14, the ranking of candidates.**
   - **(a) Unchanged (annualised cost). Replacements are reported, not ranked, and keeping an old unit stays zero in the ranking, ledgered (recommended for C14).**
   - (b) Rank by present value over the horizon, in C8 and the MILP (a larger, separate increment).
8. **C14, the IC contract.**
   - **(a) One entry per purchase with `meta.purchase`, `meta.lineage` and `meta.replaces`. The IC does not generate replacements for `source == "campus_study"` entries and accepts their `build_year` after COD (recommended; needs the IC session).**
   - (b) First purchases only, leaving replacements to the IC's `part_lifetimes` (contradicts "one entry per purchase").
   - (c) Every purchase, with the IC running the `fixed` rule for campus assets.
9. **C14, existing assets that are not a need** (an adequate existing cable or transformer with a `library_id`).
   - **(a) Listed as zero-cost `keep` rows so that their end-of-life replacement is costed (recommended).**
   - (b) Only items already in the investment table are replaced.
10. **C14, an existing asset already past its lifetime at the first period.**
    - **(a) Re-bought in the first period, flagged `overdue` (recommended).**
    - (b) Kept as is, flagged.
11. **C15, taps under N-1.**
    - **(a) Set per hour and per case: the state after the tap changers act, with the Q dispatch held (recommended, as the approval reads).**
    - (b) Set per hour and held in every case, like the Q dispatch.
12. **C15, who moves the PCC transformer's taps.**
    - **(a) A study setting, `pcc_tap_control`, defaulting to the campus (recommended).**
    - (b) Always the grid operator's (never moved).
    - (c) Always the campus's.
13. **C15, the objective for taps.**
    - **(a) Voltages in band; among equals, the fewest steps from neutral; no cost (recommended).**
    - (b) A money penalty per step (there is no data for one).
    - (c) Least losses within the band.
14. **C15, off-circuit taps (20/0.4 kV).**
    - **(a) Fixed at the file's position, never moved (recommended).**
    - (b) One setting chosen for the whole study (a later increment).
15. **C15, taps in the MILP.**
    - **(a) Set by the same rule inside every AC evaluation, fixed in the linearisation (recommended).**
    - (b) Integer tap variables, linearised.
16. **The shipped library's vector groups and tap data.**
    - **(a) One data commit after the reference-costs branch merges (recommended).**
    - (b) Inside the C13 and C15 PRs, rebasing over that branch.
