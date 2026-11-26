// Assistant-assurance 06, A5.2 (S2): the Emissions and Grid risk tabs render the
// bound view and carry its binding, so a v2 navigation ACK proves the figures shown.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { client } from '../../api/client'
import { networkApi } from '../../api/network'
import { useUIStore } from '../../store/uiStore'
import { displayedResultsSource } from '../../utils/resultsSource'
import { acknowledgeMountedView, type NavigationAcknowledgement } from '../../utils/verifiedNavigation'
import Emissions from './Emissions'
import GridRiskTab from './GridRiskTab'

vi.mock('../../api/client', () => ({ client: { get: vi.fn(), post: vi.fn() } }))
vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getCarriers: vi.fn() } }
})

const binding = (view: string, data = 'b') => ({
  version: 1 as const, project_id: 'p-1', project_name: 'Demo', scope: 'current' as const,
  view, input_sha256: 'a'.repeat(64), data_sha256: data.repeat(64),
})
const EMISSIONS = {
  total_tCO2: 15, by_carrier: [{ carrier: 'gas', tCO2: 15, share_pct: 100 }], by_generator: [],
  caps: [], by_period: [], is_multi_period: false,
}
const GRID_RISK = { available: false, project_id: 'p-1', omitted: 'no_process_unit' }

function serve(routes: Record<string, unknown>) {
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (!(url in routes)) throw new Error(`unexpected GET ${url}`)
    const body = routes[url]
    return body === 204 ? { status: 204, data: '' } : { status: 200, data: body }
  })
}

function mountTab(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <div data-assurance-panel="results" data-assurance-project="Demo">
        <div data-assurance-tab={ui.type === Emissions ? 'emissions' : 'grid_risk'}>{ui}</div>
      </div>
    </QueryClientProvider>,
  )
}

const ack = (tab: string, sb: ReturnType<typeof binding>): NavigationAcknowledgement => ({
  version: 2, action_id: `a-${tab}`, turn_id: 't-1', binding_generation: 4,
  project_id: 'p-1', project_name: 'Demo', input_sha256: 'a'.repeat(64),
  target: { panel_id: 'results', results_tab: tab }, source_binding: sb,
})
const tick = () => new Promise(resolve => setTimeout(resolve, 0))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(networkApi.getCarriers).mockResolvedValue([])
  vi.mocked(client.post).mockResolvedValue({ data: { outcome: 'completed' } })
  useUIStore.setState({ currentProject: 'Demo' })
})
afterEach(() => { cleanup(); useUIStore.setState({ currentProject: null }) })

describe('displayed-data binding on result tabs', () => {
  it('Emissions renders the bound payload and marks its root with the exact binding', async () => {
    serve({ '/results/displayed/emissions': { status: 'ok', data: EMISSIONS, binding: binding('emissions') } })
    mountTab(<Emissions />)
    await screen.findByText(/CO₂ emissions/)
    expect(vi.mocked(client.get).mock.calls.map(c => c[0])).toContain('/results/displayed/emissions')
    expect(displayedResultsSource('emissions')).toEqual(binding('emissions'))
    expect(document.body.textContent).toContain('15')
  })

  it('an unsolved Emissions tab (204) carries no binding, so no v2 ACK is sent', async () => {
    serve({ '/results/displayed/emissions': 204 })
    mountTab(<Emissions />)
    await waitFor(() => expect(client.get).toHaveBeenCalled())
    await tick()
    expect(displayedResultsSource('emissions')).toBeNull()
    const stop = acknowledgeMountedView(ack('emissions', binding('emissions')), () => true)
    await tick()
    expect(client.post).not.toHaveBeenCalled()
    stop()
  })

  it('the ACK echoes the rendered binding, and a different binding never ACKs', async () => {
    serve({ '/results/displayed/emissions': { status: 'ok', data: EMISSIONS, binding: binding('emissions') } })
    mountTab(<Emissions />)
    await screen.findByText(/CO₂ emissions/)
    const stale = acknowledgeMountedView(ack('emissions', binding('emissions', 'c')), () => true)
    await tick()
    expect(client.post).not.toHaveBeenCalled()
    stale()
    const verified = vi.fn()
    acknowledgeMountedView(ack('emissions', binding('emissions')), () => true, verified)
    await waitFor(() => expect(verified).toHaveBeenCalled())
    expect(client.post).toHaveBeenCalledWith('/chat/ui-ack', expect.objectContaining({
      version: 2, source_binding: binding('emissions') }), { skipErrorToast: true })
  })

  it('Grid risk marks what it shows, including a not-applicable answer', async () => {
    serve({ '/results/displayed/grid_risk': { status: 'ok', data: GRID_RISK, binding: binding('grid_risk') } })
    mountTab(<GridRiskTab />)
    await screen.findByText('Not applicable')
    expect(displayedResultsSource('grid_risk')).toEqual(binding('grid_risk'))
    expect(displayedResultsSource('emissions')).toBeNull()
  })
})
