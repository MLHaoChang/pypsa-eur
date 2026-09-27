// Guided-mode spec §5.3 / §5.5 (Results): the headline sentence per outcome,
// cost, the three costliest risks, what the study could not establish, the
// stale banner from the review's `stale` boolean (never `source` — gate N8),
// and "Open full report" into Results → Adequacy.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi, type EhReferenceDesignReport } from '../../../api/simulation'
import { useUIStore } from '../../../store/uiStore'
import { fmtCurrency } from '../../results/shared'
import { headline } from '../headline'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from '../hubDesignStore'
import { REPORT, review } from '../testFixtures'
import { ResultsCard } from './ResultsCard'

vi.mock('../../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: vi.fn(), getEhReview: vi.fn(), getEhReferenceDesign: vi.fn(),
      getEhTemplate: vi.fn(),
    },
  }
})

function study(report: EhReferenceDesignReport = REPORT) {
  return { status: 'done', archetype: 'weak_flexible' as const, report }
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: 'hubDesign',
    resultsTabRequest: null, ehReportRequest: false })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true, step: 'results' })
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study())
  vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
  vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(null)
  vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(null)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ResultsCard /></QueryClientProvider>)
  return userEvent.setup()
}
const verdictText = async () => (await screen.findByTestId('hub-results-verdict')).textContent

describe('ResultsCard headline (§5.5)', () => {
  const cases: [string, ReturnType<typeof review>, EhReferenceDesignReport, string][] = [
    ['fail', review(), REPORT,
      'Not certified: about 12 h/yr of shortfall vs a 3 h/yr goal — driven by grid_import'],
    ['inconclusive', review({ summary: { verdict: 'inconclusive', mc_lole_h_per_year: 3.2, target_lole_h: 3 } }),
      { ...REPORT, sections: { certification: { status: 'ok', payload: {
        verdict: 'inconclusive', lole_h_per_year: 3.2, lole_ci: [2.4, 4.1], horizon_years: 1, target_lole_h: 3 } } } },
      'Not decided: the shortfall estimate (2–4 h/yr) straddles the 3 h/yr goal — more simulation runs would settle it.'],
    ['pass', review({ summary: { verdict: 'pass', mc_lole_h_per_year: 0.44, target_lole_h: 3 } }), REPORT,
      'Certified: about 0.4 h/yr of shortfall, under the 3 h/yr goal.'],
    ['no target', review({ summary: { verdict: null, mc_lole_h_per_year: 1.25, target_lole_h: null } }), REPORT,
      'No reliability goal was set — the study reports 1.3 h/yr of shortfall. Set a goal to certify.'],
    ['no goal, no shortfall number', review({ summary: { verdict: null, mc_lole_h_per_year: null, target_lole_h: null } }),
      { ...REPORT, mc_lole_h: null, sections: { certification: { status: 'skipped', note: 'no LOLE target — certification not requested' } } },
      'No reliability goal is set for this site, so the study did not certify it — set an allowed shortfall in step 3 (Goal) to get a verdict.'],
    ['not established', review({ summary: { verdict: null, mc_lole_h_per_year: null, target_lole_h: 3 } }),
      { ...REPORT, sections: { certification: { status: 'not_established', note: 'no hub-side buses' } } },
      'The study could not certify reliability: no hub-side buses'],
  ]
  for (const [name, rv, rep, want] of cases) {
    it(name, async () => {
      vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study(rep))
      vi.mocked(resultsApi.getEhReview).mockResolvedValue(rv)
      mount()
      await waitFor(async () => expect(await verdictText()).toBe(want))
      expect(want).toBe(headline(rv, rep))
    })
  }
})

