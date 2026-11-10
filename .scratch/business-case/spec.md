<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Business case on every template: one engine, user-entered line items, plant presets

Status: ready-for-agent once the design PR merges (GK B plan GO G-C1..G-C8, GK A plan GO LO-1..10, owners BC-ALL-1 and BCA-SCOPE-1, 2026-11-10)
Owner request: 2026-11-10 (coordinator brief), owner decision **BC-ALL-1**
Research: [research.md](research.md)
Builds on: [the DC business case](../dc-business-case/spec.md) (B1–B8, DCBC-SCOPE-1) and its GK conditions BC1–BC10 ([DC 01](../dc-business-case/issues/01-design-plan.md) ## Comments)
Reviewers: GK B (finance engine, harness, app; lead), GK A (linked model outputs, template networks, IC/adequacy reads), UX (slices 09–10)

## 1. The ask

The owner, 2026-11-10: "When you design the DC business case, let's also add the
extended functionalities to all the other templates. And most importantly, let's
design how we can have a version of the business case assessment for the generic
template … where the user can input a lot of things, by themselves. With given
selections or as typed, just by typing in certain columns … But also have it for
other templates, hydrogen, mining, etc., based on the requirements of the
corresponding plant."

Owner decision **BC-ALL-1** (card answer "Generalise now (Recommended)"):

- one shared business-case engine;
- the generic site template `eh_site` gets user-entered revenue and cost **line
  items**, picked from lists or typed into table columns;
- every other template gets a **preset** for its plant, on the same engine:
  data centre (GPU-hour, kW-month lease), H2 hub (€/kg), ammonia (€/t), mining
  concentrator (concentrate €/t), IPP and IPP hybrid (PPA €/MWh), microgrid
  (tariff);
- DC slice 02 (inputs model) is paused and becomes the generic inputs model;
  DC slice 03 (IT energy series) keeps building, DC-only.

The DC design stays binding where it is not DC-specific: B1–B8 (two bases, one
engine, never summed; standalone `counterfactual=()`; P&L from the per-stream
ledger), DCBC-SCOPE-1 (separate view, prices user-entered with no default, cost
defaults researched and marked assumed) and BC1–BC10.

## 2. What exists (verified on `95cdf7f6`)

The DC spec §2 lists the engine pieces (`run_case`, `STREAM_CLASS`, standalone
mode, depreciation, debt, `revenue_by_stream`). What this generalisation adds:

| Fact | Where | Consequence |
|---|---|---|
| Every template except the IPPs binds `single_owner` value flows: one site party, no contracts, no sale line | `services/commercial/value_flow_templates.py:204`; `eh_site.py:650`, `eh_templates.py:1502,1520`, `eh_staged_hubs.py:263,370`, `eh_ammonia.py` `_commercial_and_finance`, `eh_concentrator.py` `_commercial_and_finance` | Today's IC is cost avoidance only. No template sells its product; the business case adds the sale. |
| H2 hub and ammonia carry a **reference purchase** (grey H2 2.0 €/kg at LHV 33.33 kWh/kg; grey NH3 0.501 €/kg) | `eh_staged_hubs.py` `GREY_H2`; `eh_ammonia.py` `GREY_NH3`; read on the counterfactual side, `services/results/finance_case.py:1128` | It is the "site today" purchase. With `counterfactual=()` it vanishes, so it cannot double a sale line (G4). |
| **Product buses** count in t or MWh with `mwh_per_t`, `product_value` | `services/process_products.py:38-104` (`is_product_bus`, `bus_unit`, `t_per_unit`) | The served product is a model output a sale line can read (§5 L1). |
| Shortfall slacks on product buses are priced as `lost_production` (class `fuel`) at the bus value, never VoLL | `services/adequacy/process_loss.py`, `case.py:554` `ProductShortfall` | A sale line on served product plus `lost_production` would charge a shortfall twice (G3). |
| Ammonia delivery `nh3_delivery` in MW_NH3 = t/d ÷ 24 × 5.166 | `eh_ammonia.py` (`MWH_PER_T`) | Sale €/t reads served MWh ÷ 5.166 (LO-1). |
| Concentrate ships in vessel pulses from `concentrate_shed` via the `shipments` load (t/h) | `eh_concentrator.py` | Sale reads shipped t, not mill output (LO-2). |
| `h2_offtake` (MW_H2, LHV) on bus `h2`; electrolyser and fuel cell on the same bus | `eh_templates.py:779-800`, `eh_staged_hubs.py:35` | Sale reads `h2_offtake` served, never electrolyser p1 (L2). |
| IPP and IPP hybrid already price two pay-as-produced financial PPAs (`ppa_wind`, `ppa_pv`), merchant export and `solve_ppa` | `eh_ipp.py:243-297`, `eh_ipp_hybrid/cases.json` | The PPA is already a ledger stream; the preset never re-enters it (G2). |
| Microgrid serves `residential` and `hospital_load`, no tariff, no export | `eh_templates.py:926-927, 1520` | The tariff line reads served load (L4). |
| The paused DC inputs model on #1320 (`claude/dc-bc-02-inputs-model` @ `c04a2c76`): `BusinessCaseRefused` (kind + field path), `require_runnable`, `CostDefault` + `assumed` provenance + `with_cost_defaults`, defaults pack 2026-11-10 with seven `dc-business-case.*` rows | `models/business_case.py`, `tests/test_business_case_inputs.py` on that branch | Slice 02 reuses it (§10). |

## 3. The bases and the rules

B1–B8 of the DC spec hold for every template. In short: the business case is a
second `run_case` on `dataclasses.replace(ic_case, counterfactual=(), …)` with
its own hash domain; the IC never sees a business-case line and the two are never
added; revenue is post-solve, never in the LP, never a value of lost load; the
P&L is built from the per-stream operating ledger `op`, never from `op_inc`
(BC4, binding on every template).

The generalisation adds these rules (GK B plan GO, conditions G-C1..G-C8; GK A
LO-1..LO-10, both 2026-11-10):

- **G1 Compile.** A revenue or opex line becomes one `TemplateLine` per period
  template: stream from its category (G-C1 table below), `key` = the line name,
  `esc_class` from its escalation column, `money_year` from its money-year column
  (G-C1), amount = quantity × price, signed from the owner. A capex line becomes an
  `AssetFinance` with a `DepreciationClass` from its method and life; its refresh
  years are replacements. One engine run, own hash domain (BC1).
- **G2 Energy-model streams are the ledger's (G-C5).** The bill, export, PPA and
  CfD settlement, `eaas_fee`, ancillary, and the energy assets' `fom`/`vom` enter
  from the IC case unchanged (B1). The view shows them as locked rows "from the
  energy model" beside the line table. A line whose quantity source is **linked**
  to an output a ledger stream already prices (export MWh with an export price or a
  PPA, PPA/CfD settlement, the bill streams) is refused `double_count:<stream>`,
  never warned (GK B (b)). A product bus is not a G2 case: the IC values it only
  through `lost_production`, which G3 drops instead (GK A F1). A **typed** line cannot be checked; the locked rows are what
  stop a user typing the PPA in twice, and the line table's help says so.
- **G3 Lost production.** When a revenue line reads a product bus's served
  quantity, that bus's `lost_production` lines are dropped from the business-case
  case: served already excludes the shortfall, so both would charge it twice. With
  no line on the bus, `lost_production` stays (the contracted-delivery penalty).
  Pin test (slice 06). GK A aligned LO-4 to this (2026-11-10): the shortfall's
  loss counts exactly once, as lower served revenue; the line is not refused.
- **G4 Reference purchases** are counterfactual-side and vanish with
  `counterfactual=()`; a pin that no reference-purchase value reaches the business
  case (slice 06).
- **G5 Units.** The line model's unit is an enum; price unit = currency per
  quantity unit, with no conversion in the model (slice 02). Conversions (H2 LHV
  from `models.commercial`'s `lhv_kwh_per_kg` or the line's stated LHV; a bus's
  `mwh_per_t`; `eh_ammonia.MWH_PER_T`) live only in the linked-output catalogue,
  beside the outputs they convert (slice 04, LO-1). No new literals.
- **G6 Shares.** A `share_of` line reads only non-share lines (one level); a cycle
  or a share of a share is refused `share_cycle:<line>`.
- **G7 What a preset owns (G-C3, G-C4).** A preset revenue row declares
  `requires` (capex categories it needs: `capex_missing:<category>`), `excludes`
  (capex categories it must not carry: `capex_not_owned:<category>`) and
  `price_includes_energy` (a pass-through line is then refused
  `pass_through_price_includes_energy`). One kind per refusal, no aliases (GK B
  ruling 2026-11-10): the DC presets use `capex_not_owned` with the category in
  the field path and `pass_through_price_includes_energy`; only
  `it_capex_missing` and `facility_capex_missing` keep their DC names, because the
  DC spec names them. Every kind that reaches a tool is registered once in its
  slice's error-kind fragment. Capex is priced once.
- **G8 Break-even** on any named revenue line, on the BC8 rule: the solve brackets
  its root; no root is `break_even_not_bracketed`, never an extrapolation; DSCR
  reads n/a without debt.
- **G9 Typed refusals** name the line and the column:
  `business_case_refused:<kind>` with `.field = "lines[<i>].<column>"`, never 0
  or a default (#1320 pattern).
- **G-C2 No per-line tax switch in v1.** Revenue is taxable, opex deductible,
  capex depreciated through its class, as the engine does today. A per-line
  exemption would be an engine change in its own GK B-gated slice with BC5's tie.
- **G-C6 Hash.** The business-case hash covers `preset_id@version`, the lines,
  `receivable_days` and the linked outputs' bound solve; the IC hash and every
  other digest stay byte-identical (BC1 pin).
- **G-C7** BC2 (gross and net of any pass-through), BC3 (working capital only in
  the cash bridge), BC4, BC5 (one tax, tie with a mutant), BC8 and BC9 (pack rows
  appended, previous version byte-equal) bind on slices 05 and 07 for every
  template.

**Category → stream (G-C1, GK B (a)).** The list is closed; each category is one
`STREAM_CLASS` row; a test that every category has its row and a pin that the
IC's stream set and results do not change.

| Kind | Category | Stream | Class | What it is |
|---|---|---|---|---|
| revenue | `product_sale` | `product_revenue` | contract | the plant's product: H2, NH3, concentrate, any typed product |
| revenue | `by_product` | `byproduct_revenue` | contract | O2, or any typed by-product |
| revenue | `service_fee` | `service_revenue` | contract | compute-hours, tolling, energy-as-a-service fee, remote hands |
| revenue | `lease` | `lease_revenue` | contract | space or capacity rented per kW-month or per unit |
| revenue | `capacity_payment` | `capacity_revenue` | contract | capacity-market or availability payment per kW or MW-year |
| revenue | `tariff` | `tariff_revenue` | contract | **energy the project sells to its own customers** per kWh (microgrid, campus); never the project's own bill |
| revenue | `cost_recovery` | `power_passthrough` | contract | a share of the energy bill re-billed to a tenant (DC R3) |
| revenue | `incentive` | `premium_revenue` | contract | a fixed production premium per unit (e.g. a hydrogen-bank €/kg premium), a contract line, not the engine's tax `incentive` |
| revenue | `other` | `other_revenue` | contract | anything typed |
| opex | `staff`, `maintenance`, `insurance`, `property_tax`, `feedstock`, `consumables`, `network`, `land`, `sga`, `other` | `bc_<category>` | opex | non-energy operating cost |
| capex | `facility`, `equipment`, `it_hardware`, `other` (no `land`: it does not depreciate, §11) | (none: an asset) | capex | depreciated by its class |

The DC names (`compute_revenue`, `colocation_revenue`) are the DC presets'
categories `service_fee` and `lease`; slice 05 decides with GK B whether the DC
streams keep their own rows, the table above being the default.

## 4. The line item

One row of a table. **S** = picked from a list; **T** = typed. Every number is
finite (`models.schemas.Finite`, #1320 F1), `extra=forbid`.

| Column | Input | Values | Rule |
|---|---|---|---|
| `name` | T | 1–80 chars, unique in the case | `name_duplicate` |
| `kind` | S | revenue, opex, capex | — |
| `category` | S | per kind, §3 table | `category_not_in_kind` |
| `unit` | S | `MWh`, `MWh_H2`, `kg`, `t`, `GPU_h`, `kW_month`, `kW_year`, `MW_year`, `unit`, `unit_month`, `lump`, `share` | `unit_not_allowed` for the source (below) |
| `quantity_source` | S | `typed`, `linked:<output id>` (§5, only the outputs this project has), `share_of:<kind>[/<category>]` | `linked_output_missing:<id>`, `share_cycle` |
| `quantity` | T | a number (typed); none when linked; a share in [0, 1] for `share_of` | `quantity_missing`, `share_out_of_range` |
| `quantity_by_year` | T | optional per-year override grid, year → number (BCA-SCOPE-1 Q2) | years inside start..end, `year_out_of_range`; every cell `Finite` |
| `price` | T | currency per `unit`, in `money_year` money; revenue: none = **not established** (DCBC Q4), never a default | `price_not_established` blocks the run (BC7) |
| `price_by_year` | T | optional per-year override grid (BCA-SCOPE-1 Q2): the cell is that year's price in the line's `money_year` money, and the row's escalation does not apply to that year (GK B) | as `quantity_by_year` |
| `money_year` | T | default the case's base year; a preset default carries its source's year (G-C1) | 1990..base year + 5 |
| `escalation` | S | one of `ESCALATION_CLASSES` (opex, fuel, tariff, ppa, export, capex), or `contract` | — |
| `indexation` | T | % per year, only with `contract` | `indexation_missing` |
| `start_year`, `end_year` | T | default COD..analysis end | `end_before_start`, `outside_horizon` |
| `depreciation` | S | capex only: `straight_line` (default), `declining_balance` | — |
| `life_years` | T | capex only, required, ≥ 1 | `life_missing` |
| `rate` | T | capex `declining_balance` only, (0, 1] | `rate_missing` |
| `refresh_years` | T | capex only, optional; replaces at that interval while `end_year` allows | ≥ 1 |
| `note` | T | free text, the user's source | — |
| `provenance` | — | `stated` (user) or `assumed` (preset default, with `CostDefault`: key, source, source year, money year, pack stamp, range) | written by the system, never typed |
| `preset_rule` | — | the preset's `requires`, `excludes`, `price_includes_energy`; read-only | G7 |

**Units by source.** A typed line takes any unit except `share`. A linked line
takes the units its output offers (§5: a product bus in t offers `t`; a bus in MWh
with `mwh_per_t` offers `MWh` and `t`; H2 offers `MWh_H2` and `kg`). A `share_of`
line takes `share`. `lump` is capex only (a sum per `start_year`, or per refresh).

**Refusal vs not established.** A run is refused (typed, G9) for a structural
fault (unit, cycle, missing life, capex the preset excludes, double count). A
missing revenue price is `price_not_established`: the inputs save, the view shows
the line as not established, and the run refuses until it is given (BC7). A
missing cost value with a pack default takes the default as `assumed`; one with
no default (no source, GK B ruling) is `cost_not_established`.

**Case-level fields.** `preset_id@version` (none for a blank table),
`receivable_days` (cash bridge only, BC3), and the lines.

**A preset is just pre-filled lines.** It is a versioned data file per template
(`project_templates/business_case_presets/<template>.json`): rows with every
column the user would type, revenue prices empty, cost values from the defaults
pack as `assumed`, plus `preset_rule`. Applying it copies the rows into the case;
the user then edits, deletes or adds rows as on `eh_site`. Nothing about a preset
is special at run time except its `preset_rule`.

## 5. Linked model outputs (slice 04, GK A)

A versioned catalogue `linked_outputs(n, result)` → per-year series with unit,
one id per output; the ids are a stored contract that lines link to, versioned
with a test that every reader accepts each version (#1132 F1). Every output binds
to the run's own solve (project, revision); stale or unsolved reads
`not_evaluated`, never 0 (LO-7). Post-solve only: nothing enters the LP, the IC,
the IC hash or adequacy facts (LO-9). Tests use non-unit snapshot weightings and
a multi-period network (LO-8).

| Id | Output | Read | Units |
|---|---|---|---|
| L1 `served_product:<bus>` | product served on a product bus (`is_product_bus`) | Σ per year (Load p_set − involuntary/product-shortfall slack p) × `snapshot_weightings.generators` ÷ the period's years, with ProductShortfall's helper (LO-2, GK A answers 1–2); never clipped per snapshot (a slack filling a store can exceed the hour's load); store drawdown counts as served | t; MWh and t on a bus with `mwh_per_t` |
| L2 `served_h2:<load>` | H2 delivered | `h2_offtake` as L1; never electrolyser p1 | MWh_H2, kg (LHV, LO-1) |
| L2b `shipped:<load>` | concentrate shipped | the `shipments` load as L1 | t |
| L3 `derived_o2:<link>` | O2 co-produced | electrolyser H2 output × 7.94 kg/kg (physical constant), `derived`, quantity only | t |
| L4 `served_load:<load>` | electric load served | (p_set − involuntary slack on its bus, pro rata by p_set(t) across the bus's loads) per year (LO-3); unserved earns 0, never priced | MWh |
| L5 `export:<link>` | energy exported | the grid side of the export link, after link losses, sign stated (LO-5); refused `double_count` where a ledger stream prices it | MWh |
| L6 `capacity:<asset>` | capacity in service per year | `get_active_assets(period)` (`adequacy/activity.py`); extendable reads `p_nom_opt`; unsolved `not_evaluated` (LO-6) | MW, kW |
| L7 `it_energy`, `contracted_it_kw` | DC served IT energy, contracted kW | DC 03 as merged (LO-10); no second reader | MWh, GPU_h via kW per GPU in the preset row, kW_month |

Recoverable heat is out of v1 (GK A answer 3: it needs a share and an offtaker,
and templates that model heat would count it twice).

GK A standing points on every template: revenue reads the stated series or a
load that **is** the billed quantity (L1–L4 are loads the plant contracts to
deliver: the offtake, the shipment, the served customer); unserved is the
involuntary slack only, a quantity, never priced; the billed series has an
identity test against the built network; no template's IC basis changes.

## 6. The presets

Each preset row lists category, unit, quantity source and the default status.
"—" = user-entered, no default (DCBC Q4). Defaults ship only with a source and a
range, marked assumed; a cost with no source gets no default (GK B ruling).
Sources are in [research.md](research.md).

### 6.1 Generic site `eh_site` (blank table, offered rows)

No preset rows are forced: the table opens empty with an "add line" picker that
offers the common rows below as one-click starts. Everything is editable.

| Offered row | Kind / category | Unit | Quantity | Default |
|---|---|---|---|---|
| Product sales | revenue / product_sale | t, unit | typed (the site's output) | — |
| Service or tolling fee | revenue / service_fee | unit_month, lump | typed | — |
| Flexibility / DR availability | revenue / capacity_payment | MW_year | typed or `capacity:<asset>` | — (DR settlement already in the ledger is locked, G2) |
| Staff | opex / staff | unit (FTE) | typed | — |
| Maintenance (non-energy) | opex / maintenance | share | `share_of:capex` | from pack, assumed |
| Insurance | opex / insurance | share | `share_of:capex` | from pack, assumed |
| Property tax | opex / property_tax | share | `share_of:capex` | from pack, assumed |
| SG&A | opex / sga | share | `share_of:revenue` | — |
| Building or process plant | capex / facility | lump | typed | — |
| Land lease | opex / land | unit_month, lump | typed | — |

### 6.2 Data centre (`eh_datacenter`, `eh_datacenter_staged`)

The DC spec §4–§5 and DCBC-SCOPE-1 are the preset, expressed as rows:

| Row | Kind / category | Unit | Quantity | Rule |
|---|---|---|---|---|
| R1 GPU-hour (reserved, on-demand) | revenue / service_fee | GPU_h | `it_energy` × 1000 ÷ kW per GPU × sold share (DC GK A note) | requires it_hardware + facility (`it_capex_missing`, `facility_capex_missing`); price_includes_energy |
| R2 Colocation lease | revenue / lease | kW_month | `contracted_it_kw` × lease-up | requires facility; excludes it_hardware (`capex_not_owned`, field `…category=it_hardware`) |
| R3 Power pass-through | revenue / cost_recovery | share | `share_of` the bill streams × (1 + margin) | refused under R1, `pass_through_price_includes_energy` (G-C4) |
| Facility, M&E | capex / facility | lump per MW IT | typed per COD phase | default from pack 2026-11-10, assumed (2025 money) |
| IT hardware | capex / it_hardware | lump per GPU or MW IT | typed | life 5 (4–6), refresh |
| Staff, network | opex / staff, network | MW_year | `contracted_it_kw` | pack, assumed |
| Maintenance, insurance, property tax | opex | share | `share_of:capex` | pack, assumed |

### 6.3 H2 hub (`eh_h2_hub`, `eh_h2_hub_staged`)

| Row | Kind / category | Unit | Quantity | Default / note |
|---|---|---|---|---|
| H2 offtake | revenue / product_sale | kg | `served_h2:h2_offtake` | — (hint per BCA-SCOPE-1 Q1: no public offtake benchmark; research §1) |
| Production premium | revenue / incentive | kg | `served_h2:h2_offtake`, tenor ≤ 10 years | — (hint: EU Hydrogen Bank pilot cleared €0.37–0.48/kg, 2024) |
| Oxygen sales (off by default) | revenue / by_product | t | `derived_o2:electrolyser` | — (rarely sold; needs an on-site offtaker) |
| Stack replacement | capex / equipment | share of electrolyser capex | refresh by operating hours → years | assumed, DOE 2022: 15 % of direct capex per replacement at 40,000 h |
| Non-energy O&M | opex / maintenance | share | `share_of:capex` | assumed, 3 %/yr (IRENA-linked 2022 case; range 2–3 %) |
| Water | opex / consumables | kg | `served_h2` × 20–60 L/kg | no sourced price → no default |
| Staff, insurance | opex | — | typed / share | — / pack |

The grey-H2 reference purchase stays the IC's counterfactual and is absent from
the business case (G4).

### 6.4 Ammonia (`eh_ammonia_staged`)

| Row | Kind / category | Unit | Quantity | Default / note |
|---|---|---|---|---|
| NH3 sales | revenue / product_sale | t | `served_product:nh3` (MWh ÷ 5.166) | — (hint: H2Global pilot lot €811/t ex-delivery, €1,000/t delivered, 2024; grey Tampa $392–650/t, 2025) |
| Oxygen (off) | revenue / by_product | t | `derived_o2:<electrolyser>` | — |
| Stack replacement, electrolyser O&M | as 6.3 | | | as 6.3 |
| ASU and synthesis-loop O&M | opex / maintenance | share | `share_of:capex/equipment` | no primary source → no default |
| Storage and shipping | opex / other | t | `served_product:nh3` | — (only if the price is delivered) |

`lost_production` on `nh3` drops out when the NH3 sales line reads the bus (G3).

### 6.5 Mining concentrator (`eh_concentrator_staged`)

| Row | Kind / category | Unit | Quantity | Default / note |
|---|---|---|---|---|
| Concentrate sales (net) | revenue / product_sale | t | `shipped:shipments` | — (BCA-SCOPE-1 Q3; the row's note: grade × payable × metal price − TC/RC − freight; 2026 copper TC/RC benchmark $0/dmt, 0 ¢/lb) |
| Process plant cost | opex / consumables | t (milled) | typed (mill throughput) | no sourced split → no default; research gives US$5.6–11.3/t milled (2025 reports) as a hint |
| Freight | opex / other | t | `shipped:shipments` | — |
| Staff, maintenance, insurance | opex | — | typed / share | — / pack |

`lost_production` on `concentrate` drops out when the sales line reads the
shipments (G3).

### 6.6 IPP and IPP hybrid (`eh_ipp`, `eh_ipp_hybrid`)

The PPAs (`ppa_wind`, `ppa_pv`), the merchant export and `solve_ppa` are ledger
streams: locked rows, never a line (G2). The preset adds:

| Row | Kind / category | Unit | Quantity | Default / note |
|---|---|---|---|---|
| Capacity payment (BESS, firm capacity) | revenue / capacity_payment | kW_year | `capacity:bess` × de-rating (typed) | — (hint: GB T-4 2028/29 £60/kW-yr; IT 2027 €47k/MW-yr) |
| Guarantees of origin | revenue / other | MWh | typed (generation; a linked export line is refused, G2) | — (hint €0.3–2/MWh, 2024–25) |
| Land lease | opex / land | MW_year | `capacity:<asset>` | no separate source → none |
| Insurance | opex / insurance | share | `share_of:capex` | pack |

BESS ancillary revenue is the ledger's `ancillary` stream where the template
models it; the preset does not add a typed reserve line.

### 6.7 Microgrid (`eh_microgrid`, `eh_microgrid_staged`)

| Row | Kind / category | Unit | Quantity | Default / note |
|---|---|---|---|---|
| Energy tariff, residential | revenue / tariff | MWh | `served_load:residential` | — |
| Energy tariff, hospital | revenue / tariff | MWh | `served_load:hospital_load` | — |
| Availability / EaaS fee (off) | revenue / service_fee | unit_month | typed | — |
| Staff, maintenance, insurance | opex | — | typed / share | — / pack |

Unserved load earns no tariff; resilience is not valued (no VoLL, B4).

## 7. Outputs and workflow

As DC spec §6–§7, for every template: the P&L by year from `op` by stream (BC4),
the KPIs (EBITDA margin gross and net of pass-through, revenue per unit of the
plant's capacity, NPV project and equity, IRR, payback, DSCR), break-even on a
chosen revenue line (G8), not-established never zero, a "Business case"
workbook sheet (RW-KPI-1).

- **Results, Investment tab**: one "Business case" view beside the investment
  case (DCBC Q3), for every template: the line-item table (slice 09) with the
  locked energy-model rows, then the P&L and KPIs (slice 10). Labelled by basis
  ("versus the site today" / "the project on its own"); never one total.
- **Chat**: `run_business_case`, `get_business_case` (route
  `POST /api/results/business_case`), plus `set_business_case_lines` for the
  assistant to fill rows from the user's words (BC10 checks).
- **Guided**: the DC question generalised, "Is this plant worth building?"
  (`plant_business_case`): applies the template's preset, asks each empty revenue
  price with `ask_user` (never invented), offers the assumed cost defaults with
  source and year, runs IC then business case, gives the verdict and the
  break-even price.

## 8. How the DC issues fold in

| DC issue | Becomes | Why |
|---|---|---|
| 01 design | done (#1316) | — |
| 02 inputs model (paused, #1320) | **02** generic line model on the #1320 branch; its DC classes become rows of **07**'s DC preset | the refusal, provenance and defaults machinery is generic; GpuHour, Colocation, ItHardware, Facility-per-MW-IT are presets |
| 03 IT energy series | **stays DC 03**, unchanged (GK A BC3-1..8); it becomes the catalogue's L7 | DC-only physics |
| 04 engine and P&L | **05** (generic), DC refusals carried as preset rules | BC1–BC8 are generic |
| 05 route, tool, workbook | **08** | generic |
| 06 view | **09** line-item editor + **10** P&L view | the editor is new and generic; split to stay M |
| 07 guided question | **14** | generic question, DC is one preset |

The DC issue files 02, 04–07 get a `Status: wontfix (superseded by
business-case NN; BC-ALL-1)` line in this PR; DC 03 is untouched.

## 9. Owner choices

BC-ALL-1 sets the scope. Owner decision **BCA-SCOPE-1** (2026-11-10 04:27Z, via
the coordinator): the recommended answer on all four.

- **Q1 Revenue price hints: A.** Preset revenue prices start empty, with a
  researched public reference range as hint text beside the cell (source and
  year), never filled in.
- **Q2 Per-year values: A.** One value per row + escalation + start/end year; a
  row can expand to a per-year grid (paste from a spreadsheet) that overrides it.
- **Q3 Mining revenue: A.** One net €/t concentrate line the user types, with the
  preset note on how it is built (grade × payable × metal price − TC/RC − freight).
  No NSR calculator.
- **Q4 Order: A.** Engine + generic `eh_site` line items first, then the DC presets
  (finishes DCBC), then H2 hub + ammonia, then IPP / IPP hybrid + microgrid, then
  mining: the §10 order.

## 10. Slices (one PR each, PR-SIZE-1)

| # | Slice | Size | Reviewer | Blocked by |
|---|---|---|---|---|
| 01 | [Design plan](issues/01-design-plan.md) (this spec) | S | GK B, GK A | — |
| 02 | [Line-item model and validation](issues/02-line-item-model.md) (on #1320's branch) | M | GK B | 01 |
| — | [DC 03 IT energy series](../dc-business-case/issues/03-it-energy-series.md) (unchanged) | M | GK A | DC 01 |
| 04 | [Linked model outputs and unit conversions](issues/04-linked-outputs.md) | M | GK A | 02; DC 03 for L7 |
| 05 | [Engine, P&L and KPIs](issues/05-engine-and-pnl.md) | M | GK B | 02, 04 |
| 06 | [Ledger guards: double count, lost production, reference purchases](issues/06-ledger-guards.md) | S | GK B, GK A | 05 |
| 07 | [Preset mechanism, generic offered rows, DC presets](issues/07-presets-and-dc.md) | M | GK B | 05 |
| 08 | [Route, chat tools and workbook sheet](issues/08-route-tool-workbook.md) | M | GK B | 05 |
| 09 | [Line-item table editor](issues/09-line-item-editor.md) | M | UX, GK B | 07, 08 |
| 10 | [P&L and KPI view](issues/10-pnl-view.md) | M | UX, GK B | 08 |
| 11 | [H2 hub and ammonia presets](issues/11-presets-h2-ammonia.md) | S | GK B, GK A | 06, 07 |
| 12 | [IPP, IPP hybrid and microgrid presets](issues/12-presets-ipp-microgrid.md) | S | GK B, GK A | 06, 07 |
| 13 | [Mining concentrator preset](issues/13-preset-concentrator.md) | S | GK B, GK A | 06, 07 |
| 14 | [Guided question: is this plant worth building?](issues/14-guided-question.md) | S | GK B | 08, 11–13 |

Slices 02, 05 and 07 bring their TDD plan to GK B before building (G-C8); 04
sends its PR head to GK A. If 05 grows beyond M, the G2–G4 rules are already
their own slice (06).

## 11. Out of scope (v1)

- A per-line tax switch (G-C2), non-depreciating capex (land as an asset).
- Recoverable heat as a linked output (GK A).
- Pricing unserved energy or downtime (B4, owner 2026-10-15).
- Forecasting any product or metal price; prices are user-entered.
- An NSR calculator for concentrate (BCA-SCOPE-1 Q3).
- Changing any template's IC: basis, numbers and hash stay as they are (BC1).
