// S1d-3: the panels' own rows (a branch's ends and carrier, an asset's
// carrier and bus) take their tooltip from the asset schema's descriptor,
// as the schema-rendered rows do, not from a second copy in propertyDocs.
// The tooltips below quote text only the descriptors carry.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  const empty = vi.fn(async () => [])
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: empty, getCarriers: empty, getLines: empty, getLinks: empty,
      getLinkProfiles: vi.fn(async () => ({})),
      getCatalog: vi.fn(async (component: string) => ({ component, attributes: [] })),
      listTimeseries: empty,
    },
  }
})
vi.mock('../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulation')>()
  return {
    ...actual,
    simulationApi: { ...actual.simulationApi, getSolverConfig: vi.fn(async () => ({ mode: 'lopf' })) },
  }
})

import { LinePanel, LinkCard } from './PropertiesPanel'

const LINE = { name: 'L1', bus0: 'B1', bus1: 'B2', carrier: 'AC', s_nom: 500, s_nom_extendable: false,
  length: 100, r: 3, x: 30, b: 0, lifetime: 40 } as never
const LINK = { name: 'E1', bus0: 'AC1', bus1: 'H2', carrier: 'electrolysis', p_nom: 10, efficiency: 0.7,
  p_nom_extendable: false } as never

function renderWith(node: React.ReactNode, seed: Array<[string, unknown]>) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  })
  for (const [key, value] of seed) qc.setQueryData(nk('Demo', key), value)
  render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

/** The tooltip of the row labelled `label` (its help trigger focused). */
async function tipOf(label: string): Promise<string> {
  const text = await screen.findByText(label, { selector: 'span' })
  const trigger = text.parentElement?.querySelector('[tabindex="0"]')
  if (!trigger) throw new Error(`row ${label} has no tooltip`)
  fireEvent.focus(trigger)
  const tip = (await screen.findByRole('tooltip')).textContent ?? ''
  fireEvent.blur(trigger)
  return tip
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => { useUIStore.setState({ currentProject: null }) })

describe('panel rows read their tooltip from the asset schema', () => {
  it('a line’s topology rows', async () => {
    renderWith(<LinePanel name="L1" />, [['lines', [LINE]]])
    expect(await tipOf('From bus')).toContain('A positive flow in the results runs from this bus to the other.')
    expect(await tipOf('Carrier')).toContain('join buses of different carriers with a Link')
  })

  it('a link’s carrier and ends in its read-out', async () => {
    renderWith(<LinkCard link={LINK} mode="detail" onRename={() => {}} />, [['links', [LINK]]])
    expect(await tipOf('Carrier')).toContain('Technology carrier (electrolysis, fuel cell, DC, heat pump, …)')
    expect(await tipOf('From bus')).toContain('Its capacity and cost are per MW of this input.')
  })
})
