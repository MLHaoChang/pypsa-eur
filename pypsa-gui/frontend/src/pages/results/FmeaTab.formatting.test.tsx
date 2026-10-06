// Bug 1 (guided-mode spec §2.4): the FMEA € columns printed raw integers
// (`toFixed(0)` → "590000"). They now use `fmtCurrency(v, 1)`; the sort keys
// still read the raw numbers.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import FmeaTab from './FmeaTab'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getFmeaModes: vi.fn(), getWorksheet: vi.fn(), getStressScenarios: vi.fn(),
      getStressProfilePacks: vi.fn(),
    },
  }
})

const ROW = (name: string, severity: number, criticality: number) => ({
  mode_id: `generator:${name}:forced_outage`, component_class: 'Generator',
  name, failure_class: 'B', occurrence_per_year: 1, occurrence_basis: 'EFORd',
  severity_eur: severity, criticality_eur_per_year: criticality,
  in_metric_scope: true, engine: 'lp_proxy', fidelity: 'deterministic_scenario',
})

afterEach(() => cleanup())
beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(resultsApi.getFmeaModes).mockReset().mockResolvedValue({
    per_mode: [ROW('genset_1', 12_345.6, 590_000), ROW('pv', 0.4, 42)],
    sweep_status: null, sweep_error: null,
  })
  vi.mocked(resultsApi.getWorksheet).mockReset()
    .mockResolvedValue({ version: 1, manual_rows: [], overlays: {} })
  vi.mocked(resultsApi.getStressScenarios).mockReset().mockResolvedValue({ scenarios: [] })
})

function cells(name: string): string[] {
  const row = screen.getByText(name).closest('tr')!
  return Array.from(row.querySelectorAll('td')).map(td => td.textContent ?? '')
}

it('renders severity and criticality with fmtCurrency(v, 1)', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><FmeaTab /></QueryClientProvider>)
  await screen.findByText('genset_1')
  const big = cells('genset_1')
  expect(big[3]).toBe('€12.3 k')
  expect(big[4]).toBe('€590.0 k')
  const small = cells('pv')
  expect(small[3]).toBe('€0.4')
  expect(small[4]).toBe('€42.0')
  // Sorted by raw criticality, descending: genset_1 first.
  const names = Array.from(screen.getByTestId('fmea-table').querySelectorAll('tbody tr'))
    .map(tr => tr.querySelector('td')?.textContent)
  expect(names[0]).toMatch(/^genset_1/)
})

// P29 (B3): a €0 row says why — in Guided as the cell's text, in Expert as
// the `title` of the unchanged `€0.0`. A row without the field (a sweep
// record from before P29) keeps `€0.0` with no title.
it('a €0 row shows its reason in Guided and as a title in Expert', async () => {
  vi.mocked(resultsApi.getFmeaModes).mockResolvedValue({
    per_mode: [
      { ...ROW('genset_1', 0, 0), zero_reason: 'no_shortfall' },
      { ...ROW('hp', 0, 0), zero_reason: 'out_of_scope', in_metric_scope: false },
      { ...ROW('boiler', 0, 0), zero_reason: 'no_outage_data' },
      { ...ROW('chp', 0, 0), zero_reason: 'unpriced' },
      ROW('old_row', 0, 0),
      { ...ROW('pv', 0.4, 42), zero_reason: null },
    ],
    sweep_status: null, sweep_error: null,
  })
  const severityCell = (name: string) =>
    screen.getByText(name).closest('tr')!.querySelectorAll('td')[3] as HTMLElement
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  useUIStore.setState({ uiMode: 'expert' })
  const { unmount } = render(<QueryClientProvider client={client}><FmeaTab /></QueryClientProvider>)
  await screen.findByText('genset_1')
  expect(severityCell('genset_1').textContent).toBe('€0.0')
  expect(severityCell('genset_1').getAttribute('title')).toBe('no shortfall — the site copes without it')
  expect(severityCell('hp').getAttribute('title')).toBe('not counted (outside the electricity metric)')
  expect(severityCell('boiler').getAttribute('title')).toBe('no outage frequency (outage rate or repair time missing or zero)')
  expect(severityCell('chp').getAttribute('title')).toBe('no price set for undelivered energy')
  expect(severityCell('old_row').textContent).toBe('€0.0')
  expect(severityCell('old_row').hasAttribute('title')).toBe(false)
  expect(severityCell('pv').hasAttribute('title')).toBe(false)
  unmount()

  useUIStore.setState({ uiMode: 'guided' })
  render(<QueryClientProvider client={client}><FmeaTab /></QueryClientProvider>)
  await screen.findByText('genset_1')
  expect(severityCell('genset_1').textContent).toBe('no shortfall — the site copes without it')
  expect(severityCell('boiler').textContent).toBe('no outage frequency (outage rate or repair time missing or zero)')
  expect(severityCell('chp').textContent).toBe('no price set for undelivered energy')
  expect(severityCell('hp').textContent).toBe('not counted (outside the electricity metric)')
  expect(severityCell('old_row').textContent).toBe('€0.0')
  expect(severityCell('pv').textContent).toBe('€0.4')
})