// P24-FE gate B1: `cost_at_target_eur` is the cost of the planned design at
// the pack's energy target, without shortfall costs — never the cost of
// meeting the user's reliability goal.
describe('ResultsCard cost label (gate B1)', () => {
  const LABEL = 'Yearly cost of this design (before any shortfall costs)'
  const cases: [string, ReturnType<typeof review>][] = [
    ['fail', review()],
    ['no goal', review({ summary: { verdict: null, mc_lole_h_per_year: null, target_lole_h: null } })],
  ]
  for (const [name, rv] of cases) {
    it(`${name}: the cost is the design's, and "goal" is not next to it`, async () => {
      vi.mocked(resultsApi.getEhReview).mockResolvedValue(rv)
      mount()
      const cost = await screen.findByTestId('hub-results-cost')
      expect(cost.textContent).toContain(LABEL)
      expect(cost.textContent).toContain(fmtCurrency(12_345_678))
      expect(cost.textContent).not.toMatch(/goal/i)
      expect(cost.querySelector('[data-testid="term-cost_at_target"]')?.getAttribute('data-tip'))
        .not.toMatch(/goal/i)
    })
  }
})

describe('ResultsCard body', () => {
  it('cost via fmtCurrency, three risks, the gaps', async () => {
    mount()
    await screen.findByTestId('hub-results-verdict')
    expect(screen.getByTestId('hub-results-cost').textContent).toContain(fmtCurrency(12_345_678))
    expect(screen.getByTestId('hub-results-risks-0').textContent).toContain('grid_import')
    expect(screen.getByTestId('hub-results-risks-0').textContent).toContain(fmtCurrency(590_000))
    expect(screen.getByTestId('hub-results-risks-2').textContent).toContain('genset_2')
    expect(screen.queryByTestId('hub-results-risks-3')).toBeNull()
    const gaps = screen.getByTestId('hub-results-gaps').textContent ?? ''
    expect(gaps).toContain('VOLL must be above zero for the frontier.')
    expect(gaps).not.toMatch(/\bfrontier:/)      // a plain label, not the section id
    expect(screen.queryByTestId('hub-results-stale')).toBeNull()
  })

  it('a risk with no measurable cost says so instead of "€0.00"', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study({ ...REPORT, sections: { ...REPORT.sections,
      fmea_top: { status: 'ok', payload: { rows: [
        { mode_id: 'B:a', name: 'site_transformer', criticality_eur_per_year: 7_890_000 },
        { mode_id: 'B:e', name: 'electrolyser', criticality_eur_per_year: 0 },
      ] } } } }))
    mount()
    const r1 = await screen.findByTestId('hub-results-risks-1')
    expect(r1.textContent).toContain('electrolyser')
    expect(r1.textContent).toContain('no measurable cost')
    expect(r1.textContent).not.toContain('€0')
  })

  it('stale banner from the boolean (not the source text)', async () => {
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review({ stale: true, source: 'stored report' }))
    mount()
    expect((await screen.findByTestId('hub-results-stale')).textContent).toBe(
      'These results are from an earlier study; the network was solved since. Run again to refresh.')
  })

  it('a "study record" source without stale:true shows no banner', async () => {
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(
      review({ stale: false, source: 'study record (the stored report was cleared by a later solve)' }))
    mount()
    await screen.findByTestId('hub-results-verdict')
    expect(screen.queryByTestId('hub-results-stale')).toBeNull()
  })

  it('Open full report → Results, Adequacy, and the report request', async () => {
    const user = mount()
    await user.click(await screen.findByTestId('hub-results-open-report'))
    const s = useUIStore.getState()
    expect(s.activeSlidePanel).toBe('results')
    expect(s.resultsTabRequest).toBe('adequacy')
    expect(s.ehReportRequest).toBe(true)
  })

  it('never shows the review\'s running message (N3)', async () => {
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(
      { status: 'running', message: "the EH study is still running — poll get_adequacy_results('eh_study') first" })
    mount()
    await screen.findByTestId('hub-card-results')
    await new Promise(r => setTimeout(r, 0))
    expect(document.body.textContent).not.toMatch(/get_adequacy_results|poll/)
  })
})
