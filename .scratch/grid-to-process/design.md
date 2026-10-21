# Integrated grid-to-process planning for industrial clients

Status: needs-triage (design, not yet approved; docs only, no code)

Commissioned 2026-10-16 through the coordinator. Base: master `cb04473d`.
Owner's words: "Help me understand the gaps that need to be closed for integrated grid to process
planning for industrial clients, for production, and see if we can design an example for it."
The owner picked two examples. The coordinator set the order: **A, H2 hub to green ammonia**,
then **B, mining concentrator**.

The decisions the owner has to make are collected in [§8](#8-decisions-for-the-owner). Each one
has a recommendation. Nothing in this design is built until the owner approves it.

## 0. Summary

- **The engine can already express a process chain.** PyPSA 1.1.2 (the pinned version) has
  committable Links (`committable`, `min_up_time`, `min_down_time`, `start_up_cost`,
  `stand_by_cost`, ramp limits), multi-port Links (`bus2..`, `efficiency2..`) and Stores. A
  product bus, a product Store, a delivery Load and a shortfall slack priced per tonne complete
  the chain.
- **The gaps are in the app and in the coupling.**
  - The asset schema and the forms have no process unit.
  - Results and economics read one output port only.
  - The solve path handles committable Generators, not committable Links.
  - Interruption cost is priced per MWh, not per tonne.
  - A representative week cannot value an inventory that buffers for more than a week.
  - Grid results reach the process only through the LP's connection rows.
- **Two engine traps the design avoids** (verified in PyPSA 1.1.2, §2.3):
  - start-up cost is not multiplied by the snapshot or period weighting;
  - a committable *extendable* unit is a big-M bilinear model.

  So process units are fixed-size and committed with linearised UC. Restarts are priced as lost
  tonnes, not as `start_up_cost`.
- **Example A** reuses the staged H2 hub and upstream PyPSA-Eur's Haber-Bosch data. It fits the
  representative week if delivery is continuous. It is the tracer bullet for P1 and P2.
- **Example B** needs P3. A concentrate stockpile shipped every few weeks cannot be valued
  inside one cyclic week. Owner decision D4: a full hourly chronological year per period for
  planning, with the solve time and memory measured before B is built.

## 1. Concept and glossary

### 1.1 The idea

**Grid-to-process** plans the site's electrical system and its production as one problem. The
objective is the client's margin (product value delivered, minus energy, grid and asset cost).
It is not the cheapest energy bill for a fixed load.

What the client gets that a fixed load cannot show:

- what inventory and flexibility are worth in connection capacity;
- what a grid event costs in tonnes;
- which production plan the grid can actually carry.

### 1.2 Proposed CONTEXT.md terms (`pypsa-gui/CONTEXT.md`)

Each term will cite its code location once ticket 01 builds it. These are proposals for the
`domain-modeling` pass. None is in the glossary today, and no existing term covers them.

**Process unit**:
One production step that turns inputs into a product at a rate (t/h), for example a
Haber-Bosch loop, a crusher or a grinding mill. It is a PyPSA Link with `eh_process` set:
bus0 is the main input, `bus2..` are the other inputs (negative efficiency), and bus1 is the
product. Its electricity draw follows from throughput × specific energy (kWh/t). It may be
committable (on/off with a minimum load).
_Avoid_: load (a Load is fixed demand; a process unit decides its rate), machine, plant (a plant
is several process units).

**Product**:
A material a process unit makes or uses, carried on its own bus: NH3, ore, concentrate.
Energy products (H2, NH3) stay in MWh (LHV), as upstream PyPSA-Eur does. Material products are
in tonnes. The bus's `unit` column says which (D2).
_Avoid_: commodity (that word means traded energy in the commercial code), carrier (that is the
PyPSA attribute a product bus has, not the product).

**Inventory**:
A product Store between two process units, or between a process unit and delivery. Examples:
an H2 tank, an NH3 tank, an ore stockpile, a concentrate shed. Its level is in the product's
unit.
_Avoid_: buffer (too loose), storage (that word means the electrical StorageUnit).

**Production target**:
The product a period must deliver, as a delivery Load on the product bus. Its shortfall is
supplied by a priced slack (lost production). It is not a hard constraint.
_Avoid_: quota, demand (unqualified, that means the electrical demand).

**Lost-production value**:
The cost of one unit of product not delivered: the client's margin per tonne, or a contract's
shortfall penalty. It is not a purchase. When the product can be bought in, the buy-in is its
own priced source, and the lost-production value must be above that price. It is entered per product with its source. When it is missing, it is
Unavailable ([ADR-0001](../../pypsa-gui/docs/adr/0001-unresolvable-figures-ship-as-null.md)),
never 0.
_Avoid_: VoLL (that is per MWh of electricity, and stays the price of unserved electrical load).

**Criticality tier**:
How a process unit's electrical supply is ranked when it must be cut. There are four tiers:
`safety` (VoLL), `process_critical` (a stop costs a restart), `process_flexible` (it can stop
and catch up from inventory) and `deferrable` (it shifts within the day). This extends today's
two tiers: the `eh_critical` bus, and DSR as a priced resource.
_Avoid_: priority, criticality (unqualified), critical load (that is the `eh_critical` bus only).

**Restart time**:
The hours a process unit stays down after a stop before it produces again. It is modelled as
`min_down_time`, and its cost is the lost tonnes, priced by the lost-production value.
_Avoid_: start-up cost (PyPSA's `start_up_cost` is unweighted; §2.3).

## 2. The shared process-chain model (P1)

### 2.1 PyPSA mapping

| Process concept | PyPSA | Notes |
|---|---|---|
| Product | Bus with carrier `<product>` and `unit` (`MWh` or `t`) | new carriers in `carrier_catalog.py` |
| Process unit | Link `eh_process=True`; bus0 = main input, bus1 = product, bus2.. = other inputs with `efficiency2..` < 0 | efficiency = product per unit of bus0 input |
| Electricity input to a non-electric-led unit | bus0 = electricity, `efficiency` = t/MWh_el, `bus2` = feed with `efficiency2` = −feed per MWh_el | as upstream's Haber-Bosch |
| On/off, min load | `committable`, `p_min_pu`, `min_up_time`, `min_down_time`, fixed `p_nom` | linearised UC in planning (§2.3) |
| Ramp | `ramp_limit_up/down` | per snapshot, unweighted |
| Inventory | Store on the product bus, `e_nom` or `e_nom_extendable` | the cycling rule is in §2.4 |
| Production target | Load on the product bus (`p_set`, per period) | the delivery schedule |
| Buy-in (when the product can be bought) | Generator `__buy_<product>` on the product bus, `marginal_cost` = purchase price (+ CO2) | a real purchase, booked as an expense |
| Lost production | Generator `__lost_<product>` on the product bus, `marginal_cost` = lost-production value | weighted like any marginal cost. It must be above the buy-in price, or the LP "buys" by shedding (the validator checks it) |
| Feed from outside the site (ore from the mine, N2 from air) | Generator on the feed bus, `p_max_pu` = schedule, cost 0 | the mine is out of scope |

**Why a slack and not a hard target:**
- A hard per-period target (an `extra_functionality` row) makes infeasibility the answer to a
  weak grid.
- The client's question is "how many tonnes do I lose". The slack's dispatch is exactly that,
  and it costs no engine change.

### 2.2 Asset schema, forms, results and economics

What exists (verified on `cb04473d`):

- **The API accepts multi-port Links.** `LinkCreate` (`backend/models/schemas.py:180`) has
  `bus2..bus4` and `efficiency2/3`, with `extra='allow'`.
- **The asset schema does not.** Its Link block (`services/asset_schema/fields.py:308-338`) has
  no `bus2`, no `efficiency2` and no UC fields. `p_min_pu` is there (`:333`).
- **Generators have UC fields** behind the `committable` gate (`_UC`, `fields.py:85`), shown
  with `reveal_when`.
- **The Link form** shows bus2 only for CHP (`frontend/src/layout/CreationForm.tsx:221-232`).
- **Compare economics** counts output as |p0| × efficiency, in bus1's carrier, and does not
  price bus2 (`services/compare/economics.py:571-598`).
- **The carrier catalogue** (`services/carrier_catalog.py:15-49`) has 25 carriers. It has no
  heat, steam, CO2, NH3 or material carrier. The form's heat-bus filter (`CreationForm.tsx:210`)
  points at a carrier the catalogue does not define.

Changes:

1. **Schema.**
   - Link gets `bus2`, `efficiency2` and up to bus4.
   - The Generator's UC block moves to a shared helper and is reused on Link, behind the same
     `committable` gate.
   - Link gets `eh_process` and `criticality_tier` (§1.2).
   - It also gets `throughput_unit` and `specific_energy_kwh_per_t`. These are display-only and
     derived: the solve reads `efficiency`.
2. **Carriers.** Add `NH3`, `ore`, `concentrate` and `N2`, each with a `unit`. This also fixes
   the dangling heat filter: either add `heat`, or drop the filter (ticket 02 checks which).
3. **Form.** A "Process unit" type. It has:
   - one output (product bus);
   - 1–3 inputs, each with a rate per tonne;
   - the electricity input in kWh/t;
   - a UC section (min load, min up/down, restart time).

   The form writes the PyPSA efficiencies, so the user never types a negative efficiency.
4. **Results.**
   - Per-port flows (`p0..p4`) for multi-port Links.
   - A process view per product: production (t), delivery, inventory level, lost production
     (t and EUR), and running hours per unit.
   - Every figure carries its unit. Lost production is Unavailable when the lost-production
     value is missing (ADR-0001).
5. **Economics.**
   - Compare economics reads every port. An input port is priced at its bus's price, as bus0
     is today. An output port is priced at the product's value.
   - The investment case gets product flows through the one engine
     ([ADR-0004](../../pypsa-gui/docs/adr/0004-one-investment-engine-guided-mode-compiles-onto-it.md)).
     The study layer does not compute product revenue itself.

### 2.3 Exactly what the LP gets

| Element | LP form | Weighting |
|---|---|---|
| Process-unit dispatch | Link `p` ≥ 0, ≤ `p_nom`·`p_max_pu`·u | marginal cost × objective weight × period weight |
| Commitment u | **linearised UC** (`linearized_unit_commitment=True`): u ∈ [0, 1], `p ≥ p_min_pu·p_nom·u`, min up/down on u | none on u |
| Multi-input balance | Link port rows: `p1 = −eff·p0` (product), `p2 = −eff2·p0` (feed drawn) | — |
| Inventory | Store energy balance with `snapshot_weightings.stores` = hours per snapshot; e ≤ `e_nom` | capex on `e_nom` if extendable |
| Production target | Load `p_set` on the product bus | — |
| Buy-in | Generator at the purchase price, `p_nom` = the target (A only) | objective × period weight |
| Lost production | slack Generator `p ≤ p_set` of the target, `marginal_cost` = lost-production value (> buy-in) | objective × period weight |
| Restart | `min_down_time` = restart time in snapshots. Lost tonnes are the slack's dispatch. `start_up_cost` = 0 | — |

**Three constraints the PyPSA source imposes** (PyPSA 1.1.2, `pypsa/optimization/optimize.py`):

1. **Start-up cost is unweighted.**
   - `start_up_cost` and `shut_down_cost` enter the objective as `var × cost` with no snapshot
     or period weighting (`optimize.py:301-316`).
   - Marginal and stand-by costs *are* weighted (`:211-216`, `:257-275`).
   - With one 168 h week standing for 52.14 weeks × 3–10 years, a start-up cost would be
     undercounted by a factor of about 150–520.
   - **Design** (gatekeeper agrees: refuse, don't pre-scale. Pre-scaling breaks as soon as
     weights differ by snapshot or period, and a user would see a number that isn't the real
     cost).
     - The restart is `min_down_time`, and its cost is the lost tonnes the slack prices (which
       are weighted).
     - Any non-process cost of a start (for example a purge) goes into `stand_by_cost`.
     - "Weighted" means any `snapshot_weightings.objective` ≠ 1 or any
       `investment_period_weightings.objective` ≠ 1.
     - **Templates:** the template validator *refuses* `start_up_cost` or `shut_down_cost`
       above 0 on a weighted network. The refusal names the unit and points to `stand_by_cost`
       or the shortfall slack.
     - **User networks:** the solver *warns* (does not refuse) in the same case, because
       PyPSA's behaviour there is existing behaviour (ticket 03).
     - This is an upstream PyPSA gap, worth reporting upstream. The design does not depend on a
       fix.
     - **Upstream report (draft, ticket 03).** In PyPSA 1.1.2, `optimize.py:301-316` adds
       `start_up_cost · start_up` and `shut_down_cost · shut_down` to the objective with no
       `snapshot_weightings.objective` factor and no `investment_period_weightings.objective`
       factor, while marginal and stand-by costs get both (`:211-216`, `:257-275`). So on a
       clustered or multi-period network the cost of a start is undercounted by the weight. The
       expected behaviour is to weight start-up and shut-down costs like `stand_by_cost`. The
       same note is in `pypsa-gui/backend/services/solver/process_uc.py`.
2. **Committable and extendable together is big-M bilinear.** The owner already decided this for
   gensets (2026-10-13, option A: an extendable unit is never committed, see
   `solver/island.py:368-385`). The same rule applies to process units:
   - process units are **fixed-size inputs** (the client's flowsheet);
   - a later expansion is a fixed-size row with `build_year`;
   - the plan's decisions are the grid, energy and inventory capacities.

   Sizing the process itself is out of scope (D6).
3. **MIP settings only see committable Generators.** `solver_service.py:873-876` checks only
   `generators["committable"]`. A committable Link would reach the solver without the MIP gap
   and time limit.
   - Planning uses linearised UC (an LP), so this matters only in the operational check (§4.3).
   - Ticket 03 widens the check to Links.

**Why linearised UC in planning, and its condition** (gatekeeper: yes, with conditions):
- The plan is multi-period with extendable grid assets. Binary UC on 504 (A) or 26,280 (B)
  snapshots, times several units, turns a seconds-scale LP into a MILP of unknown time.
- **The relaxation is weak.** A fractional u lets a unit sit "half on" below its real minimum
  load and spread min up/down. So it *understates* restart losses (the shortfall) and the
  min-load cost.
- **So the binary operational check is mandatory** whenever a process Link is committable:
  - It re-solves one representative week per period with binary UC on the planned design (fixed
    capacities).
  - It reports per period, in the results and the ledger: binary minus linearised cost, and the
    extra shortfall tonnes.
  - Above the flag threshold (> 2 % cost, or any extra shortfall), the plan says "linearised UC
    understates restarts". It never shows a silent number. Ticket 03 fixes the threshold.

### 2.4 Inventories with representative weeks

Today:
- every staged template runs one 168 h week per period, with `objective` and `generators`
  weight 8760/168 and `stores` = 1 h (`eh_site.stage_network`, `eh_site.py:400-404`);
- stores cycle within each period (`cyclic_per_period`, `eh_site.py:428-439`, gatekeeper on
  #304).

Inside one week, store dynamics are physical (hour-weighted). The week then repeats 52 times.

What follows:
- **An inventory that turns over within a week is valued correctly.** Examples: an H2 tank
  riding a wind lull of 1–3 days; an NH3 tank feeding a continuous offtake.
- **An inventory that turns over over several weeks is not.** It cannot carry product across
  weeks, so the model shrinks it to its in-week role. Examples: a concentrate shed loaded out
  by a 25 kt vessel every 3 weeks; a seasonal H2 cavern.
- **Rule (enforced by the template validator).**
  - A representative-week template may hold only inventories whose delivery is continuous or
    at most weekly.
  - Any other inventory needs the chronological time base (§4, D4).
  - The validator says which inventory broke the rule.

### 2.5 Production targets per period

- The target is the delivery Load's `p_set`, a series per period. For A it is constant per
  period, for example 100 t/d → 4.17 t/h × 5.166 MWh/t.
- `stage_network`'s `load_scale` already scales it per period, as it does the H2 offtake today.
- An annual target with free timing (deliver 36.5 kt in the year, any week) is a *sum* row. In
  the representative week it is the same as a constant weekly delivery, because the week
  repeats. In the chronological year (B) it becomes a delivery schedule (shipments) plus the
  inventory. No per-period sum row is needed in either case.

### 2.6 What stays out

- **Batch sequencing and scheduling.** No recipes, campaign changes or setup times between
  products. That is MES and APS work (the Rockwell side of grid-to-process). This design hands
  over a power envelope and an inventory plan, not a schedule.
- **Part-load efficiency curves.** Efficiency is constant per unit. Piecewise-linear part load
  is "later" (§7).
- **Heat and steam carriers** are later. Upstream's Haber-Bosch waste heat
  (`use_haber_bosch_waste_heat: 0.25`, `config.default.yaml:900`) is not used in A.
- **Process sizing** (D6), quality and grade blending, and mine planning.

## 3. The examples

Every number is labelled **sourced** (with its source) or **demo** (synthetic, ledgered as
`source: synthetic`, to be replaced by the client's data). The templates ship the same labels in
their assumptions ledger, as the staged hubs do today.

### 3.A Example A: H2 hub to green ammonia (`eh_ammonia_staged`)

#### Flowsheet

```
grid ─grid_import─┐
wind, PV, BESS ───┤ hub (AC, 110 kV)
                  ├─ process_load 40 MW (unchanged, demo)
                  ├─ electrolyser(s) ──→ h2 (MWh_H2) ── h2_storage
                  │                         │
                  └────────────── haber_bosch (bus0 = hub el., bus2 = h2 in, bus1 = nh3)
                                            │
                                     nh3 (MWh_NH3) ── nh3_tank ── nh3_delivery (Load)
                                                              ├── __buy_nh3 (grey NH3 purchase)
                                                              └── __lost_nh3 (shortfall penalty)
```

- The air separation unit is folded into the Haber-Bosch electricity input, as upstream does
  (D3).
- The fuel cell and the existing H2 offtake are dropped. The H2 now goes to ammonia.
- `process_load` stays a fixed Load. It is a different process, which this example leaves
  alone.

#### Numbers

| Item | Value | Label | Source |
|---|---|---|---|
| NH3 energy content | 5.166 MWh/t (LHV) | sourced | `config/config.default.yaml:1015` (`MWh_NH3_per_tNH3`), upstream PyPSA-Eur |
| H2 per t NH3 | 5.93 MWh_H2/t | sourced | `config.default.yaml:1018`; DECHEMA via technology-data (PyPSA-Eur commit "haber-bosch: use DECHEMA source for hydrogen input") |
| Electricity per t NH3 (HB + ASU) | 0.2473 **unit to settle** | sourced, check | `config.default.yaml:1019` says MWh/t (Wang et al. 2018, Table 13). Upstream's link reads technology-data's `electricity-input`, which is per MWh_NH3. Per MWh that is 1.28 MWh/t; the earlier upstream value was 1.17 MWh/t. Ticket 05 pins it against technology-data `costs_2030.csv` before use (D3) |
| Haber-Bosch capex, FOM, lifetime | technology-data `Haber-Bosch` | sourced | technology-data v0.14.0 `costs_2030.csv`. These are not in the GUI pack (`defaults_pack/versions/2026-10-13/values.csv` has no ammonia rows); ticket 05 adds them with their source |
| NH3 tank | technology-data `NH3 (l) storage tank incl. liquefaction` | sourced | same; upstream key at `prepare_sector_network.py:1486-1498` |
| Electrolyser | pack `electrolysis` (1886 EUR/kW_e, 25 y, η 0.6217) | sourced | pack rows 276-279 (IEA e-fuels, DEA). The template keeps today's η 0.68 (demo); the ledger says which |
| NH3 delivery | 50 / 100 / 150 t/d (2027 / 2030 / 2035) | demo | sized so the 2035 HB needs about 37 MW_H2 (≈ 55 MW_el of electrolysis), within today's 200 MW connection |
| HB size | 160 t/d fixed (6.67 t/h), in from 2027 | demo | about 7 % headroom over the 2035 delivery, so a stop can be caught up |
| HB minimum load `p_min_pu` | 0.4 | demo | flexible-HB literature ranges from about 0.2 to 0.7; D-A1 sweeps it |
| HB min up / min down (restart time) | 24 h / 8 h | demo | warm restart of the synthesis loop |
| HB ramp | 0.2 p.u./h | demo | |
| Grey NH3 buy-in price (`__buy_nh3`, and the IC's `ReferencePurchase`) | 97 EUR/MWh_NH3 (≈ 501 EUR/t) + CO2 | sourced, as an upstream default | `config.default.yaml:982` (`imports.price.NH3`). It is an upstream model default, not a market quote; the ledger says so |
| Lost-production value (`__lost_nh3`: the offtake contract's shortfall penalty) | 800 EUR/t | demo | must be above the buy-in price (validator). Ledgered input, never a VoLL default (#312) |
| Grey NH3 CO2 intensity | demo | demo | stated by the case (as `GREY_H2.co2_t_per_kg` is) |
| CO2 price, tariff, finance | as `eh_h2_hub_staged` | demo | `eh_staged_hubs.py` (`CO2_PRICE`, `SYNTHETIC_TARIFF`, `_h2_finance`) |
| Wind, PV, prices, process load | as `eh_h2_hub` | demo | `eh_templates.build_eh_h2_hub` |

**The NH3 bus is in MWh_NH3 (LHV).** This keeps upstream's convention and lets the investment
case reuse `ReferencePurchase` unchanged (`models/commercial.py:606`, with
`lhv_kwh_per_kg = 5.166`, `load = nh3_delivery`). The UI shows tonnes through the bus's
conversion factor (D2).

#### Gaps it exercises

- P1: all of it — a multi-input committable Link, a product Store and Load, results per port,
  and economics on bus2.
- P2:
  - grey buy-in kept separate from shortfall, priced at its own value;
  - restart time;
  - the HB's criticality tier `process_critical`, with the electrolysers `process_flexible`.
- **IC:** the counterfactual is "buy grey NH3". It uses the existing `ReferencePurchase` path
  (#312), so no engine change is expected; ticket 06 confirms it.
- **Not P3:** delivery is continuous, so the week is enough (§2.4).

#### Decision questions it answers for the client

1. **How flexible must the HB loop be?** NPV and lost tonnes at `p_min_pu` 0.2 / 0.4 / 0.7. This
   is the value of a flexible-HB design, in EUR.
2. **H2 tank or NH3 tank?** The plan sizes both (extendable) against grid import and the
   battery. The answer is a split, for example x MWh of H2 and y t of NH3.
3. **How much smaller can the connection be?** Run with the extendable connection and its fee
   (the connection agreement is already in the LP), with and without the inventories. The
   difference in MW is the inventory's grid value.
4. **Green or grey?** NPV against buying grey NH3, per period, and the CO2 price at which green
   breaks even. These are the investment case's own outputs.
5. **What does a trip cost?** A grid loss that trips the HB stops it for 8 h × 6.67 t/h ≈ 53 t
   (demo). What the NH3 tank cannot cover is bought in at about 501 EUR/t, so up to about 27 kEUR
   per event. §5 shows how the ride-through result turns into that figure.

#### Acceptance checks (A)

- The template builds and solves on HiGHS. The objective is finite, and the solve takes under 2×
  `eh_h2_hub_staged`'s time on the same machine.
- **Mass balance.** In every period, Σ production + Σ lost = Σ delivery (± tank Δ, which is 0
  per cyclic period) to 1e-6. H2 drawn by the HB = 5.93/5.166 × NH3 made.
- **Units.** With the electricity input pinned (ticket 05), HB electricity = that value × t
  made, to 1e-6.
- When it runs, the HB never runs below 0.4 × p_nom × u. Every stop lasts at least 8 snapshots.
  The binary operational check reports its objective gap against the linearised plan.
- **Inflexible HB (never stops).** A test pins the sign on the 2035 week alone, where the delivery
  (150 t/d) lies between the minimum load (64 t/d) and the size (160 t/d). The flexible HB
  (committable, min up/down 24 / 8 h, binary UC) is compared with a never-stopping HB (not
  committable, `p_min_pu` 0.4), both without the ramp limit. A never-stopping HB is a restriction of a
  flexible one, so it costs at least as much and needs at least as much H2 storage or grid import.
  It is not tested on the three-period plan, for three measured reasons:
  - The linearised plan flips the sign: `p_min_pu` 1.0 with a free fractional u is *more* flexible.
  - A never-stopping HB is infeasible in 2027 (delivery 50 t/d, minimum load 64 t/d: no sink for the
    surplus), and at the flexible plan's fixed capacities in 2035 (no H2 slack).
  - A binary re-plan of the three periods hits a 600 s time limit at a 1 % gap.
- With an infinite connection and free storage, lost production and buy-in are 0. With the
  electrolysers removed, buy-in = delivery and lost production = 0, because the lost-production
  value is above the buy-in price.
- **Binary check.** The binary operational check runs and reports its per-period gap (§2.3).
- The investment case computes. Its counterfactual is the grey NH3 purchase. It has no
  `counterfactual_not_established` blocker.
- The ledger lists every row of the table above with its label. No ammonia number lives only in
  code.

### 3.B Example B: mining concentrator (`eh_concentrator_staged`)

#### Flowsheet

```
weak grid ─poc (firm F + non-firm NF, staged)─┐
PV (candidates), BESS (candidates) ───────────┤ site (AC, 33 kV)
                                              ├─ crusher ──────→ ore_fine
mine (ore Generator, fixed schedule) → ore_rom│                    │
                                              │   crusher: bus0 = site el., bus2 = ore_rom in, bus1 = crushed ore
                                              │  coarse_ore_stockpile (Store on ore_fine)
                                              ├─ mill_1, mill_2 (SAG + ball, one Link per line) → slurry
                                              ├─ flotation ──────→ concentrate
                                              │  concentrate_shed (Store)
                                              │  shipments (Load: one vessel every 3 weeks) + __lost_conc
                                              └─ site_services (fixed Load: pumps, water, offices)
```

- Electricity-only process. No heat, no H2.
- Each process unit has bus0 = electricity, so its efficiency is t/MWh = 1000 / kWh/t, and bus2
  draws its feed.
- Slurry has no inventory: grinding and flotation run as one line. The model has mill → flotation
  in series with no Store, so flotation follows the mills (D7).

#### Numbers

Nearly all of B is demo. No open, citable dataset gives one concentrator's whole flowsheet. The
anchors that are sourced are methods, not site numbers.

| Item | Value | Label | Source |
|---|---|---|---|
| Ore feed (mine) | 800 / 1000 / 1200 t/h (2027 / 2030 / 2035) | demo | ≈ 19–29 kt/d, a mid-size copper concentrator |
| Grinding specific energy | Bond: W = 10·Wi·(1/√P80 − 1/√F80) | sourced method | Bond's third theory of comminution (Bond, 1952). With demo Wi 14 kWh/t, F80 2 mm, P80 150 µm: ≈ 8.3 kWh/t ball-mill duty, plus SAG ≈ 8 kWh/t (demo) → **≈ 16 kWh/t** |
| Crushing | 0.8 kWh/t | demo | |
| Flotation | 2.0 kWh/t | demo | |
| Site services | 3 MW | demo | |
| Mill lines | 2 × 550 t/h; a third 550 t/h line from 2035 | demo | 10–38 % headroom over feed, so a stop can be caught up |
| Crusher capacity | 1.4 × feed | demo | it can run in cheap or sunny hours, with the stockpile between |
| Coarse-ore stockpile | 24 h live (extendable) | demo | |
| Mill p_min_pu, min up/down (restart) | 0.6, 12 h / 4 h | demo | restart after a stop: re-establishing the charge and the circuit |
| Flotation p_min_pu | 0.5 | demo | follows the mills |
| Mass pull to concentrate | 1.89 % (head 0.6 % Cu × recovery 0.88 / conc grade 28 %) | demo | the formula is standard metallurgical accounting; the numbers are demo |
| Shipments | 25 kt concentrate every 21 days | demo | the reason B needs P3 |
| Concentrate shed | 40 kt (extendable) | demo | |
| Lost-production value | 1,300 EUR/t concentrate (≈ 24 EUR/t ore) | demo | NSR minus variable cost; the client's own number |
| Grid | 2027: 25 MW `non_firm_dynamic`, its envelope 15 MW in 300 h of the year and 25 MW otherwise; 2030: 25 MW firm; 2035: 35 MW firm | demo | the agreement kinds exist (`ConnectionKind`, `models/commercial.py:41`; `services/commercial/connection.py`). `non_firm_dynamic` puts the envelope in the LP; an `fca`'s hours are a stress entry, not the solve's cap (`connection.py:25-28`), so B uses the dynamic kind |
| PoC fault level | 150 MVA (SCR about 6 against 25 MW) | demo | weak, so the motor-start check (§4.3) bites |
| PV, BESS candidates | pack prices (`solar-utility`, `battery`) | sourced | defaults pack `2026-10-13` |
| Tariff, finance | as the generic site's industrial preset | demo | `eh_site.py` |

**Load check (demo).** At 1000 t/h: crushing about 0.8 MW, grinding about 16 MW, flotation about
2 MW, services 3 MW, for about 22 MW in total. That sits above the 2027 firm 15 MW and inside
firm + non-firm. The template's test pins that the 2027 plan without PV and BESS loses
production, so the example has something to decide.

**Context.** On 15 Oct 2026 (Zurich), Hitachi Energy and Rockwell Automation announced a
collaboration on an "integrated grid-to-process architecture", aimed at mining and metals first:
[press release](https://www.hitachienergy.com/news-and-events/press-releases/2026/10/hitachi-energy-and-rockwell-automation-unite-power-and-automation-to-help-industries-accelerate-electrification).

#### Gaps it exercises

- P1, with several process units in series, a feed from outside the site, and two inventories.
- P2:
  - lost production as margin;
  - criticality tiers: crusher `process_flexible`, mills `process_critical`, services `safety`
    for the life-safety share;
  - restart.
- **P3:**
  - a multi-week inventory (the shed and the shipments);
  - the minutes-scale questions: a mill motor start on a weak grid, and the restart after a dip.
- **Grid:**
  - non-firm curtailment hours;
  - staged energisation;
  - the weak-grid motor start;
  - N-1 on the site transformer.
- **IC:** the counterfactual is "the site as it is today" (existing connection, no PV or BESS,
  same process). There is no product to buy in, so the counterfactual is a baseline re-solve. It
  is the same mechanism as the microgrid's (#302), but the investment case's
  `owner_conversion_load` blocker (`services/results/finance_case.py:1074`) has to accept a
  product Load whose counterfactual is a re-solve (ticket 11, gated).

#### Decision questions it answers for the client

1. **Firm or non-firm, and how much?** Lost tonnes and NPV for each connection offer, with PV and
   BESS optimised. In other words: what does each MW of firm capacity earn in tonnes?
2. **What are the stockpiles worth?** With and without the coarse-ore stockpile and the
   mill-line headroom, how many tonnes are lost to non-firm curtailment, and how much less
   connection is needed?
3. **What is ride-through worth?** Expected dips per year × the probability that a mill trips ×
   (restart 4 h × 550 t/h × 1.89 % × 1300 EUR/t), about 54 kEUR per mill trip (demo). That
   figure is set against a BESS or STATCOM sized for ride-through (§5).
4. **NPV against the site as it stands today**, by stage (2027 / 2030 / 2035).
5. **Can the weak grid start a mill?** The dip at the PoC when a mill motor starts, with and
   without a soft starter or VFD, or the BESS's support (§4.3).

#### Acceptance checks (B)

- The template builds and solves on the hourly chronological year (D4) within the budget ticket
  09 sets: proposed ≤ 30 min on the CI runner, with memory measured and reported.
- **Mass balance.** Ore in = Σ crusher; crusher = Σ mills + Δ stockpile; concentrate =
  mass-pull × mill throughput; shipments = production + lost − Δ shed. Each holds to 1e-6 per
  period.
- **The shed carries across shipments.** Its level rises for 21 days and drops at each vessel. A
  test pins that the representative-week build of the same template is refused by the validator
  (§2.4) with the shed named.
- **Non-firm curtailment.** In the curtailment hours, import ≤ firm. Lost tonnes in 2027 without
  PV and BESS are greater than 0, and fall when BESS is allowed.
- **Restart.** Each mill stop lasts at least 4 h. With `min_down_time` 0 and no headroom, lost
  tonnes do not rise, which pins the sign.
- **Motor start (§4.3).** It reports ΔU at the PoC for each mill start method, against the
  limit. A direct-on-line start fails on 150 MVA. A VFD start passes.
- **The investment case computes** against the today baseline, with no blocker. Lost-production
  EUR is shown separately from the energy bill.

## 4. Time-scale design (B)

### 4.1 What exists

- **Planning:** multi-period and discounted, one 168 h week per period (§2.4).
- **Operation:**
  - `solve_strategy: rolling` with `rolling_horizon` 168 and overlap 24
    (`solver_service.py:393-395`). It falls back to `full` under multi-period
    (`:913-922`).
  - `time_aggregation_service.py` clusters K representative periods with tsam, for the myopic
    planner.
- **Adequacy:** MC.
- **Frequency:** 20 s at 1 ms (gridspine I6).
- **Between about 20 s and 1 h: nothing.**

### 4.2 Valuing a multi-week inventory

Three options, compared on what B needs:

| Option | Snapshots (3 periods) | LP size vs today's 504 | Chronology | Engine change |
|---|---|---|---|---|
| **(b) Full hourly year per period (decided, D4)** | 26,280 | ≈ 52× | exact | none (`stage_network` takes a year) |
| (a) Chronological year at 3 h | 8,760 | ≈ 17× | exact at 3 h | none, but every place that assumes 1 h snapshots needs an audit (below) |
| (c) Chained representative days (Kotzur et al. 2018, *Applied Energy* 213: inter-period storage linking) | ≈ 3 × 24 × 12 typical days + 365 linking variables | ≈ 2× | approximate | yes: custom `extra_functionality` rows for intra and inter storage (gated, solver) |

**Decided: (b), a full hourly year for planning** (owner, 2026-10-16, D4).

- **No engine change.**
  - `stage_network` already tiles any series. The template supplies 8,760 hourly snapshots per
    period instead of a week.
  - The weightings are `objective` = `generators` = `stores` = 1.
  - The investment-period weighting stays the period's years.
- **The shipment schedule is exact.** Every snapshot is one hour, as everywhere in the app
  today, so tariffs, 15-min billing assumptions, adequacy metrics and finance annualisation see
  the time step they already handle.
- **Min up and down times stay in hours** (1 snapshot = 1 h). There is no conversion and no
  rounding.
- **Stores** are cyclic per period over the year (`e_cyclic_per_period`), which is physical over
  a year.
- **PV and wind profiles:** a year of `p_max_pu` series per site. The demo uses the synthetic
  daily shape with seasonal scaling (demo, ledgered).
- **The solve-time and memory cost.** It is about 52× today's LP, estimated at minutes to tens
  of minutes on HiGHS.
  - Ticket 09 measures the solve time and peak memory of a B-sized network (about 30
    components, 3 periods) on the CI runner, and reports both to the owner before template 10 is
    built.
  - **Proposed budget: ≤ 30 min and within the runner's memory.**
  - **If it is exceeded:** B waits, and option (c), chained representative days, comes back as
    a later gated solver item with its own plan check. Option (a) is not the fallback, because
    of the audit below.
- **Still to check in 09** (where code assumes one week per period, not one hour per snapshot):
  - the 168 literals in `rolling_horizon` and `lf_period_length_h` (`solver_service.py`);
  - `time_aggregation_service.py`, `routers/network_time_axis.py`, `validation_service.py`,
    `profile_shapes.py` and `study/packs.py`;
  - the size of the campus study's per-hour tables (26,280 rows) and its ranking.

  Each is tested on the hourly year, or refused there with a reason.
- **The binary UC check (§2.3)** still re-solves one week per period.

**If 3 h is wanted later** (option a, not planned): the gatekeeper's list (2026-10-16) is what it
would need. Each place gets a test comparing 1 h with 3 h, or a refusal:
- `tariff_engine.py`: demand charges on 3 h averages need a peak factor or a refusal;
- `lp_bindings.py`: kWh = MW × the interval's hours;
- `connection.py`: nyears and the FCA hours;
- `finance_case.py`: annualisation `:2220-2226` and import price `:1819-1888`;
- `adequacy/metrics.py`: `HOURS_PER_YEAR` and the weights;
- the island rows and the campus tables;
- min up/down as ⌈hours / Δt⌉.

### 4.3 The minutes-scale layer (restart, motor start, dip)

This is not in the LP. It is a post-solve check, like the campus study, on the plan's selected
hours. It has three parts:

1. **Motor start (static, gridspine).**
   - ΔU at the PoC ≈ S_start / S_k, where S_start = √3·U·I_start for the start method:
     - direct-on-line ≈ 6 × rated;
     - soft starter ≈ 3 ×;
     - VFD ≈ 1 ×;
     - the BESS's reactive support is subtracted when it is grid-forming.
   - The check runs against the network operator's limit for rapid voltage changes. The demo uses
     3 % (planning level of the IEC TR 61000-3-7 kind; the client's grid code governs, and the
     ledger says so).
   - Inputs per process unit: motor kW, start method, start current. All are new fields on the
     process unit (ticket 12).
2. **Dip ride-through to process cost.**
   - For each dip class (from the operator's dip statistics: demo counts per year by depth and
     duration), decide which process units trip. That depends on each unit's contactor or VFD
     ride-through curve, a demo input.
   - Expected lost tonnes = Σ trips × (restart time × rate × mass pull).
   - This is the bridge from gridspine's ride-through and frequency results to EUR.
3. **Restart sequence (event model, 1 s – 4 h).**
   - After a trip or an island transition: the order and timing in which process units come back
     (pickup blocks, as the island sidecar's), each restart's motor start (check 1), and the
     restart time to full rate.
   - Output: the restart timeline and the lost tonnes.
   - It is a simple discrete-event model, not dynamics. Dynamics stay in gridspine I6 and PSS/E.

**What it needs that does not exist:**
- motor data and ride-through curves per process unit;
- dip statistics per PoC (demo values until the client gives the operator's);
- a gridspine static module for ΔU, beside `static/capacity.py` and `static/frequency.py`;
- a results section.

A minutes-scale model does **not** feed back into the LP in this design. Its costs are shown
beside the plan (§5). D5 asks whether a loop is wanted later.

## 5. Coupling: grid results to process cost

| Grid result | Where it comes from today | Turns into process cost how | In scope |
|---|---|---|---|
| Connection limits (firm, non-firm, FCA, energisation, extendable connection) | in the LP (`services/commercial/connection.py`, `eh_energization_mw`) | **directly**: the LP curtails process units and the lost-production slack prices it. No loop is needed | yes, P1 |
| Tariffs (ToU, demand, ratchets) | in the LP | directly: process timing shifts to cheap hours within the inventory's room | yes, P1 |
| Ride-through (UPS bridge, `ups_bridged`, frequency E1–E6) | post-solve, the campus study and gridspine I6 | **feed-forward**: failing hours × the units that trip there × restart lost tonnes × lost-production value, reported as EUR per year | yes, P2/P3 (ticket 13) |
| Island (island hours, pickup blocks) | post-solve, island sidecar and I6/I7 | feed-forward: process units outside the island's pickup blocks are lost for the island duration × rate. The I7 cuts loop (`campus_island_cuts.apply_loop`, `MAX_TRIALS` 3) is unchanged | report yes; a loop no |
| N-1 (site transformer) | post-solve, campus study | feed-forward: the units fed by the outaged transformer × outage rate × MTTR × rate → expected lost tonnes per year, so the cost of a second transformer can be set against it | yes, report (ticket 13) |
| Internal network limits (transformer and feeder ratings) | post-solve sizing; reaches finance only | **feedback** (P4): campus sizing → a Link cap on the feeder serving each process bus, re-solve (a loop like I7) | no: later, D5 |
| DR and ancillary contracts | settlement only | inside the solve as a process-flexibility product (P4) | no: later, D5 |

**Principle.** Every grid result is turned into tonnes first, then EUR at the lost-production
value. When that value is missing, the EUR figure is Unavailable. The client sees one currency
(EUR) and one physical unit (t) for every grid risk.

**Loops in scope.** None new. The existing I7 loop stays as it is. P4 (internal limits and DR in
the solve) is listed in §7 as later, because #332 is in flight in the island and tuning code and
this design stays out of it.

## 6. Tickets

The tickets live under `issues/`. They are ordered A first, then B. "Gated" means the change
touches gridspine, investment and finance, adequacy, IC or solver engine code, so the gatekeeper
sees the plan first (steward). Model tier: **opus** for gated or cross-layer work, **sonnet**
for template data, UI and docs. Size: **S** ≤ 1 day, **M** 2–3 days, **L** ≥ 4 days of agent
work, roughly.

| # | Ticket | Blocked by | Gated | Tier | Size |
|---|---|---|---|---|---|
| 01 | Glossary: process terms in CONTEXT.md | — | no | sonnet | S |
| 02 | Process unit in the asset schema, carriers and API (multi-port and UC fields on Link) | 01 | no | sonnet | M |
| 03 | Solve path: committable Links (MIP settings, linearised UC, mandatory binary check, `start_up_cost` refusal and warning, the template validator's process rules) | 02 | **yes** (solver) | opus | M–L |
| 04 | Results and compare economics read every Link port; process results view | 02 | **yes** (economics) | opus | M |
| 05 | **A tracer:** `eh_ammonia_staged` template, pack rows for Haber-Bosch and the NH3 tank, ledger | 03, 04 | no (template, pack data; the validator rules live in 03 and 09) | sonnet | M |
| 06 | A investment case: grey NH3 counterfactual via `ReferencePurchase`, product flows in the engine | 05 | **yes** (IC) | opus | S–M |
| 07 | Process-unit form ("Process unit" type) and Guided display of the process view | 02, 04 | no | sonnet | M |
| 08 | P2: criticality tiers and lost-production value per product (adequacy and DtC read the tiers) | 05 | **yes** (adequacy) | opus | L |
| 09 | P3: hourly chronological year as a staged-template time base, with measured solve time and memory (reported to the owner before 10), the one-week-per-period audit, and the inventory-delivery validator rule | 03 | **yes** (solver: it changes the staged templates' time base and adds a validator rule) | opus | M |
| 10 | **B tracer:** `eh_concentrator_staged` template | 08, 09 | no | sonnet | M |
| 11 | B investment case: re-solve counterfactual for a product Load (`owner_conversion_load`) | 10 | **yes** (IC) | opus | M |
| 12 | Minutes-scale: motor-start ΔU check and process restart sequence (gridspine static + results) | 10 | **yes** (gridspine) | opus | L |
| 13 | Coupling report: ride-through, island and N-1 → lost tonnes and EUR | 08, 12 | **yes** (reads gridspine and adequacy results, report) | opus | M |
| 14 | Assistant: process-unit fields in the chat tools and the template parameters | 07 | no (harness: read its README) | sonnet | S |

Gatekeeper additions (2026-10-16):
- **The template validator is gated.** Its rules (the `start_up_cost` refusal, inventory
  delivery, lost value > buy-in) are solver semantics, so they live in 03 and 09, not in the
  template tickets.
- **The lost-production value is gated** wherever it reaches economics or the IC (04, 06, 11).
  It is a ledgered input, never a VoLL default (#312, where the baseline valued H2 at VoLL and
  the NPV was wrong). The case books slack tonnes as lost revenue in both the project and the
  counterfactual, never as an expense twice.
- **Gated tickets follow the PR rules:**
  - each one sends its own plan to the gatekeeper;
  - a bug-fix ticket names its first failing test;
  - a gated PR carries its `mp-code-review` result in the body (steward, #334).

Critical paths:
- A runs 01 → 02 → 03/04 → 05 → 06, 08.
- B runs 09 → 10 → 11/12 → 13.
- 09 can start once 03 lands, in parallel with 05.

## 7. Later (not ticketed)

- **P4, grid feedback.** Internal transformer and feeder limits into the LP through a
  campus-sizing loop (like I7), and DR and ancillary products inside the solve. This waits for
  #332 and D5.
- Heat and steam carriers, HB waste heat, and CO2 as a product.
- Part-load efficiency (piecewise-linear) and binary UC in planning.
- Chained representative periods (option c) for large or multi-site clients.
- Process sizing as a decision (D6).

## 8. Decisions for the owner

Each decision lists its options, with the recommended option first.

**All decided by the owner on 2026-10-16 (about 13:50 CEST):**
- D1, D2, D3, D5, D6 and D7 as recommended.
- D4 changed to (b), a full hourly year.

The options are kept below for the record.

- **D1, the product for A. Decided (a), green ammonia (owner, 2026-10-16).**
  - (a) Green ammonia — *recommended*: the coordinator's pick. Upstream PyPSA-Eur already has
    the link, the data and the conventions, and the investment case's `ReferencePurchase` fits
    it unchanged.
  - (b) Green steel (H2-DRI). Upstream has DRI data too, but it brings iron ore and scrap
    materials and a heat carrier, so more of P1 is needed before the first result.
- **D2, product units. Decided (a), MWh for energy products and t for materials (owner,
  2026-10-16).**
  - (a) Energy products in MWh (LHV), material products in t; the UI converts NH3 to t —
    *recommended*: it matches upstream, and `ReferencePurchase` and the existing results work
    for A unchanged.
  - (b) Everything in t. That is uniform for the client but breaks the reuse and needs a unit
    layer in the commercial engine.
- **D3, Haber-Bosch electricity and ASU. Decided (a), ASU folded into the HB unit (owner,
  2026-10-16).**
  - (a) One HB unit whose electricity input includes the ASU, with the value pinned against
    technology-data — *recommended*: as upstream does. The 0.2473 unit question is settled in
    ticket 05 before any result ships.
  - (b) A separate ASU unit with an N2 buffer. This is more flexibility to show, but every ASU
    number is demo.
- **D4, the time base for B. Decided (b), a full hourly chronological year (owner, 2026-10-16;
  the design had recommended (a)).**
  - (a) A chronological year at 3 h per period: exact chronology for a 21-day shipment cycle,
    smaller, but it needs the 1 h audit.
  - (b) A full hourly year: exact, about 3× (a) in size, no audit. **Chosen.**
  - (c) Chained representative days: fast, but gated engine work, and approximate.
  - **D4b:** ticket 09 measures the solve time and memory and reports them before B is built.
    The proposed budget is ≤ 30 min on the CI runner. If it is exceeded, option (c) becomes a
    later gated item (§4.2).
- **D5, feedback loops. Decided (a), none new in this package (owner, 2026-10-16).**
  - (a) None new in this package. Grid results are fed forward into lost tonnes and EUR (§5),
    and P4 comes later — *recommended*: #332 is in the island code, and feed-forward already
    answers the client's questions.
  - (b) Add the internal-limits loop now (P4).
- **D6, process sizing. Decided (a), process units are fixed client inputs (owner,
  2026-10-16).**
  - (a) Process units are fixed client inputs. The plan sizes grid, energy and inventory —
    *recommended*: this avoids committable-and-extendable big-M (owner decision 2026-10-13), and
    the client's flowsheet is a given.
  - (b) Make the process size a decision (MILP or a sweep over discrete sizes).
- **D7, slurry or surge capacity between grinding and flotation in B. Decided (a), none (owner,
  2026-10-16).**
  - (a) None: grinding and flotation run as one line — *recommended*: this is the usual plant
    practice at plan scale, and a small surge tank (minutes of hold-up) adds little at hourly
    steps.
  - (b) A surge tank Store.

## Comments

- **2026-10-16, gatekeeper plan check on §2 and §4: go, with changes. All are folded in above.**
  - The gatekeeper confirmed on the installed PyPSA 1.1.2 that start-up and shut-down costs are
    unweighted.
  - **(a)** Linearised UC is OK, provided the binary check is mandatory, its gap is reported per
    period, and a flag threshold applies (§2.3).
  - **(b)** Refuse `start_up_cost` in templates, warn on user networks, with "weighted" defined
    (§2.3).
  - **(c)** 3 h snapshots are not usable until the listed places are covered. That is moot now
    that D4 chose an hourly year; the list is kept in §4.2 for a later 3 h option.
  - **(d)** Gating:
    - the validator is gated;
    - the lost-production value is gated, and is never VoLL;
    - buy-in is kept separate from shortfall, with lost value > buy-in (§2.1, §3.A, §6).
  - The design PR itself needs no gatekeeper OK.
- **2026-10-16, owner decisions** (relayed by the coordinator): D1–D3 and D5–D7 as
  recommended; D4 changed to a full hourly year. Then: "Merge, start ammonia path".
