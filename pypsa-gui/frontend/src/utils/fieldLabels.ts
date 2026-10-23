import type { EhTemplateMeta } from '../api/simulation'

/** Flow-target fields a user can edit in Expert, in their words. The template
 *  ledger adds its own labels on top (`ledgerFieldLabels`); a field in neither
 *  has no label, and the item shows the entity alone. */
export const FIELD_LABELS: Record<string, string> = {
  efficiency: 'Efficiency',
  eh_sk_mva: 'Short-circuit level (MVA)',
  target_lole_h: 'LOLE target',
  p_nom: 'Capacity',
  eh_process: 'Process unit',
  rated_mva: 'Rated apparent power',
  harmonic_source: 'Harmonic source',
  harmonic_spectrum: 'Harmonic spectrum',
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
