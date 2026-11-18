// The island requirements in the user's words, shared by the Expert island
// editor (`CampusIslandSection`) and Guided's Island requirements card, so
// both modes name a field the same way (parity test in islandWords.test.ts).
import type { IslandScenario } from '../api/campusElectrical'

/** The tagged numbers both editors edit, in their order. */
export const ISLAND_REQUIREMENT_LABELS = {
  sustained_h: 'Sustained ride-through (h)',
  bridge_s: 'UPS bridge (s)',
  fuel_autonomy_h: 'Fuel autonomy (h)',
  genset_redundancy_n: 'Genset redundancy (N+k)',
} as const

export type IslandRequirementNumber = keyof typeof ISLAND_REQUIREMENT_LABELS

/** The islanding scenarios in words. */
export const ISLAND_SCENARIO_LABELS: Record<IslandScenario, string> = {
  ups_bridged: 'UPS-bridged',
  seamless: 'Seamless',
  planned: 'Planned',
}

/** The other island requirements the Guided card lists read-only (Expert edits them). */
export const ISLAND_OTHER_REQUIREMENT_LABELS: Record<string, string> = {
  rocof_window_ms: 'Rate-of-change window (ms)',
  gfm_margin: 'Grid-forming margin',
  ride_through_storage: 'Ride-through storage',
  pickup_blocks: 'Pickup blocks',
}
