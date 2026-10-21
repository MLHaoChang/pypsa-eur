// Capture (IPP ticket 05): the asset results' price metrics per renewable
// plant and battery, read as served; a plant that produced nothing shows a
// dash with "no output", never 0 % or NaN.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { networkApi } from '../../../api/network'
import { assetResultsApi } from '../asset/api'
import CaptureTable from './CaptureTable'

vi.mock('../../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getGenerators: vi.fn(), getStorageUnits: vi.fn() } }
})
vi.mock('../asset/api', () => ({ assetResultsApi: { get: vi.fn() } }))

const ok = (id: string) => ({ id, label: id, unit: '', kind: 'scalar', origin: 'derived', status: 'ok' })

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(networkApi.getGenerators).mockResolvedValue([
    { name: 'Solar', carrier: 'solar' }, { name: 'Idle wind', carrier: 'onwind' },
    { name: 'Grid', carrier: 'grid' },
  ] as never)
  vi.mocked(networkApi.getStorageUnits).mockResolvedValue([{ name: 'Battery', carrier: 'battery' }] as never)
  vi.mocked(assetResultsApi.get).mockImplementation(async (p) => ({
    metrics: p.metrics.map(ok),
    scalars: p.name === 'Solar' ? { capture_price: 42.5, capture_rate: 0.85 }
      : p.name === 'Battery' ? { charge_price: 20, capture_price: 80, captured_spread: 60 }
        : { capture_price: null },
  }) as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function renderTable() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><CaptureTable /></QueryClientProvider>)
}

describe('CaptureTable', () => {
  it('shows the plants and batteries; a plant with no output is a dash, never 0 % or NaN', async () => {
    renderTable()
    const solar = await screen.findByTestId('ic-capture-plant-Solar')
    await within(solar).findByText('42.50')
    expect(within(solar).getByText('85.0 %')).toBeTruthy()
    const idle = screen.getByTestId('ic-capture-plant-Idle wind')
    const cells = within(idle).getAllByRole('cell').map(c => c.textContent)
    expect(cells).toEqual(['— no output', '— no output'])
    expect(idle.textContent).not.toMatch(/NaN|0 %/)
    expect(screen.queryByTestId('ic-capture-plant-Grid')).toBeNull()
    const bat = screen.getByTestId('ic-capture-battery-Battery')
    await within(bat).findByText('60.00')
    expect(within(bat).getAllByRole('cell').map(c => c.textContent)).toEqual(['20.00', '80.00', '60.00'])
    expect(assetResultsApi.get).toHaveBeenCalledWith(expect.objectContaining({
      componentClass: 'StorageUnit', name: 'Battery', category: 'prices' }))
  })
})
