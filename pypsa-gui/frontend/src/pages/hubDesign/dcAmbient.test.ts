import { describe, expect, it } from 'vitest'
import { ambientSourceText, validateAmbient, dcLoadBody, emptyAmbient, initialDcDraft } from './dcLoadForm'

describe('DC ambient source', () => {
  it('says the default curve is used when no location is set', () => {
    expect(ambientSourceText(null, emptyAmbient())).toMatch(/default temperate/)
  })
  it('says the site location is used when one is set', () => {
    expect(ambientSourceText(24.7, emptyAmbient())).toMatch(/from the site location\./)
  })
  it('says entered values win', () => {
    expect(ambientSourceText(24.7, { ...emptyAmbient(), mean: '30' })).toMatch(/your entries/)
  })
  it('sends the latitude and only the stated ambient fields', () => {
    const body = dcLoadBody(initialDcDraft(), 'eh_datacenter', { lat: 24.7, ambient: { ...emptyAmbient(), mean: '31' } })
    const dc = body.dc_load as Record<string, unknown>
    expect(dc.site_latitude).toBe(24.7)
    expect(dc.ambient_annual_mean_c).toBe(31)
    expect(dc).not.toHaveProperty('ambient_seasonal_amplitude_k')
  })
  it('sends no latitude without a location', () => {
    const dc = dcLoadBody(initialDcDraft(), 'eh_datacenter', { lat: null, ambient: emptyAmbient() }).dc_load as object
    expect(dc).not.toHaveProperty('site_latitude')
  })
  it('words a partial entry as entries plus the rest from the source', () => {
    expect(ambientSourceText(24.7, { ...emptyAmbient(), mean: '30' })).toMatch(/your entries, the rest from the site location/)
    expect(ambientSourceText(null, { ...emptyAmbient(), mean: '30' })).toMatch(/the rest from the default curve/)
  })
  it('refuses non-numeric and out-of-range entries, accepts blanks', () => {
    expect(validateAmbient(emptyAmbient())).toEqual({})
    const e = validateAmbient({ mean: 'abc', seasonal: '-1', diurnal: '5' })
    expect(Object.keys(e).sort()).toEqual(['mean', 'seasonal'])
  })
})
