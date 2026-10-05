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
