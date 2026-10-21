// M5 (visual-layers plan 2): the Issues panel shows the map's topology
// check as its own group beside "Site layout", computed from the shared
// list queries and the two sidecar stores, each row deep-linking to its
// component. Advisory — the preflight result is untouched.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { simulationApi } from '../api/simulation'
import { networkApi } from '../api/network'
import { mapLayoutApi } from '../api/mapLayout'
import { sitesApi } from '../api/sites'
import { useUIStore } from '../store/uiStore'
import { useMapLayoutStore } from './mapLayoutStore'
import { useSitesStore } from '../site3d/sitesStore'
import IssuesPanel from './IssuesPanel'

vi.mock('../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulation')>()
  return { ...actual, simulationApi: { ...actual.simulationApi, preflight: vi.fn() } }
})
vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getCarriers: vi.fn(), getBuses: vi.fn(), getLines: vi.fn(), getLinks: vi.fn(), getTransformers: vi.fn() } }
})
vi.mock('../api/mapLayout', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/mapLayout')>()
  return { ...actual, mapLayoutApi: { ...actual.mapLayoutApi, getMapLayout: vi.fn(), putMapLayout: vi.fn() } }
})
vi.mock('../api/sites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/sites')>()
  return { ...actual, sitesApi: { ...actual.sitesApi, getSites: vi.fn(), putSites: vi.fn() } }
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><IssuesPanel /></QueryClientProvider>)
}

const BUSES = [
  { name: 'A', x: 6.0, y: 53.0, v_nom: 110 },
  { name: 'A2', x: 6.0, y: 53.00002, v_nom: 110 },   // ~2 m from A
  { name: 'U', x: 0, y: 0, v_nom: 33 },
]

beforeEach(() => {
  vi.mocked(simulationApi.preflight).mockReset().mockResolvedValue({ ok: true, errors: 0, warnings: 0, issues: [] })
  vi.mocked(networkApi.getCarriers).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue(BUSES as never)
  vi.mocked(networkApi.getLines).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getLinks).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getTransformers).mockReset().mockResolvedValue([])
  vi.mocked(mapLayoutApi.getMapLayout).mockReset().mockResolvedValue({ version: 1, routes: {}, bubbles: {} })
  vi.mocked(sitesApi.getSites).mockReset().mockResolvedValue({ version: 1, sites: [] })
  useMapLayoutStore.getState().resetForTests()
  useSitesStore.getState().resetForTests()
  useUIStore.setState({ currentProject: 'Demo', selectedComponent: null, highlightedComponent: null, activeSlidePanel: 'issues' })
})
afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null, activeSlidePanel: null })
})

describe('IssuesPanel — Topology check group (M5)', () => {
  it('lists the map rules under "Topology check" and deep-links each row, leaving the preflight clean', async () => {
    renderPanel()
    await screen.findByText('All checks passed')
    await screen.findByText('Topology check')
    const group = screen.getByTestId('topology-check-group')
    await waitFor(() => expect(group.textContent).toContain('Two buses at the same spot'))
    expect(group.textContent).toContain('Bus has no location')
    expect(group.textContent).toContain('U has no location')
    expect(screen.getByText('All checks passed')).toBeTruthy()   // advisory: the counts stay at zero
    const views = screen.getAllByRole('button', { name: /View/ })
    expect(views).toHaveLength(2)
    await userEvent.click(views[0])          // the unplaced row comes first
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Bus', name: 'U' })
    expect(useUIStore.getState().activeSlidePanel).toBe('issues')
  })

  it('reads the routes from the map document and the sites from the sites document', async () => {
    vi.mocked(networkApi.getBuses).mockResolvedValue([{ name: 'A', x: 6.0, y: 53.0 }, { name: 'B', x: 7.0, y: 53.0 }] as never)
    vi.mocked(networkApi.getLines).mockResolvedValue([{ name: 'L1', bus0: 'A', bus1: 'B', length: 66.5 }] as never)
    vi.mocked(mapLayoutApi.getMapLayout).mockResolvedValue({ version: 1, routes: { 'line:L1': { points: [[6.5, 53.5]], source: 'user' } }, bubbles: {} })
    renderPanel()
    await screen.findByText('Topology check')
    await waitFor(() => expect(screen.getByTestId('topology-check-group').textContent).toContain('route on the map measures'))
    expect(mapLayoutApi.getMapLayout).toHaveBeenCalledWith('Demo')
    expect(sitesApi.getSites).toHaveBeenCalledWith('Demo')
  })

  it('shows no group when the model and the map agree', async () => {
    vi.mocked(networkApi.getBuses).mockResolvedValue([{ name: 'A', x: 6.0, y: 53.0 }, { name: 'B', x: 7.0, y: 53.0 }] as never)
    renderPanel()
    await screen.findByText('All checks passed')
    await waitFor(() => expect(networkApi.getTransformers).toHaveBeenCalled())
    expect(screen.queryByText('Topology check')).toBeNull()
  })
})
