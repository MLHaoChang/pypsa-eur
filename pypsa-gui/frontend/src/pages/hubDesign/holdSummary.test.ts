// guided-results 03: one summary line above a Guided section's list, read
// from the flow's `holds` (display only), and section 5's fixed-capacity bars.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { FlowHeld } from '../../api/flow'
import { SERIES_WORDS } from './holdWords'
import { LOAD_PROFILES, fixedCharts, holdSummary, isHelper, seriesWord } from './holdSummary'

const src = (rel: string) => readFileSync(resolve(__dirname, rel), 'utf8')
const held = (over: Partial<Omit<FlowHeld, 'helper'>> & { helper?: unknown }): FlowHeld => ({
  component: 'Generator', entity: 'pv', carrier: 'solar', field: 'p_nom', value: 5, unit: 'MW',
  extendable: false, series: false, ...over,
} as FlowHeld)
const carrier = (c: string) => held({ component: 'Carrier', entity: c, carrier: null, field: null, value: null, unit: null })

describe('the summary line', () => {
  it('carriers: count and names, commas inside the field', () => {
    expect(holdSummary('carriers', ['electricity', 'heat', 'hydrogen'].map(carrier)))
      .toBe('3 carriers: electricity, heat, hydrogen')
    expect(holdSummary('carriers', [carrier('electricity')])).toBe('1 carrier: electricity')
  })

  it('loads: the largest peak, never a sum of peaks', () => {
    const loads = [held({ component: 'Load', entity: 'a', field: 'p_set', value: 40 }),
      held({ component: 'Load', entity: 'b', field: 'p_set', value: 25 })]
    expect(holdSummary('loads', loads)).toBe('2 loads · largest peak 40 MW')
  })

  it('loads in different units list each largest peak, never added', () => {
    const loads = [held({ component: 'Load', field: 'p_set', value: 40 }),
      held({ component: 'Load', field: 'p_set', value: 3, unit: 't/h' }),
      held({ component: 'Load', field: 'p_set', value: null, series: true })]
    expect(holdSummary('loads', loads)).toBe('3 loads · largest peak 40 MW, 3 t/h')
  })

  it('assets: one sum per field and unit, extendable counted apart', () => {
    const assets = [held({ value: 40 }), held({ entity: 'wind', carrier: 'wind', value: 12 }),
      held({ component: 'Store', field: 'e_nom', value: 80, unit: 'MWh' }),
      held({ entity: 'ely', component: 'Link', value: 4, unit: 't/h' }),
      held({ entity: 'bat', extendable: true, value: 99 })]
    expect(holdSummary('assets', assets)).toBe('5 assets · 52 MW, 80 MWh, 4 t/h fixed, 1 sized by the study')
  })

  it('helpers stay in the count, are said, and are left out of the sums', () => {
    const assets = [held({ value: 40 }), held({ entity: '__lost_steel', value: 1e6, helper: true })]
    expect(holdSummary('assets', assets)).toBe('2 assets incl. 1 helper · 40 MW fixed')
  })

  it('a missing or non-boolean helper reads as not a helper, never crashes', () => {
    for (const v of [undefined, null, 'true', 1, {}]) expect(isHelper(held({ helper: v }))).toBe(false)
    expect(isHelper(held({ helper: true }))).toBe(true)
    expect(holdSummary('assets', [held({ helper: 'yes' }), held({ helper: null })])).toBe('2 assets · 10 MW fixed')
  })

  it('a size with no known unit is never added to another', () => {
    expect(holdSummary('assets', [held({ value: 5 }), held({ value: 7, unit: null })]))
      .toBe('2 assets · 5 MW fixed, 1 with no known unit')
  })

  it('profiles: the panels\' words', () => {
    const rows = [held({ component: 'Load', field: 'p_set' }), held({ component: 'Load', entity: 'b', field: 'p_set' }),
      ...['a', 'b', 'c'].map(e => held({ entity: e, field: 'p_max_pu' }))]
    expect(holdSummary('profiles', rows)).toBe('5 series · 2 load profiles, 3 availability')
  })

  it('an empty section has no line: never "0 assets · 0 MW fixed"', () => {
    for (const id of ['carriers', 'loads', 'assets', 'profiles'] as const) expect(holdSummary(id, [])).toBeNull()
  })

  it('a capped list says the line reads only the rows it has', () => {
    expect(holdSummary('assets', [held({ value: 5 })], 300)).toBe('300 assets · 5 MW fixed · from the first 1 of 300')
  })

  it('says no state word, so it never contradicts the state dot', () => {
    const line = holdSummary('assets', [held({}), held({ extendable: true })]) ?? ''
    expect(line).not.toMatch(/complete|to do|to check|ready|missing|done/i)
  })
})

describe('the words match the panels that own them', () => {
  it('a series word is the Time Series slot label; a load\'s is the Load Profiles panel', () => {
    for (const [field, word] of Object.entries(SERIES_WORDS)) {
      if (field !== 'p_set') expect(seriesWord(held({ field }))).toBe(word.toLowerCase())
    }
    expect(seriesWord(held({ component: 'Load', field: 'p_set' }))).toBe(LOAD_PROFILES)
    expect(src('../LoadProfileManager.tsx').toLowerCase()).toContain(`>${LOAD_PROFILES}<`)
  })
  it('never a raw field', () => {
    expect(seriesWord(held({ field: 'q_set_unknown' }))).toBe('other')
  })
})

describe('section 5 bars', () => {
  it('one chart per unit, one bar per carrier, helpers and extendable left out', () => {
    const charts = fixedCharts([held({ value: 30 }), held({ entity: 'pv2', value: 10 }),
      held({ entity: 'wind', carrier: 'wind', value: 12 }),
      held({ entity: 'ely', component: 'Link', carrier: 'hydrogen', value: 4, unit: 't/h' }),
      held({ entity: 'ely2', component: 'Link', carrier: 'ammonia', value: 2, unit: 't/h' }),
      held({ entity: '__buy', value: 1e6, helper: true }), held({ entity: 'bat', extendable: true, value: 9 })])
    expect(charts).toEqual([
      { unit: 'MW', bars: [{ carrier: 'solar', value: 40 }, { carrier: 'wind', value: 12 }] },
      { unit: 't/h', bars: [{ carrier: 'hydrogen', value: 4 }, { carrier: 'ammonia', value: 2 }] },
    ])
  })
  it('no chart for one carrier in a unit', () => {
    expect(fixedCharts([held({}), held({ entity: 'x', unit: 't/h', carrier: 'h2' })])).toEqual([])
  })
})
