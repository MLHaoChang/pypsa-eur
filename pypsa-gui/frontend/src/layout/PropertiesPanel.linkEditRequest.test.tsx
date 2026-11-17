// template-e2e 02: a flow item's Edit (Guided "Import efficiency (assumed)") mounts the Link card cold and
// asks for its editor before the asset schema has arrived. The card shows a busy placeholder until then,
// never blank fields, and the editor opens on the link's stored values once the schema is there.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

const schemaState = vi.hoisted(() => ({ ready: false }))
vi.mock('../hooks/useAssetSchema', async () => {
  const fixture = (await import('../test/fixtures/assetSchema.json')).default as Record<string, unknown>
  return {
    useAssetSchema: (componentClass: string | null) => ({
      data: componentClass && schemaState.ready ? fixture[componentClass] : undefined,
      isLoading: !schemaState.ready, isError: false,
    }),
  }
})
vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  const empty = vi.fn(async () => [])
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: empty, getCarriers: empty, getLinks: empty,
      getLinkProfiles: vi.fn(async () => ({})),
      getCatalog: vi.fn(async (component: string) => ({ component, attributes: [] })),
      listTimeseries: empty,
    },
  }
})
vi.mock('../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulation')>()
  return { ...actual, simulationApi: { ...actual.simulationApi, getSolverConfig: vi.fn(async () => ({ mode: 'lopf' })) } }
})

import { LinkCard } from './PropertiesPanel'

const LINK = { name: 'grid_import', eh_role: 'grid_import', bus0: 'grid', bus1: 'dc_mv', carrier: 'AC', p_nom: 40,
  p_nom_extendable: false, p_nom_min: 0, p_nom_max: null, p_min_pu: 0, p_max_pu: 1, efficiency: 0.995,
  marginal_cost: 0, overnight_cost: null, capital_cost: 0, fom_cost: 0, lifetime: null } as never

function card() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  qc.setQueryData(nk('Demo', 'links'), [LINK])
  return <QueryClientProvider client={qc}><LinkCard link={LINK} mode="detail" onRename={() => {}} /></QueryClientProvider>
}

afterEach(() => { cleanup(); schemaState.ready = false; useUIStore.getState().clearPropertiesEditRequest() })

describe('a Link edit asked for before the schema loads', () => {
  it('is busy while the schema loads, then opens on the stored values', async () => {
    useUIStore.setState({ currentProject: 'Demo' })
    useUIStore.getState().requestPropertiesEdit({ type: 'Link', name: 'grid_import', field: 'efficiency' })
    const view = render(card())
    expect(screen.getByTestId('link-edit-loading').getAttribute('aria-busy')).toBe('true')
    expect(screen.queryByLabelText('Efficiency (%)')).toBeNull()

    schemaState.ready = true
    view.rerender(card())
    const eff = await screen.findByLabelText('Efficiency (%)') as HTMLInputElement
    expect(eff.value).toBe('99.5')
    expect(screen.getAllByDisplayValue('grid_import').some(e => e.tagName === 'INPUT')).toBe(true)
    expect(screen.getByDisplayValue('40')).toBeTruthy()
    expect(screen.queryByTestId('link-edit-loading')).toBeNull()
    // One value, two views: the import link's Capacity names the Connection limit line.
    expect(screen.getByTestId('link-capacity-connection-limit').textContent).toMatch(/Connection limit/)
  })

  it('shows the read-out, not the busy state, when no edit was asked for', () => {
    useUIStore.setState({ currentProject: 'Demo' })
    render(card())
    expect(screen.queryByTestId('link-edit-loading')).toBeNull()
  })
})
