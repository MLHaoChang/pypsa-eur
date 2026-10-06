// A Link with a set extra port (`bus2`, …) draws one edge per port on both
// canvases (visual-layers plan 1, A1). The table is the palette: every Link
// item the palette can create is turned into the row the backend would hold
// and run through the edge builder; an item with a third port must yield two
// edges, one without it exactly one.
import { describe, it, expect } from 'vitest'
import type { Link } from '../api/types'
import { PALETTE_ITEM_IDS, paletteDefaults } from '../layout/paletteData'
import {
  buildLinkEdges, componentNameFromEdgeId, derivedPortFlow, linkEdgeId,
  linkPortFromEdgeId, linkPorts,
} from './topologyEdges'

const BUS_CARRIER: Record<string, string> = { B0: 'gas', B1: 'AC', B2: 'heat' }
const OPTS = {
  linkColor: (c: string) => `link:${c}`,
  busCarrier: (b: string) => BUS_CARRIER[b],
  portColor: (c: string) => `port:${c}`,
}

/** The row the backend holds after creating this palette item (ports wired B0, B1, B2). */
function linkFromPalette(id: string): Link {
  const d = paletteDefaults(id)
  const ports = Object.keys(d.ports ?? {})
  const row: Record<string, unknown> = {
    name: id, bus0: 'B0', bus1: 'B1', carrier: d.carrier ?? '', efficiency: 0.9, p_nom: 10,
    p_nom_extendable: false, p_nom_min: 0, p_nom_max: null, p_min_pu: 0, p_max_pu: 1,
    marginal_cost: 0, capital_cost: 0, fom_cost: 0, overnight_cost: null, discount_rate: null,
    build_year: 0, lifetime: null,
  }
  if (ports.includes('bus2')) { row.bus2 = 'B2'; row.efficiency2 = 0.4 }
  return row as unknown as Link
}

const LINK_ITEMS = PALETTE_ITEM_IDS.filter(id => paletteDefaults(id).cls === 'Link')

describe('buildLinkEdges over the palette', () => {
  it('the palette has Link items, and at least one with a third port', () => {
    expect(LINK_ITEMS.length).toBeGreaterThan(0)
    expect(LINK_ITEMS.some(id => 'bus2' in (paletteDefaults(id).ports ?? {}))).toBe(true)
  })

  it.each(LINK_ITEMS)('%s draws one edge per port', (id) => {
    const hasThirdPort = 'bus2' in (paletteDefaults(id).ports ?? {})
    const edges = buildLinkEdges([linkFromPalette(id)], OPTS)
    expect(edges).toHaveLength(hasThirdPort ? 2 : 1)
    expect(new Set(edges.map(e => e.id)).size).toBe(edges.length)
    expect(edges[0]).toMatchObject({ id: `link-${id}`, source: 'B0', target: 'B1', type: 'network' })
    if (hasThirdPort) {
      expect(edges[1]).toMatchObject({
        id: `link-${id}#2`, source: 'B0', target: 'B2', type: 'network',
        data: { type: 'link', port: 2, efficiency: 0.4, carrier: 'heat', color: 'port:heat' },
      })
    }
  })
})

describe('buildLinkEdges details', () => {
  const chp = linkFromPalette('chp')

  it('keeps the main edge exactly as before: coloured by the Link carrier, no port', () => {
    const [main] = buildLinkEdges([chp], OPTS)
    expect(main.data).toEqual({
      s_nom: 10, type: 'link', carrier: 'gas', color: 'link:gas', waypoints: [], history: [[]],
    })
  })

  it('colours an extra port by the far bus carrier, defaulting to the link colour helper', () => {
    const [, port] = buildLinkEdges([chp], { linkColor: OPTS.linkColor, busCarrier: OPTS.busCarrier })
    expect(port.data.color).toBe('link:heat')
  })

  it("skips an extra port whose far bus is not in the network, and an empty-string port", () => {
    const orphan = { ...chp, bus2: 'nowhere' } as Link
    const unset = { ...chp, bus2: '' } as Link
    expect(buildLinkEdges([orphan, unset], OPTS).map(e => e.id)).toEqual(['link-chp'])
  })

  it('draws bus3 too, in port order', () => {
    const three = { ...chp, bus3: 'B1', efficiency3: 0.1 } as unknown as Link
    expect(buildLinkEdges([three], OPTS).map(e => e.id)).toEqual(['link-chp', 'link-chp#2', 'link-chp#3'])
    expect(linkPorts(three as unknown as Record<string, unknown>)).toEqual([
      { port: 2, bus: 'B2', efficiency: 0.4 }, { port: 3, bus: 'B1', efficiency: 0.1 },
    ])
  })
})

describe('edge id helpers', () => {
  it('recover the component name from every edge kind, dropping only a link port suffix', () => {
    expect(componentNameFromEdgeId(linkEdgeId('K1'))).toBe('K1')
    expect(componentNameFromEdgeId(linkEdgeId('K1', 2))).toBe('K1')
    expect(componentNameFromEdgeId('line-L#1')).toBe('L#1')
    expect(componentNameFromEdgeId('tr-T1')).toBe('T1')
  })

  it('read the port back from an extra-port id', () => {
    expect(linkPortFromEdgeId('link-K1#3')).toBe(3)
    expect(linkPortFromEdgeId('link-K1')).toBeUndefined()
    expect(linkPortFromEdgeId('line-L1')).toBeUndefined()
  })
})

describe('derivedPortFlow', () => {
  it('is PyPSA\'s p_i = -p0 × efficiency_i, with efficiency defaulting to 1', () => {
    expect(derivedPortFlow(100, 0.4)).toBe(-40)
    expect(derivedPortFlow(-50, 0.4)).toBe(20)
    expect(derivedPortFlow(100, undefined)).toBe(-100)
  })
})
