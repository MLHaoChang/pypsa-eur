<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Heat as a modelled product: temperature bands and pressure levels

Status: ready-for-agent (triage 2026-10-28: owner Q15 answered, wait for issue 10; gatekeeper triage of 2026-10-23 stands; gated, plan to GK A first)
Type: task
Blocked by: 10 (owner Q15); 02 and 03 are done
Gated: **yes** (schema and validator: send the gatekeeper the plan first)
Model tier: opus
Size: M–L

Read [the design](../design.md) §2.6 and §7 first, then the
[research note](../research/se-electrifying-refining-desw024.md) §6a (gap 1, plus the heat rows of
6a.2). This is a **design change to §2.6**: the design says "Heat and steam carriers are later" and
lists them again in §7 (not ticketed). Opening this ticket moves them from "later" to "planned".
The gatekeeper must agree to that edit of the design before work starts.

## Why

The research note shows that nearly every refinery use case (e-boiler, heat pump, electric heater,
hybrid with the retained gas boiler) is heat or steam, and heat in the app is one undifferentiated
`heat` carrier (`services/carrier_catalog.py:47-49`). A process heat load has a temperature or
steam pressure, and that decides which device can serve it. Without the level, a heat pump and a
furnace look interchangeable and no refinery example can be expressed (note §6a.3, gap 1, rated
first, size M–L). Nothing in the backend names a temperature band or pressure level (the note's
grep for `temperature_c`, `temp_band`, `process_heat`, `steam_pressure` found nothing).

## What to build

1. **Carriers with a level.** `heat` and `steam` carriers get a `level` attribute: a temperature
   band (for example low / medium / high, edges in the ledger) or a steam pressure class. One bus
   per level. The unit stays MWh (LHV-style thermal), as the catalogue counts energy.
2. **Band-wise heat demand input.** A table: band, annual or peak MW_th, and a load shape. The
   shape is flat while the unit runs, or taken from a profile. Reuse the profile generator;
   `load_archetypes._heat` (`services/profile_gen/load_archetypes.py:293`) is a building-heating
   shape and does not fit process heat.
3. **Hybrid supply per level.** A template that puts a gas Link and an electric Link (e-boiler, heat
   pump, heater) in parallel onto each level's bus. Keep the fired heater or gas boiler as backup.
   Use the committable-Link path (issue 03) for minimum load and start-up.
4. **Validator rules** (errors, not warnings):
   - A heat pump may not serve a band above its reach (reach is a field on the device, in the
     ledger).
   - A device may not feed a level it cannot reach (a boiler rated for 10 bar on a 40 bar header).
   - Every level with demand has at least one supplier.
5. **Fixture values are labelled `demo`.** The paper's own numbers are not `sourced`
   (note §7.4).

## Acceptance (stated first)

- A two-level network (low band: heat pump plus gas boiler; high band: gas furnace plus electric
  heater) solves, and each level's balance closes.
- A heat pump wired to a band above its reach fails validation with a message naming the band, the
  device and its reach.
- With the electric Link priced above gas, the gas Link serves the demand; with a negative-price
  hour, the electric Link takes it. Both are asserted.
- A network with no `level` set behaves as today (existing tests unchanged).

## File pointers (checked on origin/master)

- `services/carrier_catalog.py:47-49` (`heat` entry), `:50-55` (process products with a `unit`).
- `services/asset_schema/fields.py:324` (carrier field on a Load), `:339-387` (heat Link fields),
  `:163-175` (the `committable` rows).
- `models/process_unit.py` (process unit), `models/flex_archetypes.py:140-145` (`e_boiler_mw`,
  declared and read by nothing).
- `.scratch/grid-to-process/design.md` §2.6 (:280-288), §7 (:686-692).

## Later / out of scope

- **Steam and fuel-gas balance** (note §6a.3, gap 4): header balance, letdown and turbine Links,
  steam freed by a turbine-to-motor swap, waste-heat boiler loss when a heater is electrified,
  fuel-gas surplus, and the "keep the fired heater" counterfactual. Size L. It needs this ticket
  first and gets its own issue later. Do not add it here.
- Library costs for heat equipment (note gap 2), thermal-store temperature limits, ancillary DR.

