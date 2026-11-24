// The generic site form's logic (G2): defaults that follow other values, the
// checks the backend applies said beside the field, and a request body that
// carries only what the user set. The fixture is the backend's own
// `eh_site.form_schema()` (test_eh_site_template pins the two equal).
import { describe, expect, it } from 'vitest'
import formJson from './siteFormFixture.json'
import {
  addStage, firstErrorField, initialDraft, isDefault, presetSummary, removeStage, requestBody,
  setEnergization, setField, setStage, validate, withSiteType, type SiteForm,
  isFilledFromRate, resetStageToRate, setGrowth,
} from './genericSite'

const FORM = formJson as unknown as SiteForm
const B = FORM.bounds

describe('generic site form logic', () => {
  it('every preset is valid as shipped, and every value starts as a default', () => {
    for (const t of FORM.site_types) {
      const d = initialDraft(FORM, t.id)
      expect({ t: t.id, errors: validate(d, B) }).toEqual({ t: t.id, errors: {} })
      expect(d.values).toEqual(FORM.presets[t.id])          // the client derives as the server
      expect(isDefault(d, 'connection_mw')).toBe(true)
      expect(requestBody(d)).toEqual({ site_type: t.id })
    }
  })

  it('a peak above the grid available that year is refused on its peak field', () => {
    let d = initialDraft(FORM, 'hospital')
    d = setEnergization(d, 1, 4.5)
    const e = validate(d, B)
    expect(e['stages[1].peak_mw']).toBe(
      'Peak load in 2030 (5 MW) is above the 4.5 MW available from the grid that year. '
      + 'Raise the connection or lower the peak.')
    expect(Object.keys(e)).toEqual(['stages[1].peak_mw'])
  })

  it('stage years must ascend, said in plain words', () => {
    const d = setStage(initialDraft(FORM), 1, 'year', 2026)
    expect(validate(d, B)['stages[1].year']).toBe('2026 must come after 2027.')
  })

  it('the UPS, the energization and the fault level follow until the user sets them', () => {
    let d = initialDraft(FORM, 'hospital')
    d = setField(d, 'connection_mw', 10)
    expect(d.values.energization.map(e => e.mw)).toEqual([10, 10, 10])
    expect(d.values.fault_level_mva).toBe(200)
    d = setStage(d, 2, 'peak_mw', 8)
    expect(d.values.existing.ups.mw).toBeCloseTo(1.1 * 0.5 * 8)
    d = setField(d, 'existing.ups.mw', 5)
    d = setStage(d, 2, 'peak_mw', 9)
    expect(d.values.existing.ups.mw).toBe(5)                 // set: it stays
    expect(isDefault(d, 'existing.ups.mw')).toBe(false)
    expect(isDefault(d, 'stages[2].peak_mw')).toBe(false)     // a list is one value
  })

  it('an energization the user set keeps its years when a stage is added', () => {
    let d = setEnergization(initialDraft(FORM, 'industrial'), 0, 15)
    d = addStage(removeStage(d, 2), B)
    expect(d.values.stages.map(s => s.year)).toEqual([2027, 2030, 2033])
    expect(d.values.energization).toEqual([
      { year: 2027, mw: 15 }, { year: 2030, mw: 20 }, { year: 2033, mw: 20 }])
  })

  it('switching the site type keeps what the user set and takes the rest from the preset', () => {
    let d = setField(initialDraft(FORM, 'industrial'), 'connection_mw', 30)
    d = withSiteType(FORM, d, 'hospital')
    expect(d.values.site_type).toBe('hospital')
    expect(d.values.connection_mw).toBe(30)
    expect(d.values.existing.gensets).toEqual(FORM.presets.hospital.existing.gensets)
    expect(requestBody(d)).toEqual({ site_type: 'hospital', connection_mw: 30 })
  })

  it('the body nests the paths the user set', () => {
    let d = setField(initialDraft(FORM), 'existing.ups.minutes', 20)
    d = setField(d, 'candidates.pv.enabled', false)
    d = setStage(d, 0, 'peak_mw', 9)
    expect(requestBody(d)).toEqual({
      site_type: 'industrial',
      existing: { ups: { minutes: 20 } },
      candidates: { pv: { enabled: false } },
      stages: [{ year: 2027, peak_mw: 9 }, { year: 2030, peak_mw: 12 }, { year: 2035, peak_mw: 15 }],
    })
  })

  it('a cleared field stays blank (NaN), never refilled by its default', () => {
    const d = setEnergization(initialDraft(FORM), 1, NaN)
    expect(d.values.energization[1].mw).toBeNaN()
    expect(validate(d, B)['energization[1].mw']).toBe('Enter a number.')
  })

  it('field checks mirror the backend bounds', () => {
    let d = setField(initialDraft(FORM), 'existing.gensets.count', 9)
    d = setField(d, 'existing.battery.mw', 2)
    d = setField(d, 'existing.ups.minutes', 5)
    let e = validate(d, B)
    expect(e['existing.gensets.count']).toBe('A whole number from 0 to 8.')
    expect(e['existing.battery.mwh']).toBe('Give both the power and the energy, or neither.')
    expect(e['existing.ups.minutes']).toBe('At least 10 min, until the gensets carry the load.')
    d = setField(d, 'critical_share', NaN)
    e = validate(d, B)
    expect(e.critical_share).toBe('Enter a number.')
    expect(e['existing.ups.mw']).toBeUndefined()          // not on a field the user never set
    expect(firstErrorField(e)).toBe('critical_share')
  })

  it('the preset line rounds through the tile formatter', () => {
    expect(presetSummary(FORM.presets.hospital, x => String(x))).toBe(
      'Peak 4 → 5 → 6 MW, 8 MW connection, 3 diesel gensets of 2 MW, 0.3 MW solar PV, '
      + 'a UPS on the must-stay-on load.')
  })
})

