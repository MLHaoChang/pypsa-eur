import { describe, expect, it } from 'vitest'
import { browseEntries, browseItems, defaultText, placeOf, relatedEntries, resolveGuideId, splitGuide, typicalText, usedBy } from './guideModel'
import { entry } from './testEntries'
import { CAP, CATALOGUE, LIMIT, LINE, MAX } from './guideFixture'

describe('guide entry rows', () => {
  it('splits the guide into "What it means" and "Example"', () => {
    expect(splitGuide('## What it means\n\nThe largest output.\n\n## Example\n\nA 3 MWp roof.\n'))
      .toEqual({ meaning: 'The largest output.', example: 'A 3 MWp roof.' })
    expect(splitGuide('Only words.')).toEqual({ meaning: 'Only words.', example: '' })
    expect(splitGuide(null)).toEqual({ meaning: '', example: '' })
  })

  it('splits "Used by" into the functions that need it and those that only read it', () => {
    expect(usedBy(CAP, CATALOGUE)).toEqual({ needed: ['Dispatch'], also: ['Load flow', 'Finance'] })
    expect(usedBy(LINE, CATALOGUE)).toEqual({ needed: [], also: [] })
  })

  it('a default reads in display words; none is left out', () => {
    expect(defaultText(CAP)).toBe('Built-in default 0 MW')
    expect(defaultText(MAX)).toBe('Built-in default No limit')
    expect(defaultText(LINE)).toBeNull()
    expect(defaultText(entry({ id: 'a.b', plain_name: 'X' }))).toBeNull()
    expect(defaultText({ ...CAP, default: { value: 1, source: 'library' } })).toBe('Generic default 1 MW (Assumed)')
  })

  it('typical values come from the range; no range is left out', () => {
    expect(typicalText(CAP)).toBeNull()
    expect(typicalText({ ...CAP, range: null })).toBeNull()
    expect(typicalText({ ...CAP, range: { typical: [0.1, 50], min: 0 } })).toBe('0.1–50 MW · hard limit ≥ 0 MW')
    expect(typicalText({ ...CAP, range: { min: 0, max: 1 } })).toBe('hard limit 0–1 MW')
  })

  it('related entries skip ids the catalogue does not hold', () => {
    expect(relatedEntries(CAP, CATALOGUE).map(e => e.id)).toEqual(['generator.max'])
  })

  it('names where an entry sits in plain words', () => {
    expect(placeOf(CAP, CATALOGUE)).toBe('Generator · Size and limits')
    expect(placeOf(LIMIT, CATALOGUE)).toBe('Global constraint · Size and limits')
  })
})

describe('guide ids', () => {
  it('resolves entries, former ids, and asset type, group and function ids', () => {
    expect(resolveGuideId(CATALOGUE, 'generator.capacity')).toEqual({ kind: 'entry', entry: CAP })
    expect(resolveGuideId(CATALOGUE, 'generator.p_nom')).toEqual({ kind: 'entry', entry: CAP })
    expect(resolveGuideId(CATALOGUE, 'asset_type.pvRoof')).toEqual({ kind: 'browse', tab: 'type', item: 'pvRoof' })
    expect(resolveGuideId(CATALOGUE, 'group.investment')).toEqual({ kind: 'browse', tab: 'group', item: 'investment' })
    expect(resolveGuideId(CATALOGUE, 'function.load_flow')).toEqual({ kind: 'browse', tab: 'function', item: 'load_flow' })
    expect(resolveGuideId(CATALOGUE, 'function.nope')).toBeNull()
    expect(resolveGuideId(CATALOGUE, 'asset_type.lost')).toBeNull()
    expect(resolveGuideId(CATALOGUE, 'nothing.here')).toBeNull()
  })
})

describe('browse', () => {
  it('lists asset types but not internal ones, and groups in their order', () => {
    expect(browseItems(CATALOGUE, 'type').map(i => i.id)).toEqual(['pvRoof'])
    expect(browseItems(CATALOGUE, 'group').map(i => i.id)).toEqual(['size_limits', 'investment'])
  })

  it('a function lists what it needs, then what it also reads', () => {
    expect(browseEntries(CATALOGUE, 'function', 'dispatch')).toEqual([{ title: 'Needed', entries: [CAP] }])
    expect(browseEntries(CATALOGUE, 'function', 'load_flow')).toEqual([{ title: 'Also used', entries: [CAP] }])
  })

  it('an asset type lists its entries by group; a group by class', () => {
    expect(browseEntries(CATALOGUE, 'type', 'pvRoof')).toEqual([
      { title: 'Size and limits', entries: [CAP] }, { title: 'Investment', entries: [MAX] }])
    expect(browseEntries(CATALOGUE, 'group', 'size_limits')).toEqual([
      { title: 'Generator', entries: [CAP] }, { title: 'Line', entries: [LINE] }, { title: 'Global constraint', entries: [LIMIT] }])
  })
})
