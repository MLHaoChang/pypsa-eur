// Obstacle 3 (guided-mode spec §2.7): "How to tag the network" is launched
// from Results, where the Properties panel is not rendered, and its targets
// only exist in the Bus card's Edit form. The button's `prepare` closes the
// full-screen panel, selects a bus (the first PoC bus, else the first bus),
// opens the right panel and asks the Bus card for Edit.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import { guidesApi } from '../../components/GuidedTour'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { EhReferenceDesignPanel } from './EhReferenceDesignPanel'
import { prepareTaggingTour } from './prepareTaggingTour'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  const none = () => vi.fn().mockResolvedValue(null)
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: none(), getEhReferenceDesign: none(), getEhRedundancy: none(),
      getEhLevers: none(), getEhDtc: none(), getEhDtcPlanning: none(),
      getEhReadiness: none(), getEhTemplate: none(),
    },
  }
})
vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getBuses: vi.fn() } }
})

const BUSES = [
  { name: 'it_bus', eh_poc: false }, { name: 'grid', eh_poc: true }, { name: 'hub' },
]

function renderPanel(client: QueryClient) {
  return render(
    <QueryClientProvider client={client}><EhReferenceDesignPanel /></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
  vi.spyOn(guidesApi, 'getGuide').mockResolvedValue({ version: 1, tours: {}, fields: {} })
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue(BUSES as never)
  useUIStore.setState({
    currentProject: 'Demo', activeSlidePanel: 'results', selectedComponent: null,
    rightPanelOpen: false, propertiesEditRequest: null,
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  useUIStore.setState({ activeSlidePanel: null, selectedComponent: null,
    propertiesEditRequest: null })
})

describe('tagging tour prepare (obstacle 3)', () => {
  it('the panel button closes Results, selects the PoC bus, opens Properties in Edit', async () => {
    const user = userEvent.setup()
    renderPanel(new QueryClient({ defaultOptions: { queries: { retry: false } } }))
    await user.click(screen.getByTestId('eh-reference-design-toggle'))
    await user.click(screen.getByTestId('eh-tagging-guide-button'))
    await waitFor(() => expect(useUIStore.getState().propertiesEditRequest).toBe('Bus'))
    const s = useUIStore.getState()
    expect(s.activeSlidePanel).toBeNull()
    expect(s.selectedComponent).toEqual({ type: 'Bus', name: 'grid' })
    expect(s.rightPanelOpen).toBe(true)
  })

  it('reads the buses query cache first and falls back to the first bus', async () => {
    const qc = new QueryClient()
    qc.setQueryData(nk('Demo', 'buses'), [{ name: 'a' }, { name: 'b' }])
    await prepareTaggingTour(qc, 'Demo', { waitMs: 0 })
    expect(networkApi.getBuses).not.toHaveBeenCalled()
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Bus', name: 'a' })
  })

  it('fetches the buses when the cache is empty', async () => {
    await prepareTaggingTour(new QueryClient(), 'Demo', { waitMs: 0 })
    expect(networkApi.getBuses).toHaveBeenCalledTimes(1)
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Bus', name: 'grid' })
  })

  it('rejects on a network without buses (the tour then opens with its note)', async () => {
    vi.mocked(networkApi.getBuses).mockResolvedValue([])
    await expect(prepareTaggingTour(new QueryClient(), 'Demo', { waitMs: 0 }))
      .rejects.toThrow(/no bus/i)
  })
})
