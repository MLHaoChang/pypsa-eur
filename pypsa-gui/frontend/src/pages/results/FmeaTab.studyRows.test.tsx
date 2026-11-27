// template-e2e 24: while the last Energy Hub study is the newest FMEA source,
// /fmea_modes serves the study's rows (`source: "eh_study"`). The worksheet
// lists them in the report's order (class B, then class A, each ranked within
// its class), unsorted until the user clicks a column, and dates their source.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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
vi.mock('./StressScenarioEditor', () => ({ default: () => <div data-testid="stress-editor-stub" /> }))

const ROW = (mode_id: string, name: string, failure_class: string, crit: number | null) => ({
  mode_id, name, failure_class, component_class: failure_class === 'A' ? 'Generator' : 'Link',
  occurrence_per_year: 0.5, occurrence_basis: 'FOR', severity_eur: crit,
  criticality_eur_per_year: crit, delta_eue_mwh: 0.1, in_metric_scope: true,
  engine: 'lp', fidelity: 'dispatch',
})
// A class-A row costlier than every class-B row: it must still list after them.
const STUDY = [
  ROW('link:critical_feeder:forced_outage', 'critical_feeder', 'B', 473_000),
  ROW('link:genset_1:forced_outage', 'genset_1', 'B', 0),
  ROW('gen:genset_new:forced_outage', 'genset_new', 'A', 900_000),
]

afterEach(() => cleanup())
beforeEach(() => {
  useUIStore.setState({ uiMode: 'expert', currentProject: 'Demo' })
  vi.mocked(resultsApi.getWorksheet).mockReset()
    .mockResolvedValue({ version: 1, manual_rows: [], overlays: {} })
  vi.mocked(resultsApi.getStressScenarios).mockReset().mockResolvedValue({ scenarios: [] })
})

async function renderTab(payload: Record<string, unknown>) {
  vi.mocked(resultsApi.getFmeaModes).mockReset().mockResolvedValue(payload)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><FmeaTab /></QueryClientProvider>)
  await screen.findByText('genset_new')
  await act(async () => { await new Promise(r => setTimeout(r, 20)) })
}
const names = () => Array.from(screen.getByTestId('fmea-table').querySelectorAll('tbody tr'))
  .map(tr => (tr.querySelector('td')?.textContent ?? '').trim())

describe('FMEA worksheet with the study as its source', () => {
  it('lists the study rows in the report order, unsorted, and dates the source', async () => {
    await renderTab({ per_mode: STUDY, source: 'eh_study', study_finished_at: 1_790_000_000,
      voll_eur_per_mwh: null })
    expect(names()).toEqual(['critical_feeder', 'genset_1', 'genset_new'])
    const line = screen.getByTestId('fmea-source-line').textContent ?? ''
    expect(line).toMatch(/Energy Hub study's ranking of .+\(as in the reference design and the report\)/)
    expect(line).toMatch(/Edits made since then are not reflected/)
  })

  it('ranks live rows by criticality, with no source line', async () => {
    await renderTab({ per_mode: STUDY, source: 'live', voll_eur_per_mwh: 5000 })
    expect(names()).toEqual(['genset_new', 'critical_feeder', 'genset_1'])
    expect(screen.queryByTestId('fmea-source-line')).toBeNull()
  })

  it('says unpriced study rows need the study run again', async () => {
    await renderTab({ per_mode: STUDY.map(r => ({ ...r, severity_eur: null,
      criticality_eur_per_year: null })), source: 'eh_study', voll_eur_per_mwh: null })
    expect(screen.getByTestId('fmea-study-reprice').textContent).toMatch(/run the study again/)
  })
})
