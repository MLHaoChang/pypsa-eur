import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AssetDetail from './AssetDetail'
import { assetResultsApi } from './api'
import { useUIStore } from '../../../store/uiStore'
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
  // uiStore is a real singleton shared across tests in this file — reset the
  // deep-link slot so a request set by one test never leaks into the next.
  useUIStore.setState({ assetDetailRequest: null })
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

  it('disables the xlsx export links while a refetch is showing the previous payload, and restores them once it settles', async () => {
    // Route exportXlsxUrl through its params so a settled href reveals WHICH
    // selection it was built from, instead of always the same fixed string.
    vi.mocked(assetResultsApi.exportXlsxUrl).mockImplementation(
      (p, scope) => `http://x/${scope}/${p.metrics.join(',') || 'none'}.xlsx`)

    // Make `get` controllable so the in-flight window between two successive
    // fetches (first: selected=[]; second: selected=reconciled defaults, which
    // the reconcile effect triggers the instant the first one resolves) is
    // observable instead of settling instantly.
    const deferred: Array<(v: AssetResultsResponse) => void> = []
    vi.mocked(assetResultsApi.get).mockImplementation(
      () => new Promise(resolve => { deferred.push(resolve) }))

    renderIt()

    // First fetch: selected=[] (nothing ticked yet).
    await waitFor(() => expect(deferred).toHaveLength(1))
    deferred[0](RESPONSE)

    // Capture the link references now, while `data` is real (matches the
    // still-selected=[] params) — same DOM nodes persist across re-renders
    // (no key on these <a>s), so reading attributes off them later reflects
    // whatever the component currently renders without needing a fresh
    // role-based query (which would fail once `href` is stripped, since an
    // `<a>` with no `href` has no accessible "link" role).
    const viewLink = await screen.findByRole('link', { name: /Export configured view/ })
    const fullLink = await screen.findByRole('link', { name: /Full asset report/ })

    // Resolving the first fetch lets the reconcile effect compute the default
    // tick-set and write it to `selected` — changing the query key and
    // starting a SECOND fetch. `keepPreviousData` keeps the FIRST payload on
    // screen meanwhile (table/CSV still agree with it), but `params` already
    // reflects the NEW selection — exactly the disagreement window the fix
    // closes for the xlsx links.
    await waitFor(() => expect(deferred).toHaveLength(2))
    await waitFor(() => expect(viewLink.getAttribute('href')).toBeNull())
    expect(viewLink.getAttribute('aria-disabled')).toBe('true')
    expect(fullLink.getAttribute('href')).toBeNull()
    expect(fullLink.getAttribute('aria-disabled')).toBe('true')

    // Resolve the second fetch — isPlaceholderData clears, and the links must
    // now point at the CURRENT (reconciled) selection, not the original empty one.
    deferred[1](RESPONSE)
    await waitFor(() => expect(viewLink.getAttribute('href'))
      .toBe('http://x/view/p,energy_mwh.xlsx'))
    expect(fullLink.getAttribute('href')).toBe('http://x/full/p,energy_mwh.xlsx')
  })

  it('consumes a pending assetDetailRequest deep-link: selects the asset, category and mode, then clears the request', async () => {
    useUIStore.setState({
      assetDetailRequest: {
        componentClass: 'Generator', name: 'Wind 1',
        category: 'capacity', mode: 'duration', metrics: ['p'],
      },
    })
    renderIt()

    // Resolves {componentClass, name} against the fetched assets list (the
    // request itself carries no carrier/bus) and lands on the right asset.
    expect(await screen.findByText(/Wind 1/)).toBeTruthy()

    const capacityTab = await screen.findByRole('tab', { name: /Capacity/ })
    expect(capacityTab.getAttribute('aria-selected')).toBe('true')

    // Mode carried through to the query — same assertion style as the
    // existing "switches view mode" test above.
    await waitFor(() => expect(vi.mocked(assetResultsApi.get)).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'duration', componentClass: 'Generator', name: 'Wind 1' })))

    // Consumed exactly once — a stale request left in the store would keep
    // re-firing the effect on every render.
    await waitFor(() => expect(useUIStore.getState().assetDetailRequest).toBeNull())
  })

  it('ignores a deep-link request for an asset that is not in the network', async () => {
    useUIStore.setState({
      assetDetailRequest: { componentClass: 'Generator', name: 'No Such Asset' },
    })
    renderIt()

    // Falls back to the auto-selected first asset instead of crashing or
    // hanging on an unresolved request.
    expect(await screen.findByText(/Gas 1/)).toBeTruthy()
    await waitFor(() => expect(useUIStore.getState().assetDetailRequest).toBeNull())
  })
})
