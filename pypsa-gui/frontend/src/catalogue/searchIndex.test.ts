import { describe, expect, it } from 'vitest'
import { buildParameterIndex } from './searchIndex'
import { entry } from './testEntries'

// Spec expert-catalogue §4.3 / §10.1 "Search": the ranking table.
const ENTRIES = [
  entry({ id: 'generator.p_nom', plain_name: 'Capacity', aliases: ['Rated power', 'Nameplate'],
    short_help: 'The largest output the asset can deliver.', guide: '## What it means\n\nThe **nameplate** size in MW.' }),
  entry({ id: 'generator.p_nom_max', plain_name: 'Largest capacity', depth: 'advanced',
    short_help: 'Upper bound on the size the optimiser may choose.' }),
  entry({ id: 'generator.marginal_cost', plain_name: 'Running cost', group: 'running_cost',
    aliases: ['Marginal cost'], short_help: 'Cost per MWh produced.',
    guide: 'Fuel and wear per unit of energy; the dispatch order follows it.' }),
  entry({ id: 'line.s_nom', plain_name: 'Thermal rating', scope: 'Line', aliases: ['Capacity'],
    functions: { load_flow: 'needs' } }),
]
const index = buildParameterIndex(ENTRIES)
const ids = (q: string, lens?: string) => index.search(q, { lens }).map(h => h.entry.id)
const via = (q: string, id: string) => index.search(q).find(h => h.entry.id === id)?.via

describe('parameter search ranking', () => {
  it.each([
    ['capacity', 'generator.p_nom', 'plain'],
    ['Rated power', 'generator.p_nom', 'alias'],
    ['marginal cost', 'generator.marginal_cost', 'alias'],
    ['p_nom', 'generator.p_nom', 'model'],
    ['largest cap', 'generator.p_nom_max', 'prefix'],
    ['run', 'generator.marginal_cost', 'prefix'],
    ['per mwh', 'generator.marginal_cost', 'help'],
    ['dispatch order', 'generator.marginal_cost', 'guide'],
  ])('%s finds %s by %s', (q, id, how) => {
    expect(via(q, id)).toBe(how)
  })

  it('an exact plain name ranks above an alias, which ranks above a word prefix', () => {
    expect(ids('capacity')).toEqual(['generator.p_nom', 'line.s_nom', 'generator.p_nom_max'])
  })

  it('the model-file name finds the entry without any other match', () => {
    expect(ids('p_nom')).toEqual(['generator.p_nom'])
    expect(ids('marginal_cost')[0]).toBe('generator.marginal_cost')
  })

  it('guide text ranks below a name match', () => {
    expect(ids('nameplate')).toEqual(['generator.p_nom'])
    expect(via('size', 'generator.p_nom')).toBe('guide')
    expect(ids('size')).toEqual(['generator.p_nom_max', 'generator.p_nom'])
  })

  it('a short query matches names only, never help or guide text', () => {
    expect(ids('mw')).toEqual([])
  })

  it('one query across all four tiers: exact plain name, alias, word prefix, guide text (GK B3 C3)', () => {
    const tiers = buildParameterIndex([
      entry({ id: 'd.guide', plain_name: 'Stack', guide: 'Wear grows with the hours run.' }),
      entry({ id: 'c.prefix', plain_name: 'Hours run per year' }),
      entry({ id: 'b.alias', plain_name: 'Lifetime in running hours', aliases: ['Hours run'] }),
      entry({ id: 'a.plain', plain_name: 'Hours run' }),
    ])
    expect(tiers.search('hours run').map(h => [h.entry.id, h.via])).toEqual([
      ['a.plain', 'plain'], ['b.alias', 'alias'], ['c.prefix', 'prefix'], ['d.guide', 'guide'],
    ])
  })

  it('ties break by depth, then plain name, then id, whatever the input order', () => {
    const tied = [
      entry({ id: 'z.cap', plain_name: 'Capacity', depth: 'advanced' }),
      entry({ id: 'y.cap', plain_name: 'Capacity' }),
      entry({ id: 'x.cap', plain_name: 'Capacity' }),
    ]
    const order = ['x.cap', 'y.cap', 'z.cap']
    expect(buildParameterIndex(tied).search('capacity').map(h => h.entry.id)).toEqual(order)
    expect(buildParameterIndex([...tied].reverse()).search('capacity').map(h => h.entry.id)).toEqual(order)
  })

  it('with a lens, the entries that function needs come first', () => {
    expect(ids('capacity', 'load_flow')[0]).toBe('line.s_nom')
  })

  it('an empty query finds nothing; limit caps the hits', () => {
    expect(ids('  ')).toEqual([])
    expect(index.search('capacity', { limit: 1 })).toHaveLength(1)
  })
})
