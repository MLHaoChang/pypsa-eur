import { describe, expect, it } from 'vitest'
import {
  UTILISATION_OPTIONS, MIX_FIELDS, MIX_DEFAULTS, computeMismatch, derivedMw, dcLoadBody, dcFieldOf,
  initialDcDraft, validateDc, emptyAmbient,
} from './dcLoadForm'

const body = (d = initialDcDraft(), t = 'eh_datacenter') =>
  (dcLoadBody(d, t, { lat: null, ambient: emptyAmbient() }).dc_load as Record<string, any>)

describe('workload select', () => {
  it('shows plain words, never the engine values', () => {
    expect(UTILISATION_OPTIONS.map(o => o[1])).toEqual(['AI training', 'Inference', 'Mixed', 'Flat'])
  })
  it('sends only the chosen kind, and the mix only for non-training kinds', () => {
    expect(body().utilisation).toBeUndefined() // the preset's ai_training: the default body is unchanged
    expect(body().workload_mix).toBeUndefined()
    const d = { ...initialDcDraft(), utilisation: 'inference' as const }
    expect(body(d).utilisation).toBe('inference')
    expect(body(d).workload_mix).toEqual({ trough_share: 0.4, peak_share: 0.85, peak_hour: 20 })
  })
  it('refuses out-of-range shares and a trough above the peak, opening with the label', () => {
    const d = { ...initialDcDraft(), utilisation: 'inference' as const, mix: { ...MIX_DEFAULTS, peak: '1.2' } }
    expect(validateDc(d, 'eh_datacenter').workload).toMatch(/^Workload: /)
    const e = { ...initialDcDraft(), utilisation: 'inference' as const, mix: { ...MIX_DEFAULTS, trough: '0.9', peak: '0.5' } }
    expect(validateDc(e, 'eh_datacenter').workload).toMatch(/lowest load must not exceed/)
    expect(validateDc({ ...initialDcDraft(), utilisation: 'inference' }, 'eh_datacenter').workload).toBeUndefined()
  })
  it('every non-training kind has its inputs', () => {
    for (const [v] of UTILISATION_OPTIONS) expect(MIX_FIELDS[v].length > 0).toBe(v !== 'ai_training')
  })
  it('maps backend refusals to the field', () => {
    expect(dcFieldOf('dc_load.workload_mix.peak_hour')).toBe('workload')
    expect(dcFieldOf('dc_load.compute.accelerator_counts')).toBe('compute')
  })
})

describe('compute block', () => {
  it('derives MW and stays off the body until used', () => {
    expect(body().compute).toBeUndefined()
    const d = initialDcDraft()
    d.compute = { counts: ['10000'], kw: '1.5', util: '0.8' }
    expect(derivedMw(d.compute, 0)).toBeCloseTo(12)
    expect(body(d).compute).toEqual({ kw_per_accelerator: 1.5, utilisation: 0.8, accelerator_counts: [10000] })
  })
  it('a half-filled block is refused, opening with its label', () => {
    const d = initialDcDraft()
    d.compute = { counts: [''], kw: '1.5', util: '' }
    expect(validateDc(d, 'eh_datacenter').compute).toMatch(/^Compute: /)
  })
  it('notes a mismatch only above 10 %, naming both figures', () => {
    expect(computeMismatch(11, 10)).toBeNull()
    expect(computeMismatch(11.01, 10)).toBe('Compute gives 11.0 MW. The typed IT load is 10.0 MW and is the one used.')
    expect(computeMismatch(11.01, 10, true)).toMatch(/The planned IT load is 10\.0 MW/)
    expect(computeMismatch(NaN, 10)).toBeNull()
  })
})
