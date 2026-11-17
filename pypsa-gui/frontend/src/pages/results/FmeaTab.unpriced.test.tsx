// template-e2e 11: with no VOLL, /fmea_modes sends null € fields (#1645).
// The worksheet reads "Not set" (never €0, never a bare dash), lists the
// unpriced modes after the priced ones, and says why in a visible line whose
// link opens the VOLL field — the same in Guided and Expert.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import FmeaTab from './FmeaTab'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { openVollField } from '../../layout/flowNavigation'

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
vi.mock('../../layout/flowNavigation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../layout/flowNavigation')>()
  return { ...actual, openVollField: vi.fn() }
})
vi.mock('./StressScenarioEditor', () => ({ default: () => <div data-testid="stress-editor-stub" /> }))

const ROW = (name: string) => ({
  mode_id: `generator:${name}:forced_outage`, component_class: 'Generator', name,
  failure_class: 'A', occurrence_per_year: 2, occurrence_basis: 'FOR',
  severity_eur: null, criticality_eur_per_year: null, delta_eue_mwh: 1,
  in_metric_scope: true, engine: 'copt', fidelity: 'analytic_convolution',
})
const EXPERT = {
  mode_id: 'manual:cyber', component_class: 'Network', name: 'cyber',
  failure_class: 'D', occurrence_per_year: 0.5, occurrence_basis: 'expert',
  severity_eur: 200, criticality_eur_per_year: 100, in_metric_scope: false,
  mitigability: 'segmentation', engine: 'expert', fidelity: 'expert_judgement',
}

afterEach(() => cleanup())
beforeEach(() => {
  vi.mocked(openVollField).mockReset()
  vi.mocked(resultsApi.getFmeaModes).mockReset().mockResolvedValue({
    per_mode: [ROW('grid'), ROW('gen')], sweep_status: null, sweep_error: null,
    voll_eur_per_mwh: 0,
  })
  vi.mocked(resultsApi.getWorksheet).mockReset()
    .mockResolvedValue({ version: 1, manual_rows: [EXPERT], overlays: {} })
  vi.mocked(resultsApi.getStressScenarios).mockReset().mockResolvedValue({ scenarios: [] })
})

async function renderTab(mode: 'guided' | 'expert') {
  useUIStore.setState({ uiMode: mode, currentProject: 'Demo' })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><FmeaTab /></QueryClientProvider>)
  await screen.findByText('cyber')
  await act(async () => { await new Promise(r => setTimeout(r, 20)) })
}
const rowNames = () => Array.from(screen.getByTestId('fmea-table').querySelectorAll('tbody tr'))
  .map(tr => (tr.querySelector('td')?.textContent ?? '').trim())

describe.each(['guided', 'expert'] as const)('FMEA tab, unpriced modes (%s)', (mode) => {
  it('€ cells read "Not set", never €0 or a bare dash', async () => {
    await renderTab(mode)
    const tr = screen.getByText('grid').closest('tr') as HTMLTableRowElement
    const notSet = tr.querySelectorAll('[data-testid="fmea-not-set"]')
    expect(notSet).toHaveLength(2)
    notSet.forEach(c => expect(c.textContent).toBe('Not set'))
    expect(tr.textContent).not.toMatch(/€\s?0|—/)
  })

  it('lists unpriced modes after the priced one', async () => {
    await renderTab(mode)
    const names = rowNames()
    const at = (n: string) => names.findIndex(t => t.includes(n))
    expect(at('cyber')).toBeLessThan(at('gen'))
    expect(at('cyber')).toBeLessThan(at('grid'))
  })

  it('keeps unpriced modes last when the risk column is sorted the other way', async () => {
    await renderTab(mode)
    const header = screen.getByTestId('fmea-table').querySelectorAll('thead th')
    const risk = Array.from(header).find(th => /risk|Criticality/.test(th.textContent ?? '')) as HTMLElement
    fireEvent.click(risk)   // desc → asc (or asc → desc): the sort direction flips
    const names = rowNames()
    const at = (n: string) => names.findIndex(t => t.includes(n))
    expect(at('cyber')).toBeLessThan(at('gen'))
    expect(at('cyber')).toBeLessThan(at('grid'))
  })

  it('shows no line once every mode is priced', async () => {
    vi.mocked(resultsApi.getFmeaModes).mockResolvedValue({
      per_mode: [{ ...ROW('grid'), severity_eur: 100, criticality_eur_per_year: 200 }],
      sweep_status: null, sweep_error: null, voll_eur_per_mwh: 5000,
    })
    await renderTab(mode)
    expect(screen.queryByTestId('fmea-unpriced-line')).toBeNull()
    expect(screen.queryByTestId('fmea-not-set')).toBeNull()
  })

  it('says why in a visible line whose link opens the VOLL field', async () => {
    await renderTab(mode)
    const line = screen.getByTestId('fmea-unpriced-line')
    expect(line.textContent).toContain('Not priced: 2 modes are listed last and left out of the ranking.')
    const link = screen.getByTestId('fmea-unpriced-set-voll')
    expect(link.textContent).toBe('Set the Value of Lost Load (VOLL) in Solver settings ›')
    fireEvent.click(link)
    expect(openVollField).toHaveBeenCalledWith('FMEA')
  })
})
