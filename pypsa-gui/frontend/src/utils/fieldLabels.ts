import type { EhTemplateMeta } from '../api/simulation'

/** Flow-target fields a user can edit in Expert, in their words. The template
 *  ledger adds its own labels on top (`ledgerFieldLabels`); a field in neither
 *  has no label, and the item shows the entity alone. */
export const FIELD_LABELS: Record<string, string> = {
  efficiency: 'Efficiency',
  eh_sk_mva: 'Short-circuit level (MVA)',
  site_anchor: 'Site coordinates',
  target_lole_h: 'LOLE target',
  p_nom: 'Capacity',
  p_nom_max: 'Maximum capacity',
  p_nom_min: 'Minimum capacity',
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
  single_phase_share: 'Single-phase share',
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
