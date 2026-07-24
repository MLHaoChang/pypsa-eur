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
  // hydro without pump/storage is renewable; pump/storage goes under Storage
  if (c.includes('hydro') && !c.includes('pump') && !c.includes('storage')) return true
  return RENEWABLE_KEYWORDS.some(k => c.includes(k))
}
