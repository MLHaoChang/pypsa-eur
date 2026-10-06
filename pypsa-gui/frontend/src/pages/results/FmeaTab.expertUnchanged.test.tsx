// Guided-mode deferred spec §4.2 / §0.1: in Expert mode the FMEA tab is the
// P28-GO commit's tab — same header, same table, same markup. The snapshot
// was recorded on `e7f139b84` before any P29 edit (the P23 method). B2 (the
// Guided variant) must leave it byte-identical; B3's severity-cell `title`
// on a €0 row is the one justified update (plan, P29 phase note).
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
// The stress editor has its own suites; keep this snapshot to the worksheet.
vi.mock('./StressScenarioEditor', () => ({
  default: () => <div data-testid="stress-editor-stub" />,
}))

const ROWS = [
  {
    mode_id: 'generator:genset_1:forced_outage', component_class: 'Generator',
    name: 'genset_1', failure_class: 'A', occurrence_per_year: 0,
    occurrence_basis: 'FOR', severity_eur: 0, criticality_eur_per_year: 0,
    delta_eue_mwh: 0, in_metric_scope: true, engine: 'copt',
    fidelity: 'analytic_convolution', zero_reason: 'no_shortfall',
  },
  {
    mode_id: 'generator:grid:forced_outage', component_class: 'Generator',
    name: 'grid', failure_class: 'A', occurrence_per_year: 2,
    occurrence_basis: 'FOR', severity_eur: 12_345.6,
    criticality_eur_per_year: 24_691.2, delta_eue_mwh: 1.5,
    in_metric_scope: true, engine: 'copt', fidelity: 'analytic_convolution',
    zero_reason: null,
  },
  {
    mode_id: 'link:heat_pump:forced_outage', component_class: 'Link',
    name: 'heat_pump', failure_class: 'B', occurrence_per_year: 1,
    occurrence_basis: 'FOR', severity_eur: 0, criticality_eur_per_year: 0,
    delta_eue_mwh: 0, in_metric_scope: false, engine: 'lp_proxy',
    fidelity: 'deterministic_scenario', zero_reason: 'out_of_scope',
  },
  {
    mode_id: 'stress:cold_snap', component_class: 'Network',
    name: 'cold_snap', failure_class: 'C', occurrence_per_year: 0.1,
    occurrence_basis: 'scenario', severity_eur: 50_000,
    criticality_eur_per_year: 5_000, delta_eue_mwh: 10,
    in_metric_scope: true, engine: 'lp_proxy',
    fidelity: 'deterministic_scenario', zero_reason: null,
  },
]
const EXPERT = {
  mode_id: 'manual:cyber', component_class: 'Network', name: 'cyber',
  failure_class: 'D', occurrence_per_year: 0.5, occurrence_basis: 'expert',
  severity_eur: 200, criticality_eur_per_year: 100, in_metric_scope: false,
  mitigability: 'segmentation', engine: 'expert', fidelity: 'expert_judgement',
}

afterEach(() => cleanup())
beforeEach(() => {
  useUIStore.setState({ uiMode: 'expert', currentProject: 'Demo' })
  vi.mocked(resultsApi.getFmeaModes).mockReset().mockResolvedValue({
    per_mode: ROWS, sweep_status: null, sweep_error: null, voll_eur_per_mwh: 5000,
  })
  vi.mocked(resultsApi.getWorksheet).mockReset()
    .mockResolvedValue({ version: 1, manual_rows: [EXPERT], overlays: {} })
  vi.mocked(resultsApi.getStressScenarios).mockReset().mockResolvedValue({ scenarios: [] })
})

describe('FMEA tab — Expert unchanged', () => {
  it('renders the base header and worksheet', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { container } = render(
      <QueryClientProvider client={client}><FmeaTab /></QueryClientProvider>)
    await screen.findByText('cyber')
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })
    expect((container.firstChild as HTMLElement).outerHTML).toMatchSnapshot()
  })
})
