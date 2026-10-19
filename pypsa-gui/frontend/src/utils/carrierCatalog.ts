// Curated catalog of PyPSA-Eur carriers, used to populate the carrier dropdown
// in PropertiesPanel even when the project's n.carriers table is sparse. The
// goal is to keep users from inventing typo'd carrier names like "windd" or
// from accidentally assigning "solar" to a wind turbine — they pick from the
// list instead. categorizeCarriers() in PropertiesPanel.tsx auto-groups by
// substring match, so this catalog only supplies names.
//
// Names mirror PyPSA-Eur conventions (lowercased, dashed where relevant). The
// backend auto-creates the carrier row with color + nice_name + co2_emissions
// (see services/carrier_catalog.py) on first use.

export interface CarrierEntry {
  name: string
  nice_name: string
  color: string
  co2_emissions: number  // tCO2/MWh_thermal
  unit?: string          // what a bus of this carrier counts in: 'MWh' (energy product) or 't' (material); unset = MWh
}

// Per-group catalog — also exported so the create/edit form can scope the
// dropdown by component type later if we want.
export const CATALOG_RENEWABLES: CarrierEntry[] = [
  { name: 'onwind',         nice_name: 'Onshore Wind',  color: '#235ebc', co2_emissions: 0 },
  { name: 'offwind-ac',     nice_name: 'Offshore Wind (AC)', color: '#6895dd', co2_emissions: 0 },
  { name: 'offwind-dc',     nice_name: 'Offshore Wind (DC)', color: '#74c6f2', co2_emissions: 0 },
  { name: 'solar',          nice_name: 'Solar PV (utility)', color: '#f9d002', co2_emissions: 0 },
  { name: 'solar-rooftop',  nice_name: 'Solar PV (rooftop)', color: '#ffef60', co2_emissions: 0 },
  { name: 'ror',            nice_name: 'Run-of-River',  color: '#3dbfb0', co2_emissions: 0 },
  { name: 'hydro',          nice_name: 'Hydro Reservoir', color: '#298c81', co2_emissions: 0 },
  { name: 'geothermal',     nice_name: 'Geothermal',    color: '#ba91b1', co2_emissions: 0 },
  { name: 'biomass',        nice_name: 'Biomass',       color: '#0c6013', co2_emissions: 0 },
  { name: 'wave',           nice_name: 'Wave',          color: '#28abc8', co2_emissions: 0 },
]

export const CATALOG_THERMAL: CarrierEntry[] = [
  { name: 'CCGT',    nice_name: 'CCGT (Gas)',  color: '#a85522', co2_emissions: 0.187 },
  { name: 'OCGT',    nice_name: 'OCGT (Gas)',  color: '#d35050', co2_emissions: 0.187 },
  { name: 'coal',    nice_name: 'Hard Coal',   color: '#545454', co2_emissions: 0.34 },
  { name: 'lignite', nice_name: 'Lignite',     color: '#826837', co2_emissions: 0.41 },
  { name: 'oil',     nice_name: 'Oil',         color: '#262626', co2_emissions: 0.267 },
  { name: 'nuclear', nice_name: 'Nuclear',     color: '#ff8c00', co2_emissions: 0 },
  { name: 'gas',    nice_name: 'Natural Gas', color: '#e0986c', co2_emissions: 0.187 },
  { name: 'diesel', nice_name: 'Diesel',      color: '#3b3b3b', co2_emissions: 0.267 },
]

export const CATALOG_HYDROGEN: CarrierEntry[] = [
  { name: 'H2',             nice_name: 'Hydrogen',         color: '#dd2727', co2_emissions: 0 },
  { name: 'H2 electrolysis', nice_name: 'H₂ Electrolysis', color: '#ff6361', co2_emissions: 0 },
  { name: 'H2 fuel cell',   nice_name: 'H₂ Fuel Cell',     color: '#a8463d', co2_emissions: 0 },
]

// Heat. The creation form's heat-bus pickers match any carrier naming "heat"; this is the one a user can pick.
export const CATALOG_HEAT: CarrierEntry[] = [
  { name: 'heat', nice_name: 'Heat', color: '#cc1e1e', co2_emissions: 0 },
]

// Process products (grid-to-process, design D2): NH3 is an energy product counted in MWh (LHV); the rest are
// materials counted in tonnes. Mirrors services/carrier_catalog.py.
export const CATALOG_PROCESS: CarrierEntry[] = [
  { name: 'NH3',         nice_name: 'Ammonia',     color: '#1f9d8b', co2_emissions: 0, unit: 'MWh' },
  { name: 'N2',          nice_name: 'Nitrogen',    color: '#8fb8c9', co2_emissions: 0, unit: 't' },
  { name: 'ore',         nice_name: 'Ore',         color: '#8c6d4f', co2_emissions: 0, unit: 't' },
  { name: 'concentrate', nice_name: 'Concentrate', color: '#c9a227', co2_emissions: 0, unit: 't' },
]

export const CATALOG_STORAGE: CarrierEntry[] = [
  { name: 'battery', nice_name: 'Battery',         color: '#ace37f', co2_emissions: 0 },
  { name: 'PHS',     nice_name: 'Pumped Hydro',    color: '#51dbcc', co2_emissions: 0 },
]

export const CATALOG_ELECTRICITY: CarrierEntry[] = [
  { name: 'AC', nice_name: 'AC', color: '#70af1d', co2_emissions: 0 },
  { name: 'DC', nice_name: 'DC', color: '#8a1caf', co2_emissions: 0 },
]

// Flat union used to populate dropdowns globally — categorizeCarriers() handles
// the grouping by substring match.
export const CARRIER_CATALOG: CarrierEntry[] = [
  ...CATALOG_ELECTRICITY,
  ...CATALOG_RENEWABLES,
  ...CATALOG_THERMAL,
  ...CATALOG_HYDROGEN,
  ...CATALOG_HEAT,
  ...CATALOG_PROCESS,
  ...CATALOG_STORAGE,
]

export const CARRIER_CATALOG_NAMES: string[] = CARRIER_CATALOG.map(c => c.name)
