// W3c (UX-flow #24): a Load, Generator or Link shows "Next: give it a time
// series ›", which opens Time Series on that component.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import PropertiesPanel from './PropertiesPanel'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: vi.fn(), getCarriers: vi.fn(), getGenerators: vi.fn(),
      getLoads: vi.fn(), getStorageUnits: vi.fn(), getStores: vi.fn(), getLinks: vi.fn(),
    },
  }
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><PropertiesPanel /></QueryClientProvider>)
}

beforeEach(() => {
  for (const fn of ['getBuses', 'getCarriers', 'getGenerators', 'getLoads', 'getStorageUnits', 'getStores', 'getLinks'] as const) {
    vi.mocked(networkApi[fn]).mockReset().mockResolvedValue([])
  }
})
afterEach(() => useUIStore.setState({ currentProject: null, selectedComponent: null }))

describe('Next: give it a time series', () => {
  it('opens Time Series on the selected load', () => {
    useUIStore.setState({ currentProject: 'dc', uiMode: 'expert', selectedComponent: { type: 'Load', name: 'plant' } })
    renderPanel()
    fireEvent.click(screen.getByTestId('props-next-time-series'))
    expect(useUIStore.getState().timeSeriesFocus).toMatchObject({ type: 'Load', name: 'plant' })
  })

  it('is absent for a bus', () => {
    useUIStore.setState({ currentProject: 'dc', selectedComponent: { type: 'Bus', name: 'b' } })
    renderPanel()
    expect(screen.queryByTestId('props-next-time-series')).toBeNull()
  })
})
