// The island requirements in the user's words, shared by the Expert island
// editor (`CampusIslandSection`) and Guided's Island requirements card, so
// both modes name a field the same way (parity test in islandWords.test.ts).
// Scenario words come from plainNames' islandScenarioLabel.

/** The tagged numbers both editors edit, in their order. */
export const ISLAND_REQUIREMENT_LABELS = {
  sustained_h: 'Sustained ride-through (h)',
  bridge_s: 'UPS bridge (s)',
  fuel_autonomy_h: 'Fuel autonomy (h)',
  genset_redundancy_n: 'Genset redundancy (N+k)',
} as const

export type IslandRequirementNumber = keyof typeof ISLAND_REQUIREMENT_LABELS

/** The other island requirements the Guided card lists read-only (Expert edits them). */
export const ISLAND_OTHER_REQUIREMENT_LABELS: Record<string, string> = {
  rocof_window_ms: 'Rate-of-change window (ms)',
  gfm_margin: 'Grid-forming margin',
  ride_through_storage: 'Ride-through storage',
  pickup_blocks: 'Pickup blocks',
}

/** A converter's or UPS's control settings in the user's words: the section-5
 *  Controls block's one map (UX 29 condition 1). Every field gridspine's
 *  `control_fields` names has an entry (parity test against the schema in
 *  islandWords.test.ts), so a new field without words fails. */
export interface ControlFieldWords {
  label: string
  unit: string
  /** A curve: pairs, one per line. The hint says what each pair holds. */
  pairs?: string
}

export const CONTROL_FIELD_WORDS: Record<string, ControlFieldWords> = {
  pf_rated: { label: 'Rated power factor', unit: '' },
  cos_phi: { label: 'Fixed power factor', unit: '' },
  cosphi_p_points: { label: 'Power factor by output', unit: '',
    pairs: 'Pairs of output (pu, 0 to 1) and power factor (negative = under-excited), one per line' },
  qu_points: { label: 'Reactive power by voltage', unit: '',
    pairs: 'Pairs of voltage (pu) and reactive power (pu, −1 to 1), one per line' },
  fw_k_mw_per_hz: { label: 'Frequency response gain', unit: 'MW/Hz' },
  fw_deadband_hz: { label: 'Frequency deadband', unit: 'Hz' },
  fw_delay_s: { label: 'Response delay', unit: 's' },
  fw_ramp_mw_per_s: { label: 'Response ramp', unit: 'MW/s' },
  fw_p_limit_mw: { label: 'Response power limit', unit: 'MW' },
  droop_pct: { label: 'Frequency droop', unit: '%' },
  q_droop_pct: { label: 'Voltage droop', unit: '%' },
  tau_f_s: { label: 'Frequency filter time', unit: 's' },
  H_v_s: { label: 'Virtual inertia', unit: 's' },
  i_max_pu: { label: 'Current limit', unit: 'pu' },
  k_gfm_min: { label: 'Minimum grid-forming current', unit: 'pu' },
  walk_in_s: { label: 'Walk-in time', unit: 's' },
  f_window_hz: { label: 'Frequency window', unit: 'Hz' },
}

/** The UPS settings the Controls block edits (gridspine's UPS block, less `it_load`). */
export const UPS_CONTROL_FIELDS = ['walk_in_s', 'f_window_hz'] as const
export const UPS_TRANSFER_LABEL = 'Transfers to its battery on islanding'

/** gridspine's limit wordings (schema/island.py `_SCALAR`), in words: no interval notation (UX 30; parity test
 *  test_island_control_words_parity.py). */
export const LIMIT_WORDS: Record<string, string> = {
  'in (0, 1]': 'above 0 and at most 1',
  'in (0, 100]': 'above 0 and at most 100',
  positive: 'above 0',
  'non-negative': '0 or more',
  'at least 1': '1 or more',
}
