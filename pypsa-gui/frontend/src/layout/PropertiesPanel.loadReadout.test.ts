// The load drawer's readout agrees with the section card (walkthrough issue 09):
// a loaded profile's peak and mean replace the stale static p_set, and PyPSA's
// `sign` is hidden in Guided and said in words in Expert.
import { describe, expect, it } from 'vitest'
import { LOAD_READOUT_WORDS, loadReadoutItems } from './PropertiesPanel'

const ITEMS = [
  { label: 'Demand', value: '0', unit: ' MW', field: 'p_set' },
  { label: 'Sign', value: '-1', field: 'sign' },
]

describe('loadReadoutItems', () => {
  it('says the profile suffix from the shared words', () => {
    expect(LOAD_READOUT_WORDS.profileSuffix).toBe('(from the profile)')
    expect(loadReadoutItems(ITEMS, { peak: 1, mean: 1 }, 'guided')[0].value).toContain(LOAD_READOUT_WORDS.profileSuffix)
  })

  it('shows the profile peak and mean instead of 0 MW', () => {
    const out = loadReadoutItems(ITEMS, { peak: 34.49, mean: 32.2 }, 'guided')
    expect(out).toHaveLength(1)
    expect(out[0].value).toBe('34.5 MW peak · 32.2 MW mean (from the profile)')
    expect(out[0].unit).toBeUndefined()
  })

  it('keeps the static demand when no profile is loaded', () => {
    expect(loadReadoutItems(ITEMS, null, 'guided')[0].value).toBe('0')
  })

  it('says the sign in words in Expert', () => {
    const out = loadReadoutItems(ITEMS, null, 'expert')
    expect(out[1].value).toBe(LOAD_READOUT_WORDS.sign['-1'])
    expect(out[1].value).toBe('consumes power')
    expect(loadReadoutItems([{ label: 'Sign', value: '1', field: 'sign' }], null, 'expert')[0].value).toBe('supplies power')
  })
})
