import type { EhTemplateMeta } from '../api/simulation'

/** Flow-target fields a user can edit in Expert, in their words. The template
 *  ledger adds its own labels on top (`ledgerFieldLabels`); a field in neither
 *  has no label, and the item shows the entity alone. */
export const FIELD_LABELS: Record<string, string> = {
  efficiency: 'Efficiency',
  eh_sk_mva: 'Short-circuit level (MVA)',
  site_anchor: 'Site coordinates',
  poc_xr: 'PoC X/R',
  target_lole_h: 'LOLE target',
  p_nom: 'Capacity',
  p_nom_max: 'Maximum capacity',
  p_nom_min: 'Minimum capacity',
  dc_ac_ratio: 'DC/AC ratio',
  max_hours: 'Storage hours',
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
