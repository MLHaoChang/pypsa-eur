// GK B3 K19: the Expert raw editor sends only the requirement leaves that changed, as plain values.
import { describe, expect, it } from 'vitest'
import { requirementsDiff } from './islandRequirementsDiff'

const a = (value: unknown) => ({ value, source: 'assumed' })
const SAVED = {
  scenarios: ['ups_bridged'], sustained_h: a(24), linearised_uc: false, pickup_blocks: a([1, 2]),
  frequency_limits: { transition: { f_min_hz: a(47.5), f_max_hz: a(51.5) } },
}
const copy = () => JSON.parse(JSON.stringify(SAVED))

describe('requirementsDiff', () => {
  it('an unchanged save sends nothing', () => {
    expect(requirementsDiff(SAVED, copy())).toEqual({ patch: {}, refusals: [] })
  })
  it('a changed tagged value goes as its plain value, a changed plain one as itself', () => {
    const e = copy(); e.sustained_h.value = 12; e.linearised_uc = true; e.scenarios = ['seamless']
    expect(requirementsDiff(SAVED, e)).toEqual({ patch: { sustained_h: 12, linearised_uc: true, scenarios: ['seamless'] },
      refusals: [] })
  })
  it('a typed plain number in place of a tagged one is that value', () => {
    const e = copy(); e.sustained_h = 6
    expect(requirementsDiff(SAVED, e).patch).toEqual({ sustained_h: 6 })
    const same = copy(); same.sustained_h = 24
    expect(requirementsDiff(SAVED, same).patch).toEqual({})
  })
  it('a nested limit sends only its changed leaf', () => {
    const e = copy(); e.frequency_limits.transition.f_min_hz.value = 48
    expect(requirementsDiff(SAVED, e).patch).toEqual({ frequency_limits: { transition: { f_min_hz: 48 } } })
  })
  it('a list inside a tag is replaced whole', () => {
    const e = copy(); e.pickup_blocks.value = [3]
    expect(requirementsDiff(SAVED, e).patch).toEqual({ pickup_blocks: [3] })
  })
  it('refuses an edited source, at any depth, in words', () => {
    const e = copy(); e.sustained_h.source = 'measured'; e.frequency_limits.transition.f_max_hz.source = 'datasheet'
    expect(requirementsDiff(SAVED, e).refusals).toEqual([
      'Sustained ride-through: the server sets sources. Enter the value only.',
      'The server sets sources. Enter the value only. (frequency_limits.transition.f_max_hz)',
    ])
  })
  it('refuses a new field sent with a source, a tag swapped for a mapping, and a removal', () => {
    const e = copy(); e.bridge_s = a(5); e.sustained_h = { v: 1 }; delete e.linearised_uc
    expect(requirementsDiff(SAVED, e).refusals.sort()).toEqual([
      'A setting cannot be removed here. (linearised_uc)',
      'Sustained ride-through: enter the value only.',
      'UPS bridge: the server sets sources. Enter the value only.',
    ])
  })
  it('a tag with an extra key is not a tag: it is refused, not sent', () => {
    const e = copy(); e.sustained_h = { value: 5, source: 'assumed', note: 'x' }
    expect(requirementsDiff(SAVED, e)).toEqual({ patch: {}, refusals: ['Sustained ride-through: enter the value only.'] })
  })
  it('a new plain field is sent, and null is refused', () => {
    const e = copy(); e.bridge_s = 5; e.scenarios = null
    const d = requirementsDiff(SAVED, e)
    expect(d.patch).toEqual({ bridge_s: 5 })
    expect(d.refusals).toEqual(['A setting cannot be removed here. (scenarios)'])
  })
})
