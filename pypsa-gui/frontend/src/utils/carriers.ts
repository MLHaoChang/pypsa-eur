// Carrier classification shared across the results tabs, the schematic canvas,
// and the properties panel. Previously this exact predicate was copy-pasted in
// four places (results/shared.tsx, layout/PropertiesPanel.tsx, pages/MapCanvas.tsx,
// pages/TopologyCanvas.tsx) with a subtle drift: two copies guarded a null
// carrier (`(carrier ?? '')`) and two did not (`carrier.toLowerCase()` → crash
// on a null carrier). This is the single null-safe source of truth.

export const RENEWABLE_KEYWORDS = ['wind', 'solar', 'pv', 'ror', 'run-of-river',
  'geothermal', 'offwind', 'onwind', 'wave', 'tidal', 'rooftop']

export function isRenewableCarrier(carrier: string | null | undefined): boolean {
  const c = (carrier ?? '').toLowerCase()
  // hydro without pump/storage is renewable; pump/storage goes under Storage.
  // Matched on a word boundary, not `c.includes('hydro')` — that also matched
  // inside `hydrogen` (H2 storage/generation, not hydropower), misclassifying
  // it as a renewable. Folded in from the 2026-07-31 review (Finding 2), same
  // "hydro ⊂ hydrogen" defect fixed the same way in the backend's
  // validation_service.py (_check_curtailment_cost's `rkw` mirror).
  if (/\bhydro\b/.test(c) && !c.includes('pump') && !c.includes('storage')) return true
  return RENEWABLE_KEYWORDS.some(k => c.includes(k))
}
