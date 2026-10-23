<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Research note: Schneider Electric, "Electrifying Refining and Petrochemical Industries"

Status: research note for the needs-triage grid-to-process spec ([design](../design.md)). It changes no spec
or issue file; it only points at them.

- **Source:** Schneider Electric (SE), white paper / eGuide "Electrifying Refining and Petrochemical
  Industries", author Damien Feral (SE Process Electrification Practice, p21). Doc ref
  998-23129210_DESW024EN, <https://download.se.com/files?p_Doc_Ref=998-23129210_DESW024EN>. Footer
  "© 2026" (p22); the dated data in it is 2016-2023. No publication date is stated.
- **Read:** PDF text extracted with `pdftotext -layout`; pages 10 and 18 are photo-only. Page numbers
  below are the printed numbers, which equal the PDF page index (22 pages including back cover).
- **Code state read for section 6:** `origin/master` at `3660dc810`.

## 1. How much weight this source can carry

- **It is a vendor marketing document** by a company that sells electrification, power distribution and
  advisory services (the "SE Advisory Services" logo is on the photo pages, p10 and p18; the closing
  advice is to work with "trusted partners", p19). Its job is to motivate electrification projects.
- **It is qualitative.** It has no cost figure for any technology, no heat-pump COP, no temperature band,
  no equipment size in MW, no payback, no case study, and no emission-reduction number for electrification
  itself. The "Electrification Use Cases" (p16-17) are a list of ten pictograms with one-line captions.
- **Numbers that are the paper's own** are mostly tagged "Based on Schneider Electric internal
  calculations/data" (footnotes 3, 18, 19) or carry no footnote at all. Those cannot be checked.
- **Numbers that are third-party** come from Concawe (2012, 2019), Solomon (2018, via Concawe), Wood
  Mackenzie, Reuters, IEA, TotalEnergies. These are secondary citations of other people's data; for use in
  the spec each should be re-read at its primary source (section 7).
- The petrochemical half of the title is nearly empty: steam crackers and aromatics complexes are named
  once as integration partners of refineries (p4) and never treated as electrification targets.
- Use as: a vocabulary and an issue list from an equipment vendor, plus a few citable pointers. Not as a
  source of a model parameter.

## 2. CO2 sources and energy shares

Each row: figure, page, status. "Paper" is a claim by SE; "3rd" is a figure it cites; "Chart" is read from
a figure.

| Figure (quoted) | p | Status and source |
|---|---|---|
| "an average oil refinery (i.e., with a crude capacity of about 150 kbbl/d) emits over 1 million tons of CO2 (scope 1 and 2) per year (depending on its exact configuration and energy mix)" | 4 | Paper, no footnote. Vague ("over"). |
| "an average oil refinery of 150 kbbl/d typically needs over 700-900 MW of thermal power ... and about 50 MW of electricity" | 4 | Paper; fn3 "Based on Schneider Electric internal calculations". Not reproducible. |
| refineries "consume about 3% to 9% of their crude oil feedstock, converted into fuel gas from the refining process itself" | 4, 14 | Paper, no footnote. |
| "an average EU refinery incorporates close to 20% natural gas" | 4 | 3rd: fn4 Concawe, "CO2 reduction technologies. Opportunities within the EU refining system (2030/2050)", Rpt 19/8, refining data from Solomon. |
| large steam demand (steam turbines driving compressors, distillation reboilers) "may represent approximately 20% of a refinery's direct CO2 emissions" | 4-5 | Paper, no footnote ("may"). |
| grey hydrogen from steam methane reforming (SMR) "representing up to around 20% of a refinery's direct emissions" | 5 | Paper, no footnote. Consistent with the 20.6 % hydrogen slice of Figure 2 (p6). |
| "approximately 20 fired heaters" in a typical refinery; the heaters of catalytic units, cracking, delayed coking and the reboilers of crude and vacuum distillation "generate the biggest portion of a refinery's emissions" | 5 | Paper, no footnote. |
| Figure 1, refinery energy consumption by fuel type, EU-28, operating year 2016: produced fuel gas 48.4 %, purchased fuel gas 17.7 %, coke on catalyst 11.9 %, purchased power 9 %, produced residue 4.5 %, purchased steam 3.6 %, other 3.2 %, produced C3/C4 0.8 %, purchased other 0.8 %, produced naphtha/distillate 0.1 % | 5 | 3rd, Chart: "Source: Solomon 2018". Values read from the chart labels; they sum to 100.0. |
| Figure 2, oil refining CO2 by process (utilities distributed across units): FCC 21.7 %, hydrogen 20.6 %, CDU 18.4 %, off-sites 11.7 %, catalytic reformer 9.8 %, VDU 6.4 %, alkylation/isomerisation 5.5 %, hydrotreating 5.3 %, sulphur recovery 0.6 % | 6 | 3rd, Chart: "Source: Concawe CO2 reduction technologies - July 2019". Sums to 100.0. Note that this is the same Concawe report as fn4. |
| "Currently only 5% to 10% of refineries' energy use is electrical. Reaching even 20% electrification will mean more than doubling electrical consumption." | 14, 15 | Paper, no footnote. Broadly consistent with Figure 1's 9 % purchased power and with p4's 50 MW against 700-900 MW (my arithmetic: 5.3-6.7 %). |
| energy "accounts for around 60% of EU refineries' cash operating costs. This proportion has doubled over the past 20 years" | 12 | 3rd: fn17 Concawe "EU refinery energy systems and efficiency" (2012 report). Old; Concawe's own definition of "cash operating costs" not given. |
| EU ETS "above 85 EUR/ton in 2023 but are forecasted to rise to over 100 EUR/ton" | 6 | 3rd: fn6 Wood Mackenzie (CBAM), fn7 Reuters 28 Apr 2023. Dated 2023, out of date by the 2026 footer. |
| Wood Mackenzie AET-2: "only around 150 of today's existing 700+ refineries will remain by 2050" | 7 | 3rd: fn13 Wood Mackenzie. Context only; not needed for the spec. |

