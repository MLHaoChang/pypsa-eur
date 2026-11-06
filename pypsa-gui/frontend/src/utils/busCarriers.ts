// Bus-carrier families: which carriers count as hydrogen, heat, gas or
// electricity. One definition for the creation form's bus pickers and the
// 3D asset library (a manifold vs a switchyard, an electrolyser vs a fuel
// cell). Lower-case semantics; the patterns are case-insensitive. Pure.

/** Hydrogen buses: H2, hydrogen, H2 pipeline. */
export const H2_BUS_RE = /^(h2|hydrogen|h2[ _]pipeline)$/i
/** Heat buses: anything naming heat (urban central heat, heat-low, …). */
export const HEAT_BUS_RE = /heat/i
/** Steam buses: anything naming steam (grid-to-process issue 15). */
export const STEAM_BUS_RE = /steam/i
/** Gas / fuel buses: anything naming gas or biomass, and oil, fuel. */
export const GAS_BUS_RE = /gas|biomass|^(oil|fuel)$/i
/** Electrical buses: AC, DC, electricity, or no carrier (defaults to AC). */
export const ELEC_BUS_RE = /^(ac|dc|electricity|)$/i

/** A component's H₂ carrier: H2 / hydrogen / H₂, alone or followed by ' …' or '_…' (H2 Store, h2_store). */
export const H2_CARRIER_RE = /^(h2|hydrogen|h₂)([ _].*)?$/i
export const isH2Carrier = (carrier: string): boolean => H2_CARRIER_RE.test(carrier ?? '')

export type BusFilter = 'h2' | 'non-h2' | 'electricity' | 'heat' | 'gas'
export type BusFamily = 'h2' | 'heat' | 'gas' | 'electricity' | 'other'

export function carrierMatches(busCarrier: string, want: BusFilter): boolean {
  const c = busCarrier ?? ''
  switch (want) {
    case 'h2':           return H2_BUS_RE.test(c)
    case 'non-h2':       return !H2_BUS_RE.test(c)
    case 'electricity':  return ELEC_BUS_RE.test(c)
    case 'heat':         return HEAT_BUS_RE.test(c)
    case 'gas':          return GAS_BUS_RE.test(c)
  }
}

export function busCarrierFamily(busCarrier: string): BusFamily {
  const c = busCarrier ?? ''
  if (H2_BUS_RE.test(c)) return 'h2'
  if (HEAT_BUS_RE.test(c)) return 'heat'
  if (GAS_BUS_RE.test(c)) return 'gas'
  if (ELEC_BUS_RE.test(c)) return 'electricity'
  return 'other'
}

/** A form value that holds nothing: empty, or a missing number read back as text. */
const isBlank = (v: string | undefined): boolean => v === undefined || ['', 'nan', 'null', 'undefined'].includes(v.trim().toLowerCase())

/**
 * The heat and steam level rows a card hides (grid-to-process issue 15). The heat row (`heatKey`) shows when one
 * of `busCarriers` names heat (the heat pickers' rule, `HEAT_BUS_RE`), the steam row (`steamKey`) when one names
 * steam. A row that holds a value always shows, so a level set on another carrier is never hidden. The Bus card
 * passes its own carrier; the Link card the carriers of the buses it connects.
 */
export function hiddenLevelFields(busCarriers: string[], fs: Record<string, string | undefined>,
  heatKey: string, steamKey: string): Set<string> {
  const hidden = new Set<string>()
  if (!busCarriers.some(c => HEAT_BUS_RE.test(c ?? '')) && isBlank(fs[heatKey])) hidden.add(heatKey)
  if (!busCarriers.some(c => STEAM_BUS_RE.test(c ?? '')) && isBlank(fs[steamKey])) hidden.add(steamKey)
  return hidden
}
