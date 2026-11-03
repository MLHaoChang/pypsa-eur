// SC-03 (V28): selecting a scene feature opens its native details; a timeline
// step only reads (one GET per native snapshot), never a proposal or a solve.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { SceneResults, SceneResultsQuery } from '../api/siteSceneResults'
import type { SiteScene } from '../api/siteScene'

vi.mock('../api/network', () => ({ networkApi: { getSnapshots: vi.fn() } }))
vi.mock('../api/siteSceneResults', () => ({ siteSceneResultsApi: { get: vi.fn() } }))
vi.mock('../api/siteScene', async (orig) => {
  const real = await orig<typeof import('../api/siteScene')>()
  return { ...real, siteSceneApi: { ...real.siteSceneApi, propose: vi.fn(), apply: vi.fn(), adopt: vi.fn(), cancel: vi.fn() } }
})
import { networkApi } from '../api/network'
import { siteSceneResultsApi } from '../api/siteSceneResults'
import { siteSceneApi } from '../api/siteScene'
import { useUIStore } from '../store/uiStore'
import SceneResultsPanel from './SceneResultsPanel'

const LABELS = ['2030-01-01T00:00:00', '2030-01-01T00:15:00', '2030-01-01T00:30:00', '2030-01-01T00:45:00']
const q = (value: number | null, unit: string, reason: string | null = null) => ({ value, available: value != null, reason, unit })

const answer = (snapshot: string, current = true): SceneResults => ({
  project: 'p', site_id: 's', scene_revision: 2,
  run: { digest: 'd', current, reason: current ? null : 'results_not_current' },
  snapshot: { index: LABELS.indexOf(snapshot), label: snapshot, period: null, weight_generators: q(0.25, 'h'), period_years: q(1, 'a') },
  window: null,
  features: { b1: { targets: [{ class: 'Link', uid: 'u', name: 'electrolyser', allocation: null, ports: [
    { port: 'p0', bus: 'elec', carrier: 'AC', role: 'input', reversed: false, sign: 'withdrawal_positive',
      rate: current ? q(10, 'MW') : q(null, 'MW', 'results_not_current') },
  ] }] } },
  groups: { plant: {
    boundary: [{ bus: 'elec', carrier: 'AC', net_import: q(10.25, 'MW') }, { bus: 'n2', carrier: 'N2', net_import: q(1, 't/h') }],
    internal_buses: ['h2'],
    totals: [{ carrier: 'AC', net_import: q(10.25, 'MW') }, { carrier: 'N2', net_import: q(1, 't/h') }],
    total: q(null, '', 'mixed_units'), unavailable_member: null,
  } },
})

const scene = {
  state: 'enabled', site_id: 's', revision: 2, generation: 'g', site: {}, asset_footprints: {}, last_receipt: null, unbound_keys: [],
  buildings: [{ id: 'b1', footprint: [[0, 0], [1, 0], [1, 1]], height_m: 5, source: { namespace: 'manual', source_id: 'm' } }],
  groups: [{ id: 'plant', label: 'Ammonia plant', members: ['b1'] }],
} as unknown as Extract<SiteScene, { state: 'enabled' }>

const renderPanel = (current = true) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>
    <SceneResultsPanel project="p" siteId="s" scene={scene} current={current} freshSince={1} />
  </QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(networkApi.getSnapshots).mockResolvedValue({ count: LABELS.length, snapshots: LABELS, weightings: [] })
  vi.mocked(siteSceneResultsApi.get).mockImplementation(async (_p: string, _s: string, x: SceneResultsQuery) => answer(x.snapshot))
  useUIStore.setState({ resultsOverlayEnabled: true, resultsSnapshotIdx: 1, uiMode: 'expert', sitePartSelection: null })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('SceneResultsPanel', () => {
  it('is hidden while the Eye is off, and fetches nothing', async () => {
    useUIStore.setState({ resultsOverlayEnabled: false })
    renderPanel()
    expect(screen.queryByTestId('scene-results')).toBeNull()
    expect(siteSceneResultsApi.get).not.toHaveBeenCalled()
  })

  it('asks for the native label at the timeline index and shows the served weight', async () => {
    renderPanel()
    expect((await screen.findByTestId('scene-results-snapshot')).textContent).toContain('2030-01-01 00:15:00 · weight 0.25 h')
    expect(siteSceneResultsApi.get).toHaveBeenCalledWith('p', 's', { snapshot: LABELS[1], period: null })
  })

  it('selecting a building opens its items; a group opens its boundary and per-carrier totals', async () => {
    useUIStore.setState({ sitePartSelection: { kind: 'building', id: 'b1' } })
    renderPanel()
    expect(await screen.findByText('10 MW')).toBeTruthy()
    expect(screen.getByText('electrolyser · Link')).toBeTruthy()
    fireEvent.click(screen.getByTestId('scene-result-group-plant'))
    const g = screen.getByTestId('scene-result-group')
    expect(screen.getByTestId('scene-result-boundary').textContent).toContain('elec · AC: 10.25 MW')
    expect(screen.getByTestId('scene-result-totals').textContent).toContain('N2: 1 t/h')
    expect(screen.getByTestId('scene-result-total').textContent).toContain('Unavailable: there is no single total because the carriers have different units.')
    expect(g.textContent).not.toMatch(/11\.25/)               // nothing summed across units
  })

  it('a timeline step is one GET for the next label (plus prefetch), never a proposal or a write', async () => {
    renderPanel()
    await screen.findByTestId('scene-results-snapshot')
    act(() => { useUIStore.setState({ resultsSnapshotIdx: 2 }) })
    await waitFor(() => expect(screen.getByTestId('scene-results-snapshot').textContent).toContain('00:30:00'))
    const asked = vi.mocked(siteSceneResultsApi.get).mock.calls.map(c => c[2].snapshot)
    expect(new Set(asked)).toEqual(new Set(LABELS.slice(1)))   // shown + next two prefetched, each once
    expect(asked.length).toBe(new Set(asked).size)
    for (const f of [siteSceneApi.propose, siteSceneApi.apply, siteSceneApi.adopt, siteSceneApi.cancel]) expect(f).not.toHaveBeenCalled()
  })

  it('an out-of-date run reads unavailable in words, never 0.00', async () => {
    vi.mocked(siteSceneResultsApi.get).mockImplementation(async (_p: string, _s: string, x: SceneResultsQuery) => answer(x.snapshot, false))
    useUIStore.setState({ sitePartSelection: { kind: 'building', id: 'b1' } })
    renderPanel(false)
    expect((await screen.findByTestId('scene-results-run')).textContent).toContain('Unavailable: the results are out of date')
    const port = screen.getByTestId('scene-result-port')
    expect(port.textContent).toContain('Unavailable')               // the reason is said once, in the run line
    expect(port.textContent).not.toContain('out of date')
    expect(port.textContent).not.toMatch(/0\.00|\b0 MW/)
  })

  it('an index that is not a snapshot of the model says so and asks nothing', async () => {
    useUIStore.setState({ resultsSnapshotIdx: 9 })
    renderPanel()
    expect(await screen.findByText('This step is not a snapshot of the model.')).toBeTruthy()
    expect(siteSceneResultsApi.get).not.toHaveBeenCalled()
  })
})