## Open questions

- Gatekeeper: is the `level` a free-text tag, an enum of bands, or a number (degC or bar)? Numbers
  are more general but need units.
- Gatekeeper: is a new schema field on `Bus` / `Load` acceptable, or must the level live in the
  carrier name (`heat_low`)?
- Owner: which refinery example comes first, and should this wait until A and B (issues 05, 10)
  are done?

## Dependencies

- Blocked by 02 (schema and carriers) and 03 (committable Links), both done.
- Blocks issue 16 (heat-pump COP by band).

## Triage (gatekeeper, 2026-10-23)

**Status `needs-info`:** one owner question is open (below). Once it is answered this is
`ready-for-agent`, and still gated: send the gatekeeper the plan before writing code.

**The design change is agreed.** This ticket moves "heat and steam carriers" in §2.6 and §7 from
"later" to "planned" for the **level** only. The PR edits §2.6 and §7 to say so. HB waste heat,
CO2 as a product and the steam/fuel-gas balance stay in §7.

Answers to the gatekeeper questions:

- **The level is a number with its unit in the field name, not an enum or a free tag.** Heat buses
  get `temperature_c` (the supply temperature the bus delivers at). Steam buses get `pressure_bar`
  (header pressure, gauge). The bands (low / medium / high) are display labels derived from edges in
  the ledger; they are never stored. A free tag cannot be validated, and an enum would freeze the
  edges.
- **A new Bus field is acceptable; a Load field is not needed.** `unit` is already a Bus field set
  for process products (`services/asset_schema/fields.py:487`, read in
  `services/carrier_catalog.py:75`), so the level follows that precedent. A Load serves the level
  of the bus it sits on. Do not encode the level in the carrier name (`heat_low`). The heat-bus
  pickers match any carrier containing "heat" (`carrier_catalog.py:46-47`), so a name scheme would
  be both fragile and invisible to the validator. Add a `steam` catalogue entry beside `heat`.
- **Device reach is a Link field in the same units:** `max_supply_temp_c` for heat devices,
  `max_pressure_bar` for steam devices, sourced or `demo` in the ledger. The validator compares
  numbers: the reach must be at least the level of the bus the Link feeds (`bus1`, and `bus2` for a
  heat pump's heat output where that is the heat side). An unset level on either side skips the
  rule and the network behaves as today. That keeps acceptance item 4 true.

Owner question (send it as these options):

- **Which example comes first, and does this wait for B?**
  - *(Recommended)* Do not wait. Build the generic two-level fixture from the acceptance (no
    refinery template), because A (05) is done and B (10, the concentrator) shares no heat code. A
    refinery template is its own later issue.
  - Wait for B (10) to land first. This delays heat by B's whole run for no shared code.
  - Start with a named refinery template (for example the paper's crude-unit heater). This needs
    sourced refinery data first, or the template ships as `demo` values that look like a real
    plant.

## Triage (2026-10-28)

**Verified on master (41ade3be):** not built. No `temperature_c`, `pressure_bar` or `max_supply_temp_c` exists in the backend.
- The owner answered Q15 on 2026-10-23: wait for issue 10.
- The gatekeeper's design answers above (numeric level on the Bus, reach as a Link field, a `steam` catalogue entry, unset means as today) stand and are the contract.

This is now `ready-for-agent`, blocked by 10, and gated: send GK A the plan, including the §2.6 and §7 design edit, before code. The brief is the sections above: "What to build" as amended by "Triage (gatekeeper, 2026-10-23)", "Acceptance", and "Later / out of scope".

**Likely files:** `pypsa-gui/backend/services/carrier_catalog.py`, `pypsa-gui/backend/services/asset_schema/fields.py`, `pypsa-gui/backend/services/validation_service.py`, `pypsa-gui/backend/models/process_unit.py`, `pypsa-gui/backend/services/profile_gen/load_archetypes.py` (read only), `.scratch/grid-to-process/design.md` (§2.6, §7), new tests under `pypsa-gui/backend/tests/`.
**Blocked by:** 10 (owner Q15). This blocks 16.
**Model:** Opus (schema and validator, gated).
