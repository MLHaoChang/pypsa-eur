import { describe, it, expect } from 'vitest'
import { readActiveSite, writeActiveSite, activeSiteKey } from './activeSite'

describe('active site persistence', () => {
  it('is per project and clears on null', () => {
    writeActiveSite('p', 'site_a')
    writeActiveSite('q', 'site_b')
    expect(readActiveSite('p')).toBe('site_a')
    expect(readActiveSite('q')).toBe('site_b')
    expect(readActiveSite(null)).toBeNull()
    writeActiveSite('p', null)
    expect(readActiveSite('p')).toBeNull()
    expect(activeSiteKey('p')).toBe('network-diagram:active-site:p')
  })
})