**My cross-check (not in the paper).** 700-900 MW thermal over 8,760 h is 6.1-7.9 TWh/yr. At the
repo's natural-gas factor of 0.187 tCO2/MWh (`pypsa-gui/backend/services/carrier_catalog.py`, `gas`), if all
of it were gas that is 1.1-1.5 Mt/yr, which is consistent with the paper's "over 1 million tons". The
refinery's real fuel is part fuel gas and coke, so this only shows the orders of magnitude agree.

## 3. Process heat by temperature band

**The paper gives none.** No temperature band, steam pressure level, or heat-demand split appears
anywhere. The only temperature-related text is qualitative:

- the choice of technology depends on "specific heat requirements ... the process's fluid
  characteristics (temperature, fluid composition and phases, fouling or coking tendency, etc.)" (p11);
- "steam network configuration between various pressure levels" must be considered (p15);
- the fired heaters of the biggest emitters are catalytic units, cracking, coking and crude/vacuum
  reboilers (p5), which is a statement about where the emissions are, not about their temperature.

The heat demand that does appear is a single number per refinery, 700-900 MW thermal (p4, SE internal).
The spec's need (heat by band) is not answered; see section 7.

## 4. Electrification use cases and their stated numbers

The paper's ten use cases are on p16-17 (pictogram captions). The prose around them (p11-15) holds the
only numbers. "None" below means the paper states none.

| Use case (caption) | Paper's statement | Stated efficiency / size / load / cost / CO2 | p |
|---|---|---|---|
| Electrical steam boilers and hot oil | "Electrify utilities generation with high-efficiency electrical boilers and heat pumps"; electric steam boilers "have a very fast response time and can quickly be turned on and off if necessary to balance supply"; offshore oil and gas already use "electric boilers instead of gas-fired boilers" | Efficiency: none for boilers or heat pumps. Direct electric heating "overall efficiency in the range of 95-99%" (fn19 SE internal data). No size, COP, cost. | 11, 14, 16, 13 |
| Electrical process heaters | "Decarbonize fossil-fueled process heaters with electrical technologies"; "Now it is possible to electrify at a larger scale. Equipment, such as fired heaters, steam boilers, distillation reboilers, and hot oil heaters ... can now be replaced" | "can increase energy efficiency by more than 20% compared with traditional gas-fired heaters" (fn16 cites an IEA Electrification web page, which is generic, not this figure). Combustion thermal losses "~20%" (fn19 SE). "high operability ranges close to 0-100%" for "electrical heaters and boilers". No size, no max temperature, no reference plant. | 11, 12, 13, 17 |
| Distillation reboilers | "Replace steam or fired heater reboilers with electrical technology" | None. | 17 |
| Tank heating | "From typically unmonitored or uncontrolled legally hot oil or steam coils to electrical tank heaters" | None. | 16 |
| Electrical heat tracing | "Typically low-efficiency & high maintenance-cost steam tracing replaced with fully remote and controllable electrical tracing"; "has progressively replaced legacy steam tracing" | None. Already deployed at small scale. | 11, 16 |
| Small-scale electric heaters | for "catalysts or adsorbents regeneration, gas, or lube oil skid heating" | None. Already deployed. | 11 |
| Steam turbines electrification and vacuum pumps | steam turbines and ejectors replaced by "motors and pumps"; "condensing type steam turbine is much less energy efficient (sometimes as low as 25% energy efficient) compared to an electric motor with variable speed drive (around 90-95% energy efficient)"; also "Energy-efficient electric motors (~95% energy efficient)" vs "~25%" | 25 % vs 90-95 % (no footnote on p11; p12 repeats it). No motor size, no MW. See the evaluation for why this comparison is not like for like. | 11, 12, 17 |
| Green H2 electrolysers | "Replace traditional fossil-based H2 (SMR or other) by green powered electrolyzers"; electrolysis "less energy efficient than process electrification (~70%)" | Efficiency ~70 % (no footnote on p13). Needs "a large amount of electrical power" (p8). No MW, no cost. | 13, 17, 8 |
| Energy storage | "Thermal or Electrical batteries for intermittent or low-price energy storage and release"; "Batteries or thermal energy storage technologies can also be used to store and release energy that has been intermittently generated or cheap electricity to generate steam" | None. | 14, 16 |
| Incorporated renewables | "Solar, wind, PPA and others"; on-site or via PPA | None. | 8, 14, 17 |
| Heat pumps (inside the boiler use case) | Chemical plants "are replacing steam boilers with heat pumps" (other industries as precedent) | None: no COP, no source or sink temperature. | 11, 16 |

