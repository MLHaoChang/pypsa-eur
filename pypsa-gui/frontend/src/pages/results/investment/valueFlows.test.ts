import { describe, expect, it } from 'vitest'
import type { ValueFlowsPayload } from '../../../api/commercial'
import {
  conservationState, csvRows, participantRows, periodKeys, sankeyData, streamLabel,
} from './valueFlows'

// A developer and a DSO paying each other (a two-way pair): the Sankey stays
// bipartite — `p:` payer nodes on the left, `r:` payee nodes on the right.
export const TWO_WAY: ValueFlowsPayload = {
  status: 'ok', template: 'dso_developer',
  participants: [{ id: 'developer', name: 'Developer', role: 'developer' },
                 { id: 'dso', name: 'Netz AG', role: 'dso' }],
  externals: ['retailer', 'market'],
  conservation_ok: true, flags: [], notes: [],
  periods: {
    _: {
      lines: [],
      by_participant: {
        developer: { paid: 1100, received: 300, net: -800,
                     by_stream: { energy_import: -1000, dr_availability: 300, network_capacity: -100 } },
        dso: { paid: 300, received: 100, net: -200,
               by_stream: { dr_availability: -300, network_capacity: 100 } },
        retailer: { paid: 0, received: 1000, net: 1000, by_stream: { energy_import: 1000 } },
      },
      sankey: {
        nodes: [
          { id: 'p:developer', label: 'Developer', side: 'payer', internal: true },
          { id: 'p:dso', label: 'Netz AG', side: 'payer', internal: true },
          { id: 'r:dso', label: 'Netz AG', side: 'payee', internal: true },
          { id: 'r:developer', label: 'Developer', side: 'payee', internal: true },
          { id: 'r:retailer', label: 'retailer', side: 'payee', internal: false },
        ],
        links: [
          { source: 'p:developer', target: 'r:retailer', value: 1000, stream: 'energy_import' },
          { source: 'p:developer', target: 'r:dso', value: 100, stream: 'network_capacity' },
          { source: 'p:dso', target: 'r:developer', value: 300, stream: 'dr_availability' },
        ],
      },
      conservation: { ok: true, checks: [{ name: 'coverage', ok: true, detail: [] }] },
      disclosures: {},
    },
  },
}

describe('value-flow mappings', () => {
  it('maps the bipartite Sankey ids to indices, dropping dangling links', () => {
    const s = TWO_WAY.periods!._.sankey
    const { nodes, links } = sankeyData({
      ...s, links: [...s.links, { source: 'p:ghost', target: 'r:dso', value: 5, stream: 'x' }],
    })
    expect(nodes).toHaveLength(5)
    expect(links).toEqual([
      { source: 0, target: 4, value: 1000, stream: 'energy_import' },
      { source: 0, target: 2, value: 100, stream: 'network_capacity' },
      { source: 1, target: 3, value: 300, stream: 'dr_availability' },
    ])
    // A DAG: no node index is both a source and a target.
    const src = new Set(links.map(l => l.source))
    expect(links.every(l => !src.has(l.target))).toBe(true)
  })

  it('lists participants first, keeps a null total null', () => {
    const period = structuredClone(TWO_WAY.periods!._)
    period.by_participant.dso = { paid: null, received: null, net: null,
                                  by_stream: { dr_availability: null } }
    const rows = participantRows(TWO_WAY, period)
    expect(rows.map(r => r.id)).toEqual(['developer', 'dso', 'retailer'])
    expect(rows[0].label).toBe('Developer (developer)')
    expect(rows[1].net).toBeNull()
    expect(rows[1].streams).toEqual([{ stream: 'dr_availability', net: null }])
    expect(rows[2].internal).toBe(false)
  })

  it('names the conservation state', () => {
    expect(conservationState({ ok: true, checks: [] })).toEqual({ status: 'ok' })
    expect(conservationState({ ok: false, checks: [
      { name: 'coverage', ok: false, detail: [] }, { name: 'reconciliation', ok: null, detail: '' }],
    })).toEqual({ status: 'failed', checks: ['coverage'] })
    expect(conservationState({ ok: null, checks: [{ name: 'reconciliation', ok: null, detail: '' }] }))
      .toEqual({ status: 'not_established', checks: ['reconciliation'] })
    expect(conservationState(undefined).status).toBe('not_established')
  })

  it('writes a total row and a row per stream to CSV', () => {
    const rows = csvRows('_', participantRows(TWO_WAY, TWO_WAY.periods!._))
    expect(rows[0]).toEqual(['_', 'developer', 'participant', 'total', 1100, 300, -800])
    expect(rows).toContainEqual(['_', 'dso', 'participant', 'dr_availability', null, null, -300])
  })

  it('labels annuity capex and sorts periods', () => {
    expect(streamLabel('capex')).toBe('capex (annuity)')
    expect(streamLabel('energy_import')).toBe('energy import')
    expect(periodKeys({ status: 'ok', periods: { 2040: TWO_WAY.periods!._, 2030: TWO_WAY.periods!._ } }))
      .toEqual(['2030', '2040'])
  })
})
