// Cross-tab carrier alias normalisers.
//
// Different PyPSA-Eur projects spell the same logical carrier in many ways
// (`battery` / `bess` / `Li-Ion` / `lithium-ion`, `h2` / `hydrogen` /
// `H2 Storage`, …). The Dispatch tab already canonicalised these into a small
// set of grouping keys; this module hoists those helpers out of Dispatch so
// CapacityExpansion + Economics + AggregatedOverview can apply the SAME
// canonicalisation when feeding `extractCarriers`. Without this, a user who
// picks "battery" on one tab sees only the rows literally typed "battery",
// while the Dispatch tab silently bundles BESS / Li-Ion / lithium-ion into
// the same group — the carrier-filter contract breaks across tabs.
//
// Convention: pass `(row.carrier)` through the relevant `*CarrierKey` before
// adding to the filter's `availableCarriers` AND when calling
// `matchesSelected`. The key is the canonical lower-case slug; the matching
// `*CarrierLabel` returns the human-readable display string.

// ── Storage units (StorageUnit + Store) ──────────────────────────────────
const STORAGE_BATTERY_ALIASES  = new Set(['battery', 'bess', 'li-ion', 'lithium', 'lithium-ion'])
const STORAGE_HYDROGEN_ALIASES = new Set(['h2', 'hydrogen', 'h2 storage', 'h2_storage', 'hydrogen storage'])
const STORAGE_HEAT_ALIASES     = new Set(['heat', 'thermal', 'tes', 'thermal storage', 'heat storage', 'urban heat tank', 'water tanks'])
const STORAGE_PHS_ALIASES      = new Set(['phs', 'pumped hydro', 'pumped storage', 'psh'])

/** Map any storage-carrier spelling to its canonical key. Empty / unknown
 *  values pass through unchanged (after lowercasing) so user-defined
 *  carriers still get their own filter chip. */
export function storageCarrierKey(raw: string | undefined | null): string {
  const c = (raw ?? '').trim().toLowerCase()
  if (STORAGE_BATTERY_ALIASES.has(c))  return 'battery'
  if (STORAGE_HYDROGEN_ALIASES.has(c)) return 'hydrogen'
  if (STORAGE_HEAT_ALIASES.has(c))     return 'heat'
  if (STORAGE_PHS_ALIASES.has(c))      return 'pumped_hydro'
  return c || 'unspecified'
}

export function storageCarrierLabel(k: string): string {
  if (k === 'battery')      return 'Battery'
  if (k === 'hydrogen')     return 'Hydrogen'
  if (k === 'heat')         return 'Heat'
  if (k === 'pumped_hydro') return 'Pumped Hydro'
  if (k === 'unspecified')  return 'Unspecified'
  return k.charAt(0).toUpperCase() + k.slice(1)
}

// ── Loads ─────────────────────────────────────────────────────────────
const LOAD_ELECTRICAL_ALIASES = new Set(['', 'ac', 'electricity', 'electrical', 'electric', 'power'])
const LOAD_HYDROGEN_ALIASES   = new Set(['h2', 'hydrogen'])
const LOAD_HEAT_ALIASES       = new Set(['heat', 'thermal', 'heating'])

export function loadCarrierKey(raw: string | undefined | null): string {
  const c = (raw ?? '').trim().toLowerCase()
  if (LOAD_ELECTRICAL_ALIASES.has(c)) return 'electrical'
  if (LOAD_HYDROGEN_ALIASES.has(c))   return 'hydrogen'
  if (LOAD_HEAT_ALIASES.has(c))       return 'heat'
  return c || 'unspecified'
}

// ── Cross-class dispatch ──────────────────────────────────────────────
// Generic helper for rows that may be any component class. The caller
// passes the class name and the raw carrier; the helper picks the right
// alias map. Used by the multi-class aggregator in CapacityExpansion.tsx
// where sized rows from Generator / StorageUnit / Store / Link / Line all
// flow into a single `extractCarriers` call.
export type ComponentClass =
  'Generator' | 'StorageUnit' | 'Store' | 'Link' | 'Line' | 'Transformer' | 'Load'

export function canonicaliseCarrier(
  componentClass: ComponentClass,
  raw: string | undefined | null,
): string {
  if (componentClass === 'StorageUnit' || componentClass === 'Store') {
    return storageCarrierKey(raw)
  }
  if (componentClass === 'Load') {
    return loadCarrierKey(raw)
  }
  // Generators, Links, Lines, Transformers keep their raw carrier string —
  // PyPSA-Eur conventions don't alias these the way storage spelling drifts.
  // Lowercase + trim only so cross-row deduplication still merges
  // "Gas" / "gas" / "GAS" into one chip.
  return ((raw ?? '').trim() || 'unspecified').toLowerCase()
}

/** Friendly label for any canonical key, regardless of component class.
 *  Uppercases short acronyms (ac/h2/dc), title-cases everything else. */
export function carrierDisplayName(canonical: string): string {
  if (canonical === 'unspecified') return 'Unspecified'
  if (canonical === '') return 'Unspecified'
  const ACRONYMS = new Set(['ac', 'dc', 'h2', 'co2', 'lng', 'ng', 'pv', 'ror'])
  if (ACRONYMS.has(canonical.toLowerCase())) return canonical.toUpperCase()
  // Try storage label first (handles 'battery' → 'Battery', 'pumped_hydro'
  // → 'Pumped Hydro'); fall through to title-case for everything else.
  const s = storageCarrierLabel(canonical)
  if (s !== canonical && s !== canonical.charAt(0).toUpperCase() + canonical.slice(1)) {
    return s
  }
  return canonical
    .split(/[-_\s]+/)
    .map(seg => seg.charAt(0).toUpperCase() + seg.slice(1).toLowerCase())
    .join(' ')
}