**Emission reduction from electrification:** not quantified for any use case. The only statement is that
electrification "produces zero direct emissions at the point of use" (p12), conditional on low-carbon power
(p11, p14). CCS is described as "still at an industrial pilot scale" (p13, paper; no footnote), which is
contestable and would need a primary check before anyone repeats it.

**Cost claims (all unsupported):**

- "Decarbonizing using direct process electrification can be faster to implement and more cost-effective
  than other decarbonization options" (p13; paper, no figure).
- "process electrification has emerged as an effective, cost-efficient way" (p20; fn21 is a generic IEA
  page).
- Maintenance: "longer mean time between failures and shorter mean time to repair" (p12; fn18 SE internal
  data).
- The one cost-adjacent fact is the energy share of EU refinery cash operating costs, about 60 % (p12,
  Concawe 2012).

## 5. Grid and power-system challenges, and best practices

### 5.1 What the paper says (p14-15, p19)

- **Connection and supply capacity.** Raising electrification from the current 5-10 % to 20 % "will mean
  more than doubling electrical consumption. This could have consequences for the electrical supply and
  distribution (e.g., capex on upgrading or extending the existing power system)" (p14-15). No MW, no
  voltage level, no connection-time or cost figure.
- **Sourcing.** Power can be "internally generated or purchased and integrated via a PPA" (p14); electricity
  may be "purchased over the fence ... or internally generated" (p4); renewables "generated on site or
  purchased via a power purchase agreement" (p8). Grid utilities and governments must decarbonise the
  supply side too (p14).
- **Flexibility and price.** New loads "can, in some cases, participate in various grid flexibility or
  stability mechanisms"; electric steam boilers can be switched "quickly"; grid prices "can even be
  negative" and refineries "could benefit from cheap electricity to generate steam or to lower the electrical
  demand ... whenever required by the grid utility in a flexibility mechanism" (p14). Batteries or thermal
  storage can bridge intermittency (p14, p8). Heaters keep efficiency at reduced load, unlike fired heaters
  with "low turndown" (p12).
- **Process-side couplings the paper does name:** the fuel gas balance (electrifying reduces fuel gas use;
  gas import falls, then excess fuel gas may need export or H2/LPG recovery; p14) and the steam balance
  (fired heaters' waste-heat boilers stop making steam; gas boilers go to turndown or stop; header pressure
  levels must be rebalanced; p15).
- **Reliability and power quality:** the paper never mentions harmonics, voltage dips, flicker, motor
  starting, fault level, or ride-through. The only reliability text is "design a robust and optimized
  electrical distribution system that properly integrates the new electrical loads" (p19) and "ensure
  stability and reliability of the power system" through unified power and process simulation (p19).

### 5.2 Best practices (p19; paper)

1. Assess the processes' and assets' electrification potential in depth, with a "value-stacked approach"
   (throughput or constraint removal, control accuracy, flexibility, lower safety risk, subsidies).
2. Evaluate the impact on existing infrastructure (process, fuel gas and utilities, electrical, digital and
   control systems) and on transition paths (low-carbon products, energy or biofuels hub).
3. Ensure integrity and operability: a robust electrical distribution that integrates the new loads, and
   headroom for future upgrades.
4. Train personnel and monitor performance; unify power and process simulation; upgrade the digital
   system for power-system monitoring.
