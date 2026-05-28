// Hover tooltips for the right Properties Panel.
//
// Keys are `<component>.<field>` where <component> matches the asset type the
// field belongs to (`bus`, `line`, `generator`, `storage_unit`, `store`,
// `load`, `link`) and <field> is the PyPSA attribute name.
//
// Descriptions are intentionally short (1–2 sentences). The primary audience
// is a power-systems engineer using the GUI, so we use PyPSA's terminology
// rather than re-explaining basics.

export const PROPERTY_DOCS: Record<string, string> = {
  // ── Bus ──────────────────────────────────────────────────────────────────
  'bus.name':
    'Unique identifier for the bus. Must be unique across the network and is used as foreign key by every component connected to it.',
  'bus.v_nom':
    'Nominal voltage of the bus in kV. Lines are coloured by voltage class on the canvas; AC topology and transformer ratios reference this value.',
  'bus.carrier':
    'Energy carrier modelled at this bus (AC / DC / H₂ / heat / gas / …). Determines which assets can attach and how the optimisation handles its energy balance.',
  'bus.control':
    'Power-flow control mode for non-linear AC solves: PQ (load), PV (regulated voltage), or Slack (reference). For linear OPF only the slack matters.',
  'bus.country':
    'ISO country code for grouping and policy constraints (CO₂ caps, country-level statistics, country-coloured maps).',
  'bus.x':
    'Geographic longitude (°E). Used to position the bus on the canvas; not used by the optimisation itself.',
  'bus.y':
    'Geographic latitude (°N). Used to position the bus on the canvas; not used by the optimisation itself.',
  'bus.coordinates':
    'Geographic position (longitude °E, latitude °N) used to place the bus on the canvas.',

  // ── Line (AC) ────────────────────────────────────────────────────────────
  'line.name':
    'Unique line identifier. Used in flow results and the audit log when this line is edited.',
  'line.bus0':
    'From-bus of the line. Convention is that positive flow goes from bus0 to bus1 in the dispatch results.',
  'line.bus1':
    'To-bus of the line. Both buses must exist before the line can be created.',
  'line.carrier':
    'Carrier of the line — typically AC. Lines connecting buses with different carriers are not allowed; use a Link instead.',
  'line.length':
    'Physical length in km. Combined with per-unit-length impedances (where applicable) and used by length-based capital-cost models.',
  'line.s_nom':
    'Nominal apparent-power capacity in MVA. Used as the upper bound on |s| in power-flow constraints when the line is not extendable.',
  'line.s_nom_extendable':
    'Whether the line capacity is a decision variable. When true, the optimiser chooses s_nom between s_nom_min and s_nom_max and pays capital_cost per added MVA.',
  'line.s_nom_min':
    'Lower bound on the optimised line capacity (MVA). Only used when extendable; defaults to the existing s_nom so existing lines can be reinforced but not shrunk.',
  'line.s_nom_max':
    'Upper bound on the optimised line capacity (MVA). Only used when extendable. Leave blank for unbounded.',
  'line.r':
    'Series resistance per km (Ω/km). UI multiplies by length on save; PyPSA stores absolute Ω and then converts to per-unit via r_pu = r / v_nom². Rule of thumb: ~0.03 Ω/km on a 380 kV overhead line. Drives Ohmic losses when transmission_losses is enabled.',
  'line.x':
    'Series reactance per km (Ω/km). UI multiplies by length on save; PyPSA stores absolute Ω. The dominant term in DC OPF — flows split between parallel paths inversely proportional to x. Typical 380 kV value: ~0.3 Ω/km.',
  'line.b':
    'Shunt susceptance per km (S/km). UI multiplies by length on save; PyPSA stores absolute Siemens. Models charging current; usually negligible for short overhead lines and important for long underground/submarine cables.',
  'line.capital_cost':
    'Annualised investment cost per added MVA of capacity (€/MVA). Only used when the line is extendable.',
  'line.fom_cost':
    'Fixed O&M cost per installed MVA per year (€/MVA/yr). Paid on the full s_nom_opt, regardless of dispatch.',
  'line.overnight_cost':
    'Lump-sum construction cost (€/MVA·km or €/MVA). When set, the solver recomputes capital_cost = overnight_cost × annuity(discount_rate, lifetime) + fom_cost.',

  // ── Generator ────────────────────────────────────────────────────────────
  'generator.name':
    'Unique generator identifier. Time-series profiles are matched to this exact name when uploaded.',
  'generator.bus':
    'Bus the generator injects into. Carrier compatibility is checked at solve time.',
  'generator.carrier':
    'Energy/technology carrier (e.g. solar, onwind, lignite, OCGT). Drives CO₂ accounting, classification on the Time Series page (renewable/conventional/DR), and colours on the canvas.',
  'generator.p_nom':
    'Nominal active-power capacity in MW. Multiplied by p_max_pu to give the per-snapshot dispatch upper bound.',
  'generator.p_nom_min':
    'Lower bound on the optimised installed capacity (MW). Only meaningful when extendable.',
  'generator.p_nom_max':
    'Upper bound on the optimised installed capacity (MW). Only meaningful when extendable. Leave blank for unbounded.',
  'generator.p_nom_extendable':
    'When true, p_nom is a capacity-expansion decision variable. The optimiser chooses p_nom in [p_nom_min, p_nom_max] and charges capital_cost per added MW.',
  'generator.p_min_pu':
    'Minimum dispatch as a fraction of p_nom (0–1). For renewables this can be a time series equal to the must-run profile; for thermal units it sets the must-run floor.',
  'generator.p_max_pu':
    'Maximum dispatch as a fraction of p_nom (0–1). For VRE units this is the time-varying availability/capacity factor uploaded as a profile.',
  'generator.efficiency':
    'Conversion efficiency from primary energy to electricity (only used for CO₂ accounting via carrier.co2_emissions / efficiency).',
  'generator.marginal_cost':
    'Variable cost per MWh dispatched (€/MWh). Includes fuel + variable O&M; used directly in the objective.',
  'generator.capital_cost':
    'Annualised investment cost per MW of added capacity (€/MW/yr). Only paid on the extension above p_nom_min when extendable.',
  'generator.fom_cost':
    'Fixed O&M cost per installed MW per year (€/MW/yr). Paid on the full p_nom_opt regardless of dispatch — covers labour, insurance, scheduled maintenance.',
  'generator.overnight_cost':
    'Lump-sum construction cost (€/MW), as if built instantaneously without financing. When set, the solver recomputes capital_cost = overnight_cost × annuity(discount_rate, lifetime) + fom_cost. Leave empty to use the capital_cost you typed directly.',
  'generator.curtailment_cost':
    'Penalty added to the objective for every MWh of available-but-unused renewable energy: cost × (p_max_pu × p_nom_opt − p). Set > 0 to discourage the optimiser from spilling wind/solar. Custom attribute — not in stock PyPSA; injected via extra_functionality at solve time.',
  'generator.build_year':
    'First year the unit is operational. Combined with lifetime to determine availability across planning horizons.',
  'generator.lifetime':
    'Technical/economic lifetime in years. The unit is automatically retired in horizons after build_year + lifetime.',
  'generator.committable':
    'Enable unit commitment (binary on/off + start-up/shut-down decisions). Adds integer variables — slower to solve, more realistic for thermal plants.',
  'generator.ramp_limit_up':
    'Maximum hour-on-hour ramp-up as a fraction of p_nom (pu/h). Only enforced when committable.',
  'generator.ramp_limit_down':
    'Maximum hour-on-hour ramp-down as a fraction of p_nom (pu/h). Only enforced when committable.',
  'generator.start_up_cost':
    'Cost per start-up event (€). Discourages frequent cycling of large thermal units.',
  'generator.shut_down_cost':
    'Cost per shut-down event (€). Usually smaller than start-up cost.',
  'generator.min_up_time':
    'Minimum number of consecutive hours the unit must remain on after a start-up.',
  'generator.min_down_time':
    'Minimum number of consecutive hours the unit must remain off after a shut-down.',

  // ── StorageUnit (combined power + energy) ────────────────────────────────
  'storage_unit.name':
    'Unique storage-unit identifier. p_nom describes the AC-side rating; energy capacity follows from max_hours.',
  'storage_unit.bus':
    'Bus the storage charges/discharges through. Storage units operate symmetrically — same bus for both directions.',
  'storage_unit.carrier':
    'Storage technology (battery, PHS, …). Used for grouping in summaries and CO₂ accounting; does not affect the energy-balance equations.',
  'storage_unit.p_nom':
    'Nominal AC-side power rating in MW (charge and discharge use the same rating).',
  'storage_unit.max_hours':
    'Hours of energy storage at rated power: energy_capacity = p_nom × max_hours. Higher means longer-duration storage (PHS / hydrogen) vs short-duration (battery).',
  'storage_unit.p_nom_extendable':
    'When true, p_nom (and therefore energy capacity, since max_hours is fixed) is a decision variable.',
  'storage_unit.p_nom_min':
    'Lower bound (MW) on optimised p_nom_opt — only honoured when extendable. Set ≥ p_nom to forbid retirements.',
  'storage_unit.p_nom_max':
    'Upper bound (MW) on optimised p_nom_opt — only honoured when extendable. Leave empty for no upper bound (PyPSA defaults to +∞).',
  'storage_unit.efficiency_store':
    'Round-trip charging efficiency (0–1). Energy reaching the reservoir = electrical input × efficiency_store.',
  'storage_unit.efficiency_dispatch':
    'Round-trip discharging efficiency (0–1). Electrical output = energy from reservoir × efficiency_dispatch.',
  'storage_unit.standing_loss':
    'Self-discharge per hour (fraction of state-of-charge lost each step). Usually tiny for batteries, larger for thermal stores.',
  'storage_unit.cyclic_state_of_charge':
    'When true, SoC at the last snapshot must equal SoC at the first — prevents the optimiser from "free-charging" at the start of the horizon.',
  'storage_unit.state_of_charge_initial':
    'SoC at the first snapshot, in MWh. Ignored when cyclic_state_of_charge is true.',
  'storage_unit.marginal_cost':
    'Variable cost per MWh dispatched (€/MWh). Often zero for storage; used to model wear-and-tear.',
  'storage_unit.capital_cost':
    'Annualised investment cost per MW of added p_nom (€/MW/yr). Only used when extendable.',
  'storage_unit.fom_cost':
    'Fixed O&M cost per installed MW per year (€/MW/yr). Paid on the full p_nom_opt regardless of throughput.',
  'storage_unit.overnight_cost':
    'Lump-sum construction cost (€/MW). When set, the solver recomputes capital_cost = overnight_cost × annuity(discount_rate, lifetime) + fom_cost.',
  'storage_unit.build_year':
    'Commissioning year. Used together with lifetime to retire the unit in later horizons.',

  // ── Store (energy-only reservoir) ────────────────────────────────────────
  'store.name':
    'Unique store identifier. A Store models pure energy storage; pair it with a Link if you need a separate charge/discharge rating.',
  'store.bus':
    'Bus the store balances. The bus carrier (H₂, heat, methane, …) determines what kind of energy is stored.',
  'store.carrier':
    'Carrier of the store. Inherits from the bus when not set; used for grouping and CO₂ accounting.',
  'store.e_nom':
    'Nominal energy capacity in MWh (or carrier-specific units). Constrains the SoE between e_min_pu × e_nom and e_max_pu × e_nom.',
  'store.e_nom_extendable':
    'When true, e_nom is a decision variable in [e_nom_min, e_nom_max], priced via capital_cost.',
  'store.e_nom_min':
    'Lower bound on optimised energy capacity (MWh). Only used when extendable.',
  'store.e_nom_max':
    'Upper bound on optimised energy capacity (MWh). Only used when extendable. Leave blank for unbounded.',
  'store.e_min_pu':
    'Minimum state-of-energy as a fraction of e_nom (0–1). Use for reserve floors or to forbid full discharge.',
  'store.e_max_pu':
    'Maximum state-of-energy as a fraction of e_nom (0–1). Usually 1; can be a time series for seasonal limits.',
  'store.e_initial':
    'Initial state-of-energy in MWh at the first snapshot. Ignored when e_cyclic is true.',
  'store.e_cyclic':
    'When true, the SoE at the last snapshot must equal the SoE at the first — eliminates "free fuel" at horizon boundaries.',
  'store.capital_cost':
    'Annualised investment cost per MWh of added energy capacity (€/MWh/yr). Only used when extendable.',
  'store.fom_cost':
    'Fixed O&M cost per installed MWh per year (€/MWh/yr). Paid on the full e_nom_opt regardless of cycling.',
  'store.overnight_cost':
    'Lump-sum construction cost (€/MWh). When set, the solver recomputes capital_cost = overnight_cost × annuity(discount_rate, lifetime) + fom_cost.',
  'store.marginal_cost':
    'Variable cost per MWh of energy released from the store (€/MWh). Often zero.',
  'store.standing_loss':
    'Self-discharge per hour as a fraction of SoE. Important for thermal/hydrogen stores.',

  // ── Load ────────────────────────────────────────────────────────────────
  'load.name':
    'Unique load identifier. Time-series profiles are matched to this exact name when uploaded on the Time Series page.',
  'load.bus':
    'Bus that feeds the load. The load draws from this bus during every snapshot.',
  'load.carrier':
    'Carrier of the demand (electricity / H₂ / heat). Determines which energy balance the load enters.',
  'load.p_set':
    'Static active-power demand in MW used when no time-series profile is uploaded. Replaced by the profile when one is present.',
  'load.q_set':
    'Static reactive-power demand in MVAr. Only meaningful for non-linear AC power flow; ignored by linear OPF.',
  'load.sign':
    'Direction convention: -1 (default) means the load consumes from the bus; +1 turns it into an injection (rarely used).',

  // ── Link (lossy directional converter / HVDC / electrolyser / …) ─────────
  'link.name':
    'Unique link identifier. Profiles uploaded on the Time Series page (e.g. electrolyser availability) match this exact name.',
  'link.bus0':
    'Input/from-bus. Power is drawn from bus0 during dispatch.',
  'link.bus1':
    'Output/to-bus. Power delivered to bus1 = power drawn from bus0 × efficiency.',
  'link.carrier':
    'Link technology (electrolyser, fuel cell, HVDC, heat pump, …). Drives classification on the Time Series page and colour coding.',
  'link.p_nom':
    'Nominal active-power capacity at bus0 (input side), in MW. The bus1 side is automatically capped at p_nom × efficiency.',
  'link.p_nom_min':
    'Lower bound on optimised input capacity (MW). Only used when extendable.',
  'link.p_nom_max':
    'Upper bound on optimised input capacity (MW). Only used when extendable. Leave blank for unbounded.',
  'link.p_nom_extendable':
    'When true, p_nom is a decision variable in [p_nom_min, p_nom_max], priced via capital_cost.',
  'link.efficiency':
    'Conversion efficiency from bus0 to bus1 (0–1). Power_out = Power_in × efficiency. For an electrolyser this is the H₂ output / electricity input.',
  'link.p_min_pu':
    'Minimum dispatch as a fraction of p_nom. Negative values allow reverse flow (use with caution — model the reverse direction as a separate Link when efficiencies differ).',
  'link.p_max_pu':
    'Maximum dispatch as a fraction of p_nom (0–1). Use a time series here for technology-specific availability.',
  'link.marginal_cost':
    'Variable cost per MWh of input dispatched (€/MWh).',
  'link.capital_cost':
    'Annualised investment cost per MW of added input capacity (€/MW/yr). Only paid on the extension when extendable.',
  'link.fom_cost':
    'Fixed O&M cost per installed MW per year (€/MW/yr). Paid on the full p_nom_opt regardless of throughput.',
  'link.overnight_cost':
    'Lump-sum construction cost (€/MW). When set, the solver recomputes capital_cost = overnight_cost × annuity(discount_rate, lifetime) + fom_cost.',
  'link.build_year':
    'Commissioning year. Used with lifetime to retire the link in later horizons.',
  'link.lifetime':
    'Technical/economic lifetime in years. The link is automatically retired build_year + lifetime years after commissioning.',

  'line.build_year':
    'Commissioning year. Used with lifetime to retire the line in later horizons.',
  'line.lifetime':
    'Technical/economic lifetime in years. The line is automatically retired build_year + lifetime years after commissioning. Leave blank for infinite (PyPSA default).',
  'transformer.build_year':
    'Commissioning year. Used with lifetime to retire the transformer in later horizons.',
  'transformer.lifetime':
    'Technical/economic lifetime in years. The transformer is automatically retired build_year + lifetime years after commissioning. Leave blank for infinite (PyPSA default).',
}

// Convenience accessor — returns undefined when the key isn't in the map so
// callers can pass it straight into a `tip?: string` prop.
export function tip(key: string): string | undefined {
  return PROPERTY_DOCS[key]
}
