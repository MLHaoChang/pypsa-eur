// The map draws ONE bubble per bus × category, so it must decide which icon is
// honest for a group of carriers. Testing badge IDENTITY rather than carrier
// strings is the point: onwind + offwind-ac is uniform (both are wind), while
// solar + onwind is not.
import { describe, expect, it } from 'vitest'
import { getCarrierBadge, uniformBadge } from './carrierBadges'

describe('getCarrierBadge', () => {
  it('resolves solar — the gap that made a solar plant render as wind', () => {
    expect(getCarrierBadge('solar').label).toBe('Solar')
    expect(getCarrierBadge('solar-rooftop').label).toBe('Solar')
  })

  it('resolves the other carriers this project uses', () => {
    expect(getCarrierBadge('onwind').label).toBe('Wind')
    expect(getCarrierBadge('offwind-ac').label).toBe('Wind')
    expect(getCarrierBadge('hydro').label).toBe('Hydro')
    expect(getCarrierBadge('nuclear').label).toBe('Nuclear')
    expect(getCarrierBadge('coal').label).toBe('Coal')
    expect(getCarrierBadge('biomass').label).toBe('Biomass')
    expect(getCarrierBadge('gas').label).toBe('Gas')
  })

  it('falls back to a truncated label for an unknown carrier', () => {
    expect(getCarrierBadge('unobtainium').label).toBe('unobt')
  })
})

describe('uniformBadge', () => {
  it('returns the badge when every carrier resolves to the same one', () => {
    expect(uniformBadge(['solar'])?.label).toBe('Solar')
    // Different strings, same badge — a turbine is honest for this group.
    expect(uniformBadge(['onwind', 'offwind-ac', 'offwind-dc'])?.label).toBe('Wind')
  })

  it('returns null when the group is mixed', () => {
    expect(uniformBadge(['solar', 'onwind'])).toBeNull()
    expect(uniformBadge(['gas', 'coal'])).toBeNull()
  })

  it('returns null for an empty group', () => {
    expect(uniformBadge([])).toBeNull()
  })
})
