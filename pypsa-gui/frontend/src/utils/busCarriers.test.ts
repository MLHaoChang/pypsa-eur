// The bus-carrier families the creation form's bus pickers filter by, and
// the 3D asset library matches on (WP1 review-gate finding 5): one
// definition, pinned against the sets CreationForm used before the move.
import { describe, it, expect } from 'vitest'
import { busCarrierFamily, carrierMatches, H2_CARRIER_RE, isH2Carrier } from './busCarriers'

// The pre-move sets, verbatim from CreationForm.tsx.
const H2 = new Set(['h2', 'hydrogen', 'h2 pipeline', 'h2_pipeline'])
const HEAT = new Set(['heat', 'heat-low', 'heat-high', 'urban heat', 'rural heat', 'urban central heat', 'urban decentral heat'])
const GAS = new Set(['gas', 'natural gas', 'biomass', 'biogas', 'oil', 'fuel'])
const ELEC = new Set(['ac', 'dc', 'electricity'])
const old = (c: string, want: string) => {
  const x = c.toLowerCase()
  switch (want) {
    case 'h2': return H2.has(x)
    case 'non-h2': return !H2.has(x)
    case 'electricity': return ELEC.has(x) || x === ''
    case 'heat': return HEAT.has(x) || x.includes('heat')
    default: return GAS.has(x) || x.includes('gas') || x.includes('biomass')
  }
}
const SAMPLES = ['AC', 'DC', 'electricity', '', 'H2', 'hydrogen', 'H2 pipeline', 'h2_pipeline', 'H2 Store', 'heat', 'urban central heat',
  'rural heat', 'heat-low', 'gas', 'natural gas', 'biogas', 'biomass', 'oil', 'fuel', 'co2', 'battery', 'Li ion', 'methanol']

describe('bus carrier families', () => {
  it.each(['h2', 'non-h2', 'electricity', 'heat', 'gas'] as const)('%s filter matches exactly what CreationForm matched', want => {
    for (const c of SAMPLES) expect(carrierMatches(c, want), `${c} / ${want}`).toBe(old(c, want))
  })
  it('names each bus carrier\'s family', () => {
    expect(['AC', 'H2 pipeline', 'urban central heat', 'natural gas', 'co2'].map(busCarrierFamily)).toEqual(['electricity', 'h2', 'heat', 'gas', 'other'])
  })
  it('an H₂ component carrier is H2 / hydrogen / H₂, alone or as a prefix (PropertiesPanel\'s rule)', () => {
    for (const c of ['H2', 'hydrogen', 'H₂', 'H2 Store', 'H2 storage', 'hydrogen storage', 'h2_store']) expect(isH2Carrier(c), c).toBe(true)
    for (const c of ['H2O', 'h20', 'chp', 'hydro', 'H2pipeline']) expect(isH2Carrier(c), c).toBe(false)
    for (const c of ['H2', 'H2 Store']) expect(H2_CARRIER_RE.test(c)).toBe(true)
  })
})
