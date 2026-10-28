// The whole-tab upload leaves an in-place receipt (not a toast only).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import TimeSeriesManager from './TimeSeriesManager'

const net = vi.hoisted(() => ({ upload: vi.fn(), template: vi.fn() }))
vi.mock('../api/network', () => ({
  networkApi: new Proxy({
    getLoads: async () => [],
    getGenerators: async () => [
      { name: 'ccgt', bus: 'b1', carrier: 'gas', p_nom: 20 },
      { name: 'coal', bus: 'b1', carrier: 'coal', p_nom: 20 }],
    getLinks: async () => [],
    getLoadProfiles: async () => ({}),
    getGeneratorProfiles: async () => ({
      ccgt: { category: 'conventional' }, coal: { category: 'conventional' } }),
    getLinkProfiles: async () => ({}),
    getSnapshots: async () => ({ count: 0 }),
    getTimeseries: async () => ({ index: [], columns: [], data: [] }),
    uploadGeneratorProfile: net.upload,
    downloadGeneratorTemplate: net.template,
  } as Record<string, unknown>, { get: (t, k: string) => t[k] ?? (async () => ({})) }),
}))
vi.mock('../components/series/DefaultsReview', () => ({ default: () => <div /> }))
vi.mock('../pages/economics/SiteConnectionLink', () => ({ openSiteConnection: vi.fn() }))

async function upload() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><TimeSeriesManager /></QueryClientProvider>)
  await userEvent.click(await screen.findByRole('button', { name: /Conventional/ }))
  await screen.findByText('ccgt')
  const input = screen.getByTestId('tab-upload-input') as HTMLInputElement
  await userEvent.upload(input, new File(['x'], 'plant.csv', { type: 'text/csv' }))
}

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ currentProject: 'Site A', timeSeriesFocus: null, uiMode: 'expert' })
})
afterEach(() => cleanup())

describe('bulk upload receipt', () => {
  it('shows two matched columns with old → new and the unknown one with its reason', async () => {
    net.upload.mockResolvedValue({
      matched: ['ccgt', 'coal'], unmatched: ['ghost'], rows: 24, snapshot_count: 24,
      matched_detail: [
        { name: 'ccgt', old: { peak: 1, mean: 0.9 }, new: { peak: 0.8, mean: 0.5 } },
        { name: 'coal', old: null, new: { peak: 1, mean: 0.7 } }],
      unmatched_detail: [{ name: 'ghost', reason: 'no_such_component' }],
    })
    await upload()
    const r = await screen.findByTestId('upload-receipt')
    expect(within(r).getByTestId('receipt-matched-ccgt').textContent).toMatch(/peak 1 → 0\.8, mean 0\.9 → 0\.5/)
    expect(within(r).getByTestId('receipt-matched-coal').textContent).toMatch(/peak 1, mean 0\.7/)
    expect(within(r).getByTestId('receipt-unmatched-ghost').textContent).toMatch(/no component of this tab has this name/)
    await userEvent.click(within(r).getAllByRole('button', { name: /Open its editor/ })[0])
    expect(await screen.findByRole('heading', { name: /of ccgt/ })).toBeTruthy()
  })

  it('shows a refused file with its reason and the template', async () => {
    net.upload.mockRejectedValue(new Error('No column names matched any generator.'))
    net.template.mockResolvedValue(new Blob(['x']))
    await upload()
    const r = await screen.findByTestId('upload-receipt')
    expect(within(r).getByTestId('receipt-refusal').textContent).toMatch(/No column names matched/)
    expect(within(r).getByRole('button', { name: /Download the template/ })).toBeTruthy()
  })
})
