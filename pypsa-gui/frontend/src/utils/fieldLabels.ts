import type { EhTemplateMeta } from '../api/simulation'

/** Flow-target fields a user can edit in Expert, in their words. The template
 *  ledger adds its own labels on top (`ledgerFieldLabels`); a field in neither
 *  has no label, and the item shows the entity alone. */
export const FIELD_LABELS: Record<string, string> = {
  efficiency: 'Efficiency',
  eh_sk_mva: 'Short-circuit level (MVA)',
  site_anchor: 'Site coordinates',
  poc_xr: 'PoC X/R',
  connection_limit: 'Connection limit (MW)',
  target_lole_h: 'LOLE target',
  p_nom: 'Capacity',
  p_nom_max: 'Maximum capacity',
  p_nom_min: 'Minimum capacity',
  dc_ac_ratio: 'DC/AC ratio',
  max_hours: 'Storage hours',
  e_nom: 'Energy capacity',
  e_nom_max: 'Maximum capacity', // a store's cap: the asset form's word (asset_schema/fields.py)
  marginal_cost: 'Running cost',
  eh_process: 'Process unit',
  rated_mva: 'Rated apparent power',
  harmonic_source: 'Harmonic source',
  harmonic_spectrum: 'Harmonic spectrum',
  flicker_step_mva: 'Largest power step',
  flicker_changes_per_min: 'Changes per minute',
  motor_kw: 'Motor rating',
  start_method: 'Start method',
  i_start_pu: 'Start current multiple',
  ride_through_curve: 'Ride-through curve',
  restart_time_h: 'Restart time',
  // Grid risk to production (G2P-13): the asset form's words (services/asset_schema/fields.py).
  outage_rate_value: 'Forced-outage rate',
  mttr_hours: 'Mean time to repair',
  lost_production_value: 'Lost-production value',
  mwh_per_t: 'Energy per tonne',
  single_phase_share: 'Single-phase share',
  temperature_c: 'Supply temperature',
  pressure_bar: 'Header pressure',
  max_supply_temp_c: 'Max supply temperature',
  max_pressure_bar: 'Max steam pressure',
  // Set only in Expert; Guided names them (design §3.5). The Expert editors' own words.
  grid_code_profile: 'Grid code', // CampusElectricalPanel "Grid code and PCC"
  grid_code_document: 'Grid-code document', // CampusGridCodeSection upload
  timezone: 'Site time zone',
  dsr_share_of_load: 'DSR volume', // SolverSettings "Demand-response tier"
  dsr_price_eur_per_mwh: 'Demand-response price',
  business_case: 'Business case', // Results › Investment case, its "Business case" section
  committable: 'Unit commitment', // the asset form (asset_schema/fields.py); its schema label wins
  variants: 'Project variants', // ScenariosPanel "project variants"
}

/** The ledger's label for each field its rows point at (DC-02: the grid
 *  readiness rows carry `label` and `field`), over `FIELD_LABELS`. */
export function ledgerFieldLabels(template: Pick<EhTemplateMeta, 'ledger'> | null): Record<string, string> {
  const out: Record<string, string> = { ...FIELD_LABELS }
  for (const r of template?.ledger ?? []) {
    if (r.label && r.field) out[r.field] = r.label
  }
  return out
}
