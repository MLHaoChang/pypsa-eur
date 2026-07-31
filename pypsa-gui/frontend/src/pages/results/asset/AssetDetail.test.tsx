import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AssetDetail from './AssetDetail'
import { assetResultsApi } from './api'
import type { AssetResultsResponse } from './types'

vi.mock('./api')

const CATEGORIES = [
  { id: 'summary', label: 'Summary', status: 'ok' as const },
  { id: 'capacity', label: 'Capacity', status: 'ok' as const },
  { id: 'dispatch', label: 'Dispatch', status: 'ok' as const },
  { id: 'storage', label: 'Storage', status: 'na' as const,
    reason: 'Generator does not store energy' },
  { id: 'loadflow', label: 'Load flow', status: 'na' as const,
    reason: 'Generator is not a branch or bus component' },
  { id: 'prices', label: 'Prices & duals', status: 'ok' as const },
  { id: 'economics', label: 'Economics', status: 'ok' as const },
  { id: 'emissions', label: 'Emissions', status: 'blocked' as const,
    reason: "carrier 'gas' declares no co2_emissions",
    remedy: { action: 'open_properties' as const, label: 'Set co2_emissions' } },
]

const RESPONSE: AssetResultsResponse = {
  asset: { class: 'Generator', name: 'Gas 1', carrier: 'gas', bus: 'B1',
           params: { p_nom: 200 } },
  solve: { source: 'lopf', objective: 1e9, solve_time: 2, condition: 'optimal' },
  category: 'dispatch', mode: 'chronological', categories: CATEGORIES,
  metrics: [
    { id: 'p', label: 'Active power', unit: 'MW', kind: 'series',
      origin: 'output', status: 'ok' },
    { id: 'energy_mwh', label: 'Energy', unit: 'MWh', kind: 'scalar',
      origin: 'derived', status: 'ok', formula: 'Σ p × w' },
  ],
  scalars: { energy_mwh: 512000 },
  index: ['2026-01-01T00:00:00'], periods: null, pct_of_hours: null,
  columns: [{ id: 'p', label: 'Active power', unit: 'MW', metric_id: 'p', agg: null }],
  series: { p: [120] },
}

const renderIt = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><AssetDetail /></QueryClientProvider>)
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(assetResultsApi.listAssets).mockResolvedValue([
    { class: 'Generator', name: 'Gas 1', carrier: 'gas', bus: 'B1' },
    { class: 'Generator', name: 'Wind 1', carrier: 'onwind', bus: 'B1' },
  ])
  vi.mocked(assetResultsApi.get).mockResolvedValue(RESPONSE)
  vi.mocked(assetResultsApi.exportXlsxUrl).mockReturnValue('http://x/export.xlsx')
})

describe('AssetDetail', () => {
  it('auto-selects the first asset and shows its identity', async () => {
    renderIt()
    expect(await screen.findByText(/Gas 1/)).toBeTruthy()
    await waitFor(() => expect(screen.getByText(/carrier/i)).toBeTruthy())
  })

  it('greys out categories the class cannot use and explains why', async () => {
    renderIt()
    const loadflow = await screen.findByRole('tab', { name: /Load flow/ })
    expect(loadflow).toHaveProperty('disabled', true)
    expect(loadflow.getAttribute('title')).toMatch(/not a branch/)
  })

  it('renders a blocked category as disabled but distinct from n/a', async () => {
    renderIt()
    const emissions = await screen.findByRole('tab', { name: /Emissions/ })
    expect(emissions).toHaveProperty('disabled', true)
    expect(emissions.getAttribute('title')).toMatch(/co2_emissions/)
  })

  it('shows selected scalars as KPI cards', async () => {
    renderIt()
    expect(await screen.findByText(/Energy/)).toBeTruthy()
    expect(await screen.findByText(/512000|512,000/)).toBeTruthy()
  })

  it('switches view mode and refetches with the new mode', async () => {
    renderIt()
    await screen.findByRole('tab', { name: /Dispatch/ })
    await userEvent.click(screen.getByRole('button', { name: /Duration/ }))
    await waitFor(() => expect(vi.mocked(assetResultsApi.get)).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'duration' })))
  })

  it('remembers the tick-set per class across asset switches', async () => {
    renderIt()
    await screen.findByRole('checkbox', { name: /Active power/ })
    await userEvent.click(screen.getByRole('checkbox', { name: /Active power/ }))
    await waitFor(() => expect(
      JSON.parse(localStorage.getItem('assetDetail:metrics:Generator:dispatch')!),
    ).not.toContain('p'))
  })

  it('offers both export scopes', async () => {
    renderIt()
    expect(await screen.findByRole('link', { name: /Export configured view/ })).toBeTruthy()
    expect(await screen.findByRole('link', { name: /Full asset report/ })).toBeTruthy()
  })
})