5. Work with "heavy industry experts" and "trusted partners" (the vendor's own offer).

Item 4 ("unifying power and process simulations") is the same idea as the spec's grid-to-process concept;
the paper offers it as a principle, not a method.

## 6. Evaluation of the paper's proposals

Ratings are mine, separate from the paper's claims: **well-supported**, **plausible**, **weak**. "Weak"
means the paper's support is thin, not that the technology fails. Statements about the wider state of the
art are from general engineering knowledge, not from the paper or the repo, and carry the label "to verify"
where a number matters; section 7 lists the primary sources that would settle them.

| Proposal | Maturity and temperature reach | Efficiency and cost claim vs. what is known | Grid impact | Depends on | Rating and reason |
|---|---|---|---|---|---|
| Electric heat tracing and tank heating (p11, p16) | Mature, commercial. Low band (roughly tens to a few hundred degC). | Replaces steam tracing; no number given. Believable because the loads are small and the steam losses are high. | Small (kW to low MW in total), steady, little flexibility. | Little; local power supply. | **Well-supported.** Already deployed (p11), low risk, but small in refinery energy terms. |
| Electric steam boilers (electrode or resistive) and electric hot-oil heaters (p11, p14, p16) | Commercial technology; steam to medium and high pressure, hot oil to a few hundred degC (to verify). Serves steam-header duties, not furnace duties above its pressure/temperature rating. | Paper's 95-99 % (p13, SE internal) is the right order for resistive heat to the fluid. The paper never compares the cost per MWh_th of electricity against gas plus carbon price; without it "cost-effective" (p13) is unsupported. | Large, sized to the steam it replaces: the paper's 700-900 MW thermal (p4) is the full refinery heat; an e-boiler for a header of tens of MW is a step load of that size. Very fast ramp (p14) and low minimum load make it the most flexible new load: fits curtailment and negative-price hours (p14). | Power price relative to gas plus CO2 (the paper cites ETS above 85 EUR/t, p6); a grid connection of tens of MW; the steam balance (p15). Retained gas boilers as backup is the usual hybrid (inference). | **Well-supported** as technology and as a flexible load. The economics are **weak** in the paper (no cost data). |
| Heat pumps for steam / low-grade heat (p11, p16) | Commercial for low-temperature heat; high-temperature (above about 150 degC) industrial heat pumps are demonstration to early commercial (to verify against IEA and Arpagaus et al.). Reach is the low band only; no heat pump replaces a crude-unit or cracker furnace. | The paper gives no COP. COP falls steeply with temperature lift, and the paper's single sentence "chemical plants are replacing steam boilers with heat pumps" (p11) is unreferenced. | Electricity input is heat / COP, so a few times lower MW than a boiler for the same duty, with a profile that follows the heat demand and the source temperature. | A heat source (a refinery has plenty of low-grade waste heat, which also competes with the waste-heat recovery strategy the paper lists on p8); sink temperature; steam balance. | **Plausible** for low-pressure steam and reboilers; **weak** as presented (no performance data, no temperature reach). |
| Electric process heaters replacing fired heaters, and electric reboilers (p11, p12, p17) | Electric heating of process fluid is established for small duties (p11 itself says so); at the scale of a crude-unit or reformer heater (tens of MW, high outlet temperature, coking tendency) it is early commercial or demonstration (to verify). Above several hundred degC (reformers, steam crackers) it is demonstration. | "more than 20 % more efficient" (p12) compares electric delivery at the heater with the gas heater's flue-gas loss; it ignores generation and grid losses, and the fn16 source does not state it. Fair in direct-use terms, misleading as an energy-system claim. | Large: heater duty converts one-for-one to MW of load (a 150 kbbl/d refinery has about 20 heaters, p5), at near-constant load while the unit runs, and a trip drops the process. | Process redesign (heater type, coke-prone fluids), firm high-voltage supply, steam balance when the convection-section waste-heat boiler goes (p15). | **Plausible** below roughly 400 degC and for small duties; **weak** above, where the paper offers no evidence at all (the use case is a pictogram caption, p17). |
| Steam turbines and ejectors replaced by motors, VSDs and vacuum pumps (p11, p12, p17) | Mature, fully commercial; large VFD-driven compressors and motors exist. No temperature issue. | The 25 % (condensing turbine) versus 90-95 % (motor with VSD) (p11, p12) compares shaft efficiency at the machine; a system view must include the electricity's own generation efficiency and that the turbine exhaust often serves as process heat (the paper itself flags the steam balance, p15). So the direction is right, the magnitude is overstated. | Adds large motor load (several MW per machine, my estimate), with start-up current and harmonic questions the paper does not mention; flexibility is limited because compressors are process-critical. | Spare steam handled in the header (p15); power-quality and motor-start capability of the grid at the point of connection. | **Well-supported** technically; **plausible** on the efficiency number. |
| Green H2 electrolysers replacing SMR hydrogen (p13, p17) | Mature at tens of MW per stack module; large-scale (hundreds of MW) projects are being built. Refinery hydrogen supply is a direct fit (p6: hydrogen 20.6 % of CO2). | "~70 %" (p13) is in line with 0.62 LHV in the repo's own pack row (see section 6a below); no capex, no electricity-price breakeven given. | Very large and continuous: as an illustration, 50 kt H2/yr at the repo's 53.6 MWh_el per t H2 (33.33 / 0.6217) is about 2.7 TWh/yr, roughly 305 MW round the clock. That is more than the whole refinery's 50 MW of electricity (p4, 6x). Flexible within H2 storage limits. | Cheap low-carbon power in large volumes; grid connection of hundreds of MW; H2 buffer. | **Well-supported** as technology; the paper offers no cost, so economic merit is **weak**. The paper is honest that it needs "a large amount of electrical power" (p8). |
| Thermal or electrical batteries (p14, p16) | Heat storage and batteries are commercial at modest temperatures; high-temperature "thermal batteries" are early commercial (to verify). | None given. | Shifts load to cheap or negative-price hours (p14), which is the grid flexibility the paper sells. | Price spreads that pay for the storage; a steam or heat bus to discharge into. | **Plausible**; no support offered. |
| Renewables and PPAs (p8, p14, p17) | Mature. | None given. | PPA is a financial claim; physical delivery still needs the grid connection. | Local resource and connection. | **Well-supported** as a decarbonisation principle; weak as a statement about the electrified load's real carbon intensity (the paper correctly says zero direct emissions only for low-carbon power, p11). |
| Overall claim: electrification is "one of the best ways" and "cost-efficient" (p12, p20) | n/a | The paper compares only against green H2 and CCS (p13), which are the options that SE does not sell, and not against fuel switching, biomass or efficiency. | n/a | n/a | **Weak** as a general claim: unreferenced, selective comparators. |

**Vendor optimism, in brief:** system-level efficiency gains are quoted at the point of use (95-99 %, 90-95 %,
more than 20 %); costs, power prices and capacity charges are absent; the grid is treated as an enabler
("must decarbonise too", p14) and not as a constraint with a lead time and a cost.

## 6a. Modelling gap analysis: what the app can model today versus what is needed

Read-only, on `origin/master` `3660dc810`. Paths are under `pypsa-gui/backend/` unless stated.
"Not found" says where I looked. Findings are about the codebase, not about the paper.

### 6a.1 What exists, in one paragraph

The engine is plain PyPSA, so any heat or steam system is expressible as buses, Links (multi-port, with
`bus2..bus4` and `efficiency2..4`), Stores and Loads. The grid-to-process tickets 01-06 and 08 are done,
so a committable, multi-input process unit with products, inventories, a lost-production slack and
criticality tiers exists. There is no heat-specific modelling layer: heat is an ordinary carrier, and the
library holds no heat equipment.

### 6a.2 Per solution

| Solution / use case | Verdict | How, or what is missing (file pointer) |
|---|---|---|
| Heat demand as a Load; hybrid gas + electric supply | **Partly** | A Load with carrier `heat` (`services/asset_schema/fields.py:324`; the `heat` carrier is in `services/carrier_catalog.py:47-49`) and parallel Links from `gas` and from electricity into one heat bus is plain PyPSA; no template does it. Missing: heat demand by temperature band and pressure level (not found: grep for `temperature_c`, `temp_band`, `process_heat`, `steam_pressure` over `pypsa-gui/backend`). The only heat-demand generator is a building-heating one (`services/profile_gen/load_archetypes.py:293`, `_heat`, base-temperature degree-hours). |
| E-boiler / electrode boiler | **Partly** | A Link electricity to heat, efficiency near 0.99, `p_min_pu`, ramp limits and optional commitment (`asset_schema/fields.py:339-387`; `committable` rows at `fields.py:163-175`). Missing: a catalogue item, pack costs (see below), steam-pressure semantics, a turndown rule. `ThermalFlexSpec` declares `e_boiler_mw` (`models/flex_archetypes.py:140-145`) but nothing reads it: only a contract test references it (`tests/test_investment_case_contracts.py:332`). |
| High-temperature heat pump, COP vs temperature | **Partly** | A Link with bus2 as the low-grade source (`efficiency2 = -(COP-1)`), shown in `scripts/scaffold_dc_heatpump.py:1-35`. COP as an hourly efficiency series from ambient or ground temperature, Carnot-fraction form η·T_sink/(T_sink−T_source), clipped to [1, cop_max]: `services/profile_gen/series.py:18` and `:220` (`cop`, targets links `efficiency` at `:264`). Missing: a sink temperature that varies by duty, a lift-dependent COP for a process (the series uses a constant `sink_c`, default 45 degC, `series.py:61`), and part-load: `part_load` is refused (`series.py:47`, `:273`, "needs a piecewise or committable formulation"). |
| Thermal storage | **Can model** | A Store on a `heat` bus with energy capacity and `standing_loss` (`asset_schema/fields.py:277-313`; extendable via `e_nom_extendable`, `fields.py:286`). Not a template, no pack costs. Charge/discharge temperature limits are not modelled. |
| Electric furnaces / process heaters / crackers | **Partly** | Same as the e-boiler (a Link to a heat bus or, as a process unit, to a product bus: `eh_process`, `fields.py:348`). Process unit in tonnes works (`services/carrier_catalog.py:50-55`, `models/process_unit.py`). Missing: heat as a delivered product with a temperature, furnace-specific parts (radiant efficiency, coking, run length), and a refinery or cracker template. Design defers it: `.scratch/grid-to-process/design.md` section 2.6 "Heat and steam carriers are later" and section 7 (not ticketed). |
| Steam network (pressure levels, header balance, turbines, letdown) | **Cannot** | Not found: grep of `heat|steam|boiler|furnace|temperature` over `pypsa-gui/backend` and `gridspine` returned no steam or pressure-level model. It could be built from buses (one per header) and Links (letdown, boiler, turbine), but there is no support, no template and no validator. |
| Fuel gas balance | **Cannot** | Not found (same grep). A fuel gas bus with produced and imported gas is plain PyPSA, but nothing in the app, the catalogue or the results knows it. |
| Electric drives replacing steam turbines (compressors, vacuum pumps) | **Partly** | As a Link or Load (electricity in, mechanical or process service out) with `criticality_tier` (`fields.py:350-355`; `models/process_unit.py`). Missing: a drive as a thing (motor kW, efficiency curve, start method; design issue 12 adds motor kW, start method and ride-through curve, still needs-triage), and the steam released into the header. |
| Hydrogen electrolysis for hydrotreating | **Can model** | The staged H2 hub has an `electrolyser` Link (`project_templates/eh_templates.py:689-690`, efficiency 0.68 demo), an H2 store, and the ammonia template adds a committable Haber-Bosch process unit (`project_templates/eh_ammonia.py:9`; `eh_staged_hubs.py:136`, `build_eh_h2_hub_staged`). Pack rows for electrolysis (1886 EUR/kW_e, 25 y, efficiency 0.6217), `h2-tank`, `haber-bosch`, `nh3-tank` are in `services/library/defaults_pack/versions/2026-10-23/values.csv`. Missing: a refinery H2 demand shape and the SMR counterfactual with its own fuel and CO2 (the grey-H2 counterfactual exists for NH3: `ReferencePurchase`, design section 3.A). |
| Grid connection sizing for a large new MW load | **Can model** | Connection agreements (firm, non-firm static/dynamic, FCA) with fee in the LP (`services/commercial/connection.py:1-40`); staged energisation in MW (`services/adequacy/energization.py:93`, `apply_energization`); a bus-capacity study "how many MW can connect here" with N-1 (`gridspine/static/capacity.py:1-25`, `gridspine/static/contingency.py`); a site-peak check against connection plus gensets (`services/adequacy/site_peak.py:306`, `peak_check`). |
| Short-circuit and voltage strength at the PoC | **Can model** | IEC 60909 fault levels (`gridspine/static/shortcircuit.py:1`), SCR at inverter buses (`gridspine/static/strength.py:1`), an SCR gate and grid-readiness scorecard (`services/adequacy/scr_gate.py:142`; `services/adequacy/grid_readiness.py:236`). The rapid-voltage-change row is built only for the AI-training load envelope (`grid_readiness.py:442-465`, `_NO_ENVELOPE`), not for an arbitrary process load step. |
| Motor start / inrush on a weak grid | **Cannot (planned)** | Not found: grep for `motor start`, `inrush` over `gridspine` and `pypsa-gui/backend` (non-test) found nothing. Planned in `.scratch/grid-to-process/issues/12-minutes-scale-motor-start-restart.md` (needs-triage). |
| Harmonics / power quality of large drives and heaters | **Cannot** | Not found: the only hits are statements that studies are steady-state, "no dynamics, no harmonics" (`gridspine/static/campus_invest.py:182`, `campus_milp.py:194`). No issue covers harmonics, flicker or thyristor-heater control. |
| Reliability / on-site generation / island | **Can model** | Island frequency model (`gridspine/static/frequency.py:1`), island sidecar and pickup blocks, genset N+k and ride-through requirements in the adequacy layer (`services/adequacy/island_config.py`), Monte Carlo adequacy; outage to tonnes via `services/adequacy/process_loss.py:111` (`outage_loss`). |
| Flexibility / demand response of electrified loads | **Partly** | Inventory-backed flexibility is modelled: process unit plus Store plus delivery Load plus lost-production slack (`services/solver/lost_production.py`), tiers `process_flexible` and `deferrable` (`models/process_unit.py`), tariffs and curtailable connections in the LP. DSR exists as a priced slack that curtails and does not shift (`services/adequacy/slack.py:39`, per design ticket 08 "What exists"). Missing: ancillary-service products and shifting DR inside the solve; design section 7, "P4 (later)", not ticketed. |
| Costs (capex, fixed O&M, lifetime) for heat equipment | **Cannot** | The defaults pack has no row for an electric boiler, heat pump, process heater, furnace, thermal store, steam turbine or motor/VFD. Technologies in `values.csv` (2026-10-23): battery, cables, capacitor banks, diesel and gas gensets, electrolysis, fuel cell, `h2-tank`, `haber-bosch`, `nh3-tank`, onshore wind, solar, shunt reactors, STATCOM, switchgear, transformers. The other library source `services/library/reference_data/eu-dea-2026-08.json` was searched for heat pump, boiler and thermal storage: nothing found. |
| Fuel-switch / avoided-cost accounting (gas saved, ETS cost avoided, steam displaced) | **Partly** | Gas and CO2 are priced in the LP through carrier emissions (`services/carrier_catalog.py:41`) and the investment case compares against a counterfactual. Missing: a counterfactual that is "keep the fired heater" (the existing counterfactuals are "buy grey NH3" and "re-solve the baseline", design sections 3.A and 3.B). |

### 6a.3 Gaps in priority order

Priority is by how much of the paper's use-case list each gap blocks, for a first refinery-electrification
example. Size: S up to 1 day, M 2-3 days, L 4+ days of agent work (the design's own scale).

| # | Gap | What it would take | Size | Covered by |
|---|---|---|---|---|
| 1 | **Heat as a modelled product: demand by temperature band and pressure level, `steam`/`heat` carriers with a unit and a level, and the retained fired-heater / gas-boiler supply as a hybrid.** Without it no refinery use case is expressible beyond a single undifferentiated heat bus. | Carriers with a level attribute; a heat-demand input (band table) and its load shape; a template with gas and electric supply on each level; validator rules (a heat pump may not serve a band above its reach). | M-L | **No issue.** `.scratch/grid-to-process/design.md` sections 2.6 and 7 list heat and steam carriers as "later". Needs its own issue. |
| 2 | **Library data for the electrified technologies:** e-boiler, high-temperature heat pump, electric heater or furnace, thermal storage, VFD/motor, with capex, fixed O&M, lifetime, efficiency and an operating range. | Source rows (technology-data, DEA) into a new defaults-pack version with provenance and ranges, as ticket 05 did for Haber-Bosch (`issues/05-a-tracer-ammonia-template.md`). | S-M | **No issue.** Closest: ticket 05 as a pattern. |
| 3 | **Large-drive and heater grid behaviour:** motor start ΔU, restart sequence, and harmonic / flicker screening for drives and phase-angle-controlled heaters. | Motor start: ticket 12 as written. Harmonics and flicker: a new static check beside `gridspine/static/strength.py`, with inputs per unit (rated MVA, drive topology, harmonic spectrum). | M (motor start) + L (harmonics) | Motor start: `issues/12-minutes-scale-motor-start-restart.md`. **Harmonics and flicker: no issue.** |
| 4 | **Steam and fuel gas balances at the refinery level:** header pressures, turbine-to-motor swaps freeing steam, waste-heat boiler loss when a heater is electrified, fuel gas surplus (the paper's p14-15 couplings). | A steam-bus pattern (buses per header, letdown and turbine Links), a fuel gas bus with produced and imported gas, a balance report, and an issue for the "keep the fired heater" counterfactual. | L | **No issue.** Design section 7 defers heat and steam; nothing on fuel gas. |
| 5 | **Heat-pump performance for process duties:** COP as a function of the sink temperature of the served band and the source (the series generator already does Carnot-fraction COP at a fixed sink), plus part-load and minimum-load behaviour. | Extend `services/profile_gen/series.py` (`cop`) with a per-duty sink temperature; decide on a piecewise or committable part-load formulation (currently refused at `series.py:273`). | S-M | **No issue.** Ticket 03 (`issues/03-solve-path-committable-links.md`) built the committable-Link path this would reuse. |
| 6 | **Process-load rapid-voltage-change and ride-through for non-data-centre loads.** The RVC row needs an AI-training envelope. | Generalise `grid_readiness._rvc_row` to take the largest step of any process-load profile or a unit's start. | S-M | Partly: `issues/12` and `issues/13-coupling-report-grid-to-tonnes.md` (ride-through to tonnes). |
| 7 | **Ancillary and shifting DR in the solve** for electrified loads (e-boiler as a frequency or reserve resource). | Design "P4"; needs the #332 island code to settle. | L | Design section 7 "P4" (not ticketed). |

## 7. What this means for grid-to-process

### 7.1 Spec questions and issue files this informs

- **Context and motivation for an "electrified refinery or petrochemical" example (design section 3,
  currently A ammonia and B concentrator).** The paper gives a plausible third example family: refinery heat
  and drive electrification. It confirms the problem statement (heat is the bulk of the energy, p4; the grid
  is the constraint, p14) but supplies no model data.
- **Design section 2.6 ("Heat and steam carriers are later") and section 7.** The paper's use cases are
  nearly all heat and steam, so the gap analysis (6a) shows that deferral is what limits a refinery example.
  This is the main input for deciding whether to open a heat/steam issue after A and B.
- **Design section 5 (grid result to process cost) and `issues/13-coupling-report-grid-to-tonnes.md`.**
  The paper's steam and fuel gas balance couplings (p14-15) are the process-side counterpart: they say a trip
  or a curtailment has knock-on effects on the steam header that a tonnes-only view misses.
- **Design section 4.3 and `issues/12-minutes-scale-motor-start-restart.md`.** The paper lists large drives
  (turbine-to-motor swaps, p11-12, p17) but never mentions motor starting or dips. It is a reminder that
  vendor material does not cover what ticket 12 must check.
- **`issues/05-a-tracer-ammonia-template.md` and `issues/06-a-investment-case-grey-nh3.md`.** The
  hydrogen side of refining (SMR hydrogen as about 20 % of direct CO2, p5-6) is the same
  electrolyser-plus-counterfactual structure; an electrolyser efficiency of about 70 % (p13) matches the
  pack's 0.6217 LHV order of magnitude, but it is an unreferenced SE number and must not enter the ledger as
  `sourced`.
- **`issues/08-p2-criticality-tiers-lost-production.md`.** The paper's flexibility argument (e-boilers on and
  off, p14; heaters 0-100 %, p12) maps to `process_flexible` / `deferrable`, while compressor and heater
  drives are `process_critical`. A tier assignment per use case would be a reasonable test fixture, labelled
  demo.
- **`issues/09-p3-chronological-year-time-base.md`.** Negative-price and curtailment hours (p14) are the
  reason steam or heat storage is valued; a representative week would not value multi-day heat storage the
  way a chronological year does (design section 2.4).

### 7.2 What it does NOT answer

- Process heat demand by temperature band, steam pressure levels, or any split of the 700-900 MW thermal.
- Which fraction of refinery heat is technically electrifiable at each temperature (no reach for heat pumps,
  boilers or heaters).
- Any COP, efficiency curve, minimum load, ramp rate or part-load behaviour of any device.
- Any capex, opex, lifetime, retrofit cost or abatement cost; any electricity or gas price needed for a
  breakeven.
- Sizes: MW per boiler, heater or drive; total added MW for a real refinery (the "more than doubling" at
  20 % electrification, p14-15, is the only statement).
- Connection capacity, voltage level, reinforcement cost or timeline; power quality; reliability targets;
  restart times after trips.
- Emission reductions per measure.
- Anything on petrochemicals (crackers, aromatics, polymer lines) beyond their being mentioned (p4).

### 7.3 Suggested follow-up sources (primary only)

- **EU BREF for the Refining of Mineral Oil and Gas** (JRC, 2015, and the 2025 revision) for energy and CO2
  by process unit, fired heater and utility data, and BAT-associated levels. Replaces SE's p4-5 shares.
- **Concawe reports** (the paper's own fn4 and fn17: Rpt 19/8 "CO2 reduction technologies" and the EU refinery
  energy systems report) read at source for the CO2 by unit and the 60 % energy cost share.
- **IEA:** "Electrification" and "Energy efficiency" tracking pages the paper cites (fn15, fn16, fn21); and
  the IEA's industrial process-heat analysis, which breaks demand into temperature bands and gives the
  technology reach table the paper lacks.
- **EU Joint Research Centre / EU Commission** studies on industrial heat pumps and process heat by
  temperature (e.g. the "heat roadmap" and JRC industrial heat demand data), to fill the band table.
- **Industrial heat pump performance:** the Arpagaus et al. review of high-temperature heat pumps (an
  academic primary), and the Danish Energy Agency technology catalogue (also feeds PyPSA technology-data)
  for the e-boiler, heat pump and thermal storage cost rows needed by gap 2.
- **PyPSA technology-data** (`costs_*.csv`) for electrolysis, heat pump, resistive heater and thermal
  storage rows already tagged with sources, to extend the defaults pack.
- **Grid-side:** the relevant IEC technical reports for motor-start and rapid voltage change (IEC TR
  61000-3-7 and 61000-3-6 for flicker and harmonic emission limits) and the operator's grid code, for
  ticket 12 and for gap 3.
- **Primary project evidence** for electrified furnaces and e-boilers: the operator or licensor reports of
  the demonstration plants (for example the e-cracker demonstrations by chemical majors) and the Danish and
  Dutch industrial e-boiler connection cases, for real MW sizes, connection costs and operating hours.

### 7.4 Caveats for anyone citing this note

- Every SE figure above is "paper's claim"; do not label any of it `sourced` in a template ledger without
  the primary source behind it. Where the paper cites a third party, the third party is the source and SE is
  a pointer.
- The figures marked "my arithmetic" (the 1.1-1.5 Mt cross-check, the 305 MW electrolyser illustration, the
  5.3-6.7 % electricity share) are mine, built on the paper's inputs and on the repo's carrier and pack
  rows; the 50 kt H2/yr is an illustration, not a refinery fact.
- Statements marked "to verify" in section 6 are general engineering knowledge not sourced in this note.
- I did not follow any link in the PDF or run anything from it.
