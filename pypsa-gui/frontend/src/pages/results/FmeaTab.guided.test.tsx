// P29 (B2, deferred spec §4.2): the FMEA tab reached from Guided reads in
// plain words — class letters become labels with a hover, the engine badge
// becomes the row's hover, "FOR" reads "outage rate". The test ids stay; the
// table gains `data-guided="1"`. The hover texts are the guide catalogue's
// fields, imported straight from the backend package as Term.test does.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import guide from '../../../../backend/data/guides/eh_fmea_guide.json'
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

const fields = (guide as { fields: Record<string, string> }).fields

const ROW = (over: Record<string, unknown>) => ({
  component_class: 'Generator', occurrence_per_year: 2, occurrence_basis: 'FOR',
  severity_eur: 100, criticality_eur_per_year: 200, delta_eue_mwh: 1,
  in_metric_scope: true, engine: 'copt', fidelity: 'analytic_convolution', ...over,
})
const ROWS = [
  ROW({ mode_id: 'generator:grid:forced_outage', name: 'grid', failure_class: 'A' }),
  ROW({ mode_id: 'link:hp:forced_outage', name: 'hp', failure_class: 'B', component_class: 'Link',
    engine: 'lp_proxy', fidelity: 'deterministic_scenario', criticality_eur_per_year: 150 }),
  ROW({ mode_id: 'stress:cold', name: 'cold', failure_class: 'C', occurrence_basis: 'scenario:parametric',
    engine: 'lp_proxy', fidelity: 'deterministic_scenario', criticality_eur_per_year: 120 }),
]
const EXPERT = {
  mode_id: 'manual:cyber', component_class: 'Network', name: 'cyber',
  failure_class: 'D', occurrence_per_year: 0.5, occurrence_basis: 'expert',
  severity_eur: 200, criticality_eur_per_year: 100, in_metric_scope: false,
  mitigability: 'segmentation', engine: 'expert', fidelity: 'expert_judgement',
}

afterEach(() => cleanup())
beforeEach(() => {
  useUIStore.setState({ uiMode: 'guided', currentProject: 'Demo' })
  vi.mocked(resultsApi.getFmeaModes).mockReset().mockResolvedValue({
    per_mode: ROWS, sweep_status: null, sweep_error: null, voll_eur_per_mwh: 5000,
  })
  vi.mocked(resultsApi.getWorksheet).mockReset()
    .mockResolvedValue({ version: 1, manual_rows: [EXPERT], overlays: {} })
  vi.mocked(resultsApi.getStressScenarios).mockReset().mockResolvedValue({ scenarios: [] })
})

async function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><FmeaTab /></QueryClientProvider>)
  await screen.findByText('cyber')
  await act(async () => { await new Promise(r => setTimeout(r, 20)) })
}
const row = (name: string) => screen.getByText(name).closest('tr') as HTMLTableRowElement
const cellTexts = () => Array.from(screen.getByTestId('fmea-table').querySelectorAll('td, th'))
  .map(c => (c.textContent ?? '').trim())

describe('FMEA tab in Guided', () => {
  it('the title and header prose are plain', async () => {
    await renderTab()
    expect(screen.getByText('What could fail, and what it would cost')).toBeTruthy()
    expect(screen.queryByText('FMEA worksheet')).toBeNull()
    expect(screen.getByText(/Each row is one way the site can lose power\./).textContent).toBe(
      'Each row is one way the site can lose power. Occurrence is how often it happens; ' +
      'severity is what one event costs; the last column is the yearly risk. Your own rows ' +
      'and notes are kept with the project.')
  })

  it('the table keeps its id and is marked Guided', async () => {
    await renderTab()
    expect(screen.getByTestId('fmea-table').getAttribute('data-guided')).toBe('1')
  })

  it('no cell shows a class letter or an engine id', async () => {
    await renderTab()
    const bad = cellTexts().filter(t => /^(copt|lp_proxy|expert|A|B|C|D)$/.test(t))
    expect(bad).toEqual([])
    for (const t of cellTexts()) expect(t).not.toMatch(/\b(copt|lp_proxy)\b/)
  })

  it.each([
    ['grid', 'fmea_class_a', 'Generator outage'],
    ['hp', 'fmea_class_b', 'Link outage'],
    ['cold', 'fmea_class_c', 'Stress scenario'],
    ['cyber', 'fmea_class_d', 'Your own row'],
  ])('%s: the class reads %s → %s, with the catalogue hover', async (name, key, label) => {
    await renderTab()
    const term = row(name).querySelector(`[data-testid="term-${key}"]`) as HTMLElement
    expect(term.textContent).toBe(label)
    expect(fields[key]).toBeTruthy()
    expect(term.getAttribute('data-tip')).toBe(fields[key])
  })

  it('"FOR" reads "outage rate" with the occurrence hover', async () => {
    await renderTab()
    const term = row('grid').querySelector('[data-testid="term-fmea_occurrence"]') as HTMLElement
    expect(term.textContent).toBe('outage rate')
    expect(term.getAttribute('data-tip')).toBe(fields.fmea_occurrence)
    expect(row('grid').textContent).not.toContain('FOR')
  })

  it('the engine is the row hover, in words', async () => {
    await renderTab()
    const title = row('grid').getAttribute('title') ?? ''
    expect(title.startsWith(fields.fmea_engine)).toBe(true)
    expect(title).not.toMatch(/copt|lp_proxy|COPT|LP proxy/)
    expect(row('hp').getAttribute('title')!.startsWith(fields.fmea_engine)).toBe(true)
  })

  it('the severity and yearly-risk headers carry the catalogue hovers; yearly risk is the last data column', async () => {
    await renderTab()
    const table = screen.getByTestId('fmea-table')
    expect(table.querySelector('[data-testid="term-fmea_severity"]')!.getAttribute('data-tip'))
      .toBe(fields.fmea_severity)
    const crit = table.querySelector('[data-testid="term-fmea_criticality"]')!
    expect(crit.getAttribute('data-tip')).toBe(fields.fmea_criticality)
    const ths = Array.from(table.querySelectorAll('thead th'))
    expect(ths[ths.length - 1].contains(crit)).toBe(true)
  })

  it('Expert still shows the letters and badges', async () => {
    useUIStore.setState({ uiMode: 'expert' })
    await renderTab()
    expect(screen.getByText('FMEA worksheet')).toBeTruthy()
    expect(screen.getByTestId('fmea-table').hasAttribute('data-guided')).toBe(false)
    expect(cellTexts()).toContain('A')
    expect(cellTexts()).toContain('copt')
  })
})
