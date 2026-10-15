// The generic site form's logic (G2): defaults that follow other values, the
// checks the backend applies said beside the field, and a request body that
// carries only what the user set. The fixture is the backend's own
// `eh_site.form_schema()` (test_eh_site_template pins the two equal).
import { describe, expect, it } from 'vitest'
import formJson from './siteFormFixture.json'
import {
  addStage, firstErrorField, initialDraft, isDefault, presetSummary, removeStage, requestBody,
  setEnergization, setField, setStage, validate, withSiteType, type SiteForm,
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