// homer-parity 04a2 (owner decision LOADGROWTH-FORM-1): "Grow from the first
// stage at X % per year" fills the later stages' peaks; a typed peak wins.
describe('grow from the first stage', () => {
  const peaks = (d: ReturnType<typeof initialDraft>) => d.values.stages.map(s => s.peak_mw)
  // industrial: 8 MW in 2027, stages in 2030 and 2035.
  it('fills every later stage compounded from the first stage year (hand-checked)', () => {
    const d = setGrowth(initialDraft(FORM, 'industrial'), 5)
    expect(peaks(d)).toEqual([8, 9.26, 11.82])           // 8·1.05^3, 8·1.05^8
    expect([0, 1, 2].map(i => isFilledFromRate(d, i))).toEqual([false, true, true])
    expect(requestBody(d)).toEqual({ site_type: 'industrial', stages: d.values.stages })
  })

  it('the rate is a form helper only: it is never in the request body', () => {
    const d = setGrowth(initialDraft(FORM, 'industrial'), 5)
    expect(JSON.stringify(requestBody(d))).not.toMatch(/growth|rate/i)
  })

  it('a typed peak wins for its stage and the reset gives it back to the rate', () => {
    let d = setGrowth(initialDraft(FORM, 'industrial'), 5)
    d = setStage(d, 1, 'peak_mw', 10)
    expect(peaks(d)).toEqual([8, 10, 11.82])
    expect(isFilledFromRate(d, 1)).toBe(false)
    d = setGrowth(d, 10)
    expect(peaks(d)).toEqual([8, 10, 17.15])             // 8·1.1^8 = 17.148…
    d = resetStageToRate(d, 1)
    expect(peaks(d)).toEqual([8, 10.65, 17.15])          // 8·1.1^3 = 10.648
    expect(isFilledFromRate(d, 1)).toBe(true)
  })

  it('a new first peak or a moved year refills the untyped stages', () => {
    let d = setGrowth(initialDraft(FORM, 'industrial'), 5)
    d = setStage(d, 0, 'peak_mw', 10)
    expect(peaks(d)).toEqual([10, 11.58, 14.77])
    d = setStage(d, 2, 'year', 2031)
    expect(peaks(d)).toEqual([10, 11.58, 12.16])         // 10·1.05^4 = 12.155
    expect(isFilledFromRate(d, 2)).toBe(true)            // a year is not a typed peak
  })

  it('an added stage is filled; a removed stage keeps the others typed or filled', () => {
    let d = removeStage(initialDraft(FORM, 'industrial'), 2)
    d = setGrowth(setStage(d, 1, 'peak_mw', 9), 5)
    d = addStage(d, B)
    expect(d.values.stages.map(s => s.year)).toEqual([2027, 2030, 2033])
    expect(peaks(d)).toEqual([8, 9, 10.72])              // 8·1.05^6 = 10.720
    d = removeStage(d, 1)
    expect(isFilledFromRate(d, 1)).toBe(true)
  })

  it('clearing the rate leaves the filled peaks as ordinary values', () => {
    let d = setGrowth(initialDraft(FORM, 'industrial'), 5)
    d = setGrowth(d, null)
    expect(peaks(d)).toEqual([8, 9.26, 11.82])
    expect([1, 2].map(i => isFilledFromRate(d, i))).toEqual([false, false])
    d = setStage(d, 0, 'peak_mw', 20)
    expect(peaks(d)).toEqual([20, 9.26, 11.82])
  })

  it('a rate at or below -100 % or not a number is refused beside the field and fills nothing', () => {
    for (const g of [-100, -150, NaN]) {
      const d = setGrowth(initialDraft(FORM, 'industrial'), g)
      expect(validate(d, B).growth_pct).toBe(
        'Grow from the first stage: enter a number above -100 % per year.')
      expect(peaks(d)).toEqual([8, 12, 15])
    }
    expect(validate(setGrowth(initialDraft(FORM, 'industrial'), -5), B).growth_pct).toBeUndefined()
    // One stage hides the row, so its rate cannot block the form unseen.
    const one = removeStage(removeStage(setGrowth(initialDraft(FORM, 'industrial'), NaN), 2), 1)
    expect(validate(one, B).growth_pct).toBeUndefined()
  })

  it('a filled peak over the grid that year shows on that stage\'s peak field', () => {
    const d = setGrowth(initialDraft(FORM, 'industrial'), 200)
    expect(Object.keys(validate(d, B))).toContain('stages[2].peak_mw')
  })
})
