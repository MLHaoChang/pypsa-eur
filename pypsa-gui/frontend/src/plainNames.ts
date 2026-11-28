// Plain-language labels for names the backend uses internally (RAW-NAMES-1).
// Parameter keys, option ids and Library ids stay what the backend sends; only the text
// a person reads comes from here. Glossary: pypsa-gui/CONTEXT.md.

const PARAM_LABELS: Record<string, string> = {
  holidays: 'Holiday dates',
  timezone: 'Time zone',
  seasonal_amplitude: 'Seasonal swing',
  noise: 'Random variation',
  seed: 'Random seed',
  shifts: 'Shifts per day',
  base_share: 'Always-on share',
  saturday_operation: 'Runs on Saturdays',
  shutdown_weeks: 'Shutdown weeks',
  preset: 'Starting point',
  profile_id: 'Library profile',
  cooling_share: 'Cooling share',
  heating: 'Heating type',
  heating_base_c: 'Heating threshold (°C)',
  ev_share: 'Electric-vehicle share',
  mode: 'Charging pattern',
  weekend_factor: 'Weekend level',
  base_temp_c: 'Heating threshold (°C)',
  hot_water_share: 'Hot-water share',
  pue_at_design: 'Cooling overhead at design (PUE)',
  pue_temperature_slope_per_k: 'Overhead rise per °C',
  pue_design_temp_c: 'Design temperature (°C)',
  ai_workload: 'AI workload',
}

/** Sentence-case a snake_case id: `ev_charging` → `Ev charging`. */
export function sentenceCase(id: string): string {
  const s = id.replace(/_/g, ' ').trim()
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export const paramLabel = (key: string): string => PARAM_LABELS[key] ?? sentenceCase(key)

const ACRONYMS: Record<string, string> = {
  ev_charging: 'EV charging',
  ai_training_dc: 'AI training data centre',
  data_centre: 'Data centre',
  households_heat_pumps: 'Households with heat pumps',
}

/** Archetype and preset names, e.g. `ai_training_dc` → `AI training data centre`. */
export const archetypeLabel = (name: string): string => ACRONYMS[name] ?? sentenceCase(name)

const CONTROL_LABELS: Record<string, string> = {
  gfl_pq: 'Grid-following, fixed power (P/Q)',
  gfl_qu: 'Grid-following, voltage-dependent reactive power (Q/U)',
  gfl_pf: 'Grid-following, fixed power factor',
  gfl_fw: 'Grid-following, frequency response',
  gfm_droop: 'Grid-forming, droop',
  gfm_vsm: 'Grid-forming, virtual synchronous machine',
}
export const controlModeLabel = (id: string): string => CONTROL_LABELS[id] ?? sentenceCase(id)

/** The island control presets (gridspine `PRESETS`) in words. */
const PRESET_LABELS: Record<string, string> = {
  grid_following: 'Grid following',
  grid_forming_support: 'Grid forming support',
  fast_frequency_response: 'Fast frequency response',
  voltage_support: 'Voltage support',
}
export const presetLabel = (id: string): string => PRESET_LABELS[id] ?? sentenceCase(id)

const ISLAND_SCENARIO_LABELS: Record<string, string> = {
  ups_bridged: 'UPS-bridged',
  seamless: 'Seamless',
  planned: 'Planned',
}
export const islandScenarioLabel = (id: string): string => ISLAND_SCENARIO_LABELS[id] ?? sentenceCase(id)

/** A Library series for a picker: `ipp_da_base`, v1 → `Ipp da base (version 1)`. */
export const librarySeriesLabel = (id: string, version: number): string =>
  `${sentenceCase(id)} (version ${version})`

const EH_ROLE_LABELS: Record<string, string> = {
  grid_import: 'Grid import',
  eh_import: 'Grid import (Energy Hub)',
  import: 'Import',
  eh_conversion: 'Conversion (Energy Hub)',
  conversion: 'Conversion',
  electrolyser: 'Electrolyser',
  fuel_cell: 'Fuel cell',
}
/** An Energy Hub Link role id in words; the id itself stays what the backend stores. */
export const ehRoleLabel = (id: string): string => EH_ROLE_LABELS[id] ?? sentenceCase(id)
