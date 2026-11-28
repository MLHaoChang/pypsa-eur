// Guided-mode spec §5.3 / §5.5 (Results): the headline sentence per outcome,
// cost, the three costliest risks, what the study could not establish, the
// stale banner from the review's `stale` boolean (never `source` — gate N8),
// and "Open full report" into Results → Adequacy.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { kpiFigure, splitValueUnit } from '../../../utils/format'
import { TERM_FALLBACK } from '../shared/Term'
import { networkApi } from '../../../api/network'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi, simulationApi, type EhReferenceDesignReport } from '../../../api/simulation'
import { useUIStore } from '../../../store/uiStore'
import { fmtCurrency } from '../../results/shared'
import { headline } from '../headline'
import { kpiMoney } from '../plainWords'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from '../hubDesignStore'
import { DC_TEMPLATE, REPORT, SITE_TEMPLATE, review } from '../testFixtures'
import { EDITED_TEXT, FRESHNESS_UNKNOWN_TEXT, ResultsCard } from './ResultsCard'
import { useProjectFlow } from '../../../hooks/useProjectFlow'

vi.mock('../../../hooks/useProjectFlow', () => ({ useProjectFlow: vi.fn() }))

function flowWith(by: Record<string, string | null>, req = { optimisation: true, study: true, loadflow: false }) {
  vi.mocked(useProjectFlow).mockReturnValue({ flow: { deferred: false, run_request: req,
    sections: [{ section: 'results', freshness_by_kind: by }] },
  } as unknown as ReturnType<typeof useProjectFlow>)
}

vi.mock('../../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: vi.fn(), getEhReview: vi.fn(), getEhReferenceDesign: vi.fn(),
      getEhTemplate: vi.fn(), getEhLevers: vi.fn(),
    },
    simulationApi: { ...actual.simulationApi, getStatus: vi.fn() },
  }
})

function study(report: EhReferenceDesignReport = REPORT) {
  // A study the server compared with the current network and found current (GK B F1: only `false` is).
  return { status: 'done', archetype: 'weak_flexible' as const, report, edited_since_study: false as boolean | null }
}

beforeEach(() => {
  vi.mocked(useProjectFlow).mockReturnValue({ flow: null } as unknown as ReturnType<typeof useProjectFlow>)
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: 'hubDesign',
    resultsTabRequest: null, ehReportRequest: false })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true, step: 'results' })
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study())
  vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
  vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(null)
  vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(null)
  // Unread by default, as before this mock existed: no claim either way about the solve.
  vi.mocked(simulationApi.getStatus).mockRejectedValue(new Error('no status'))
  vi.mocked(resultsApi.getEhLevers).mockResolvedValue(null)
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
      'Not certified: about 12.4 h/yr of shortfall vs a 3 h/yr goal, driven by Grid import'],
    ['inconclusive', review({ summary: { verdict: 'inconclusive', mc_lole_h_per_year: 3.2, target_lole_h: 3 } }),
      { ...REPORT, sections: { certification: { status: 'ok', payload: {
        verdict: 'inconclusive', lole_h_per_year: 3.2, lole_ci: [2.4, 4.1], horizon_years: 1, target_lole_h: 3 } } } },
      'Not decided: the shortfall estimate (2.4–4.1 h/yr) straddles the 3 h/yr goal: more simulation runs would settle it.'],
    ['pass', review({ summary: { verdict: 'pass', mc_lole_h_per_year: 0.44, target_lole_h: 3 } }), REPORT,
      'Certified: about 0.4 h/yr of shortfall, under the 3 h/yr goal.'],
    ['no target', review({ summary: { verdict: null, mc_lole_h_per_year: 1.25, target_lole_h: null } }), REPORT,
      'No reliability goal was set: the study reports 1.3 h/yr of shortfall. Set a goal to certify.'],
    ['no goal, no shortfall number', review({ summary: { verdict: null, mc_lole_h_per_year: null, target_lole_h: null } }),
      { ...REPORT, mc_lole_h: null, sections: { certification: { status: 'skipped', note: 'no LOLE target — certification not requested' } } },
      'No reliability goal is set for this site, so the study did not certify it: set an allowed shortfall in section 8, Scenarios & optimisation, to get a verdict.'],
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
// The shared tile rounds to the KPI standard (utils/format.ts::kpiFigure).
const KPI_COST = (() => {
  const raw = splitValueUnit(fmtCurrency(12_345_678))
  const p = kpiFigure(raw.value, raw.unit)
  return p.unit ? `${p.value} ${p.unit}` : p.value
})()

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
      expect(cost.querySelector('[data-kpi-value]')?.getAttribute('data-kpi-value')).toBe(KPI_COST)
      expect(cost.textContent).not.toMatch(/goal/i)
      // the glossary text rides on the tile's hover, still never "goal"
      expect(cost.getAttribute('title')).toBe(TERM_FALLBACK.cost_at_target)
      expect(cost.getAttribute('title')).not.toMatch(/goal/i)
    })
  }
})

describe('ResultsCard product cost (issue 28)', () => {
  it('shows the hydrogen cost per kg from the study when it is established', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study({ ...REPORT, tea: { lcoh_eur_per_kg: 2.345, lcoh_status: 'ok' } }))
    mount()
    const tile = await screen.findByTestId('hub-results-lcoh')
    expect(tile.textContent).toContain('Cost of hydrogen')
    expect(tile.querySelector('[data-kpi-value]')?.getAttribute('data-kpi-value')).toBe('€\u00a02.35/kg')
  })
  it('shows no hydrogen tile for a design with none', async () => {
    mount()
    await screen.findByTestId('hub-results-cost')
    expect(screen.queryByTestId('hub-results-lcoh')).toBeNull()
  })
  it('shows the ammonia cost per tonne from the study beside an unchanged hydrogen tile (issue 28a)', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study({ ...REPORT, tea: {
      lcoh_eur_per_kg: 2.345, lcoh_status: 'ok', lcoa_eur_per_t: 612.4, lcoa_status: 'ok', currency: 'EUR' } }))
    mount()
    const tile = await screen.findByTestId('hub-results-lcoa')
    expect(tile.textContent).toContain('Cost of ammonia')
    expect(tile.querySelector('[data-kpi-value]')?.getAttribute('data-kpi-value')).toBe('€\u00a0612/t')
    expect(screen.getByTestId('hub-results-lcoh').querySelector('[data-kpi-value]')
      ?.getAttribute('data-kpi-value')).toBe('€\u00a02.35/kg')

    // One two-column row, hydrogen left, ammonia right (UX 31).
    const row = screen.getByTestId('hub-results-products')
    expect(row.className).toContain('grid-cols-2')
    expect([...row.children].map(e => e.getAttribute('data-testid'))).toEqual(['hub-results-lcoh', 'hub-results-lcoa'])
  })
  it('writes a cost per tonne from 1,000 in full, never as k (UX 30)', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study({ ...REPORT, tea: {
      lcoa_eur_per_t: 1150.4, lcoa_status: 'ok', currency: 'USD' } }))
    mount()
    const tile = await screen.findByTestId('hub-results-lcoa')
    expect(tile.querySelector('[data-kpi-value]')?.getAttribute('data-kpi-value')).toBe('$\u00a01,150/t')
    expect(tile.getAttribute('title')).toBe('Levelised cost of the ammonia this design produces, per tonne, as the study priced it.')
  })
  it('dims both product tiles under the one Out of date banner, as the cost tile', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study({ ...REPORT, tea: {
      lcoh_eur_per_kg: 2.345, lcoh_status: 'ok', lcoa_eur_per_t: 612.4, lcoa_status: 'ok' } }))
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review({ stale: true, source: 'stored report' }))
    mount()
    expect((await screen.findByTestId('hub-results-stale')).textContent).toContain('Out of date')
    for (const id of ['hub-results-lcoh', 'hub-results-lcoa']) {
      const tile = screen.getByTestId(id)
      expect(tile.getAttribute('data-state')).toBe('ready')
      expect(tile.textContent).not.toContain('Out of date')
      expect(tile.querySelector('.opacity-60')).not.toBeNull()
    }
  })
  it('shows no ammonia tile for a design with no ammonia synthesis', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study({ ...REPORT, tea: {
      lcoh_eur_per_kg: 2.345, lcoh_status: 'ok', lcoa_eur_per_t: null, lcoa_status: 'skipped' } }))
    mount()
    await screen.findByTestId('hub-results-lcoh')
    expect(screen.queryByTestId('hub-results-lcoa')).toBeNull()
  })
})

describe('ResultsCard body', () => {
  it('cost via fmtCurrency, three risks, the gaps', async () => {
    mount()
    await screen.findByTestId('hub-results-verdict')
    expect(screen.getByTestId('hub-results-cost').querySelector('[data-kpi-value]')
      ?.getAttribute('data-kpi-value')).toBe(KPI_COST)
    expect(screen.getByTestId('hub-results-risks-0').textContent).toContain('Grid import')
    expect(screen.getByTestId('hub-results-risks-0').textContent).not.toContain('grid_import')
    // The tile rounding (UX audit M8): "€590 k", never "€590.00 k".
    expect(screen.getByTestId('hub-results-risks-0').textContent).toContain(kpiMoney(590_000))
    expect(screen.getByTestId('hub-results-risks-0').textContent).not.toMatch(/\.00/)
    expect(screen.getByTestId('hub-results-risks-2').textContent).toContain('Genset 2')
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
    expect(r1.textContent).toContain('Electrolyser')
    expect(r1.textContent).toContain('no measurable cost')
    expect(r1.textContent).not.toContain('€0')
  })

  // P29 (B3): the hub reads the row's `zero_reason`, the same text as the
  // FMEA tab; without the field it falls back to "no measurable cost".
  it('a zero-cost risk says why', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study({ ...REPORT, sections: { ...REPORT.sections,
      fmea_top: { status: 'ok', payload: { rows: [
        { mode_id: 'B:a', name: 'site_transformer', criticality_eur_per_year: 7_890_000, zero_reason: null },
        { mode_id: 'B:e', name: 'electrolyser', criticality_eur_per_year: 0, zero_reason: 'out_of_scope' },
        { mode_id: 'A:g', name: 'genset_1', criticality_eur_per_year: 0, zero_reason: 'no_shortfall' },
      ] } } } }))
    mount()
    const r1 = await screen.findByTestId('hub-results-risks-1')
    expect(r1.textContent).toContain('not counted (outside the electricity metric)')
    expect(r1.textContent).not.toContain('no measurable cost')
    expect(screen.getByTestId('hub-results-risks-0').textContent).toContain(kpiMoney(7_890_000))
    // A unit whose loss causes no shortfall is not a risk (UX S6, G3): it is
    // said once under the list, never ranked in it.
    expect(screen.queryByTestId('hub-results-risks-2')).toBeNull()
    expect(screen.getByTestId('hub-results-no-shortfall').textContent)
      .toBe('Also checked: Genset 1: no shortfall, the site copes without it.')
  })

  it('every unit copes: no ranked list, the units still named', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study({ ...REPORT, sections: { ...REPORT.sections,
      fmea_top: { status: 'ok', payload: { rows: [
        { mode_id: 'B:n1', name: 'pv', criticality_eur_per_year: 0, zero_reason: 'no_shortfall' },
      ] } } } }))
    mount()
    expect((await screen.findByTestId('hub-results-no-shortfall')).textContent)
      .toBe('Also checked: PV: no shortfall, the site copes without it.')
    expect(screen.queryByTestId('hub-results-risks')).toBeNull()
  })

  it('the three risks are drawn from the rows that are risks', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study({ ...REPORT, sections: { ...REPORT.sections,
      fmea_top: { status: 'ok', payload: { rows: [
        { mode_id: 'B:a', name: 'grid_import', criticality_eur_per_year: 500 },
        { mode_id: 'B:n1', name: 'pv', criticality_eur_per_year: 0, zero_reason: 'no_shortfall' },
        { mode_id: 'B:n2', name: 'ups_battery', criticality_eur_per_year: 0, zero_reason: 'no_shortfall' },
        { mode_id: 'B:b', name: 'genset_1', criticality_eur_per_year: 300 },
        { mode_id: 'B:c', name: 'genset_2', criticality_eur_per_year: 200 },
      ] } } } }))
    mount()
    await screen.findByTestId('hub-results-risks-2')
    expect(screen.getAllByTestId(/^hub-results-risks-\d$/).map(e => e.textContent?.split(' ·')[0]))
      .toEqual(['Grid import', 'Genset 1', 'Genset 2'])
    expect(screen.getByTestId('hub-results-no-shortfall').textContent)
      .toBe('Also checked: PV, UPS battery: no shortfall, the site copes without them.')
  })

  it('stale banner from the boolean (not the source text)', async () => {
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review({ stale: true, source: 'stored report' }))
    mount()
    // The shared stale state (UX audit L5), not a custom banner.
    const stale = await screen.findByTestId('hub-results-stale')
    expect(stale.textContent).toContain('Out of date')
    expect(stale.textContent).toContain(
      'The verdict, expected shortfall, design cost and risks are from an earlier study; the network was solved since. Run again to refresh.')
    // One mark per card: the tile dims but carries no second "Out of date" pill; the banner offers the way
    // to refresh (UX review N4).
    const tile = screen.getByTestId('hub-results-cost')
    expect(tile.getAttribute('data-state')).toBe('ready')
    expect(tile.textContent).not.toContain('Out of date')
    expect(tile.querySelector('.opacity-60')).not.toBeNull()
    expect(stale.querySelector('button')?.textContent).toBe('Go to Goal and run the study')
  })

  it('says once, in words, that uncompared results were never checked (PG-12)', async () => {
    flowWith({ optimisation: 'uncompared', study: 'fresh', loadflow: 'fresh' })
    mount()
    const note = await screen.findByTestId('hub-results-unchecked')
    expect(note.textContent).toBe(
      'Optimisation results came with the project and were not checked against its inputs. Run to check them.')
    expect(screen.getAllByTestId('hub-results-unchecked')).toHaveLength(1)
    expect(screen.queryByTestId('hub-results-stale')).toBeNull()
  })

  it.each(['fresh', 'stale'])('shows no unchecked notice for a %s kind', async (word) => {
    flowWith({ optimisation: word, study: 'fresh', loadflow: 'fresh' })
    mount()
    await screen.findByTestId('hub-results-verdict')
    expect(screen.queryByTestId('hub-results-unchecked')).toBeNull()
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

// P26 walkthrough: the Results card read as the end of the flow; the next
// step (Improve: findings, the FMEA check) was only reachable from the rail.
describe('ResultsCard next step', () => {
  it('"Next: Improve" moves the rail to Improve as a user move', async () => {
    const user = mount()
    await screen.findByTestId('hub-results-verdict')
    const next = screen.getByTestId('hub-next-results')
    expect(next.textContent).toContain('Next: Improve')
    await user.click(next)
    expect(useHubDesignStore.getState().step).toBe('improve')
    expect(useHubDesignStore.getState().userMovedRail).toBe(true)
  })
})

// P26 gate friction 5: with no goal (the H2 hub) the headline says "set an
// allowed shortfall in step 3 (Goal)" but nothing took the user there.
describe('ResultsCard: Set a goal (P26 gate)', () => {
  it('no goal → "Set a goal" jumps to Goal as a user move', async () => {
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(
      review({ summary: { verdict: null, mc_lole_h_per_year: null, target_lole_h: null } }))
    const user = mount()
    await user.click(await screen.findByTestId('hub-results-set-goal'))
    expect(useHubDesignStore.getState().step).toBe('goal')
    expect(useHubDesignStore.getState().userMovedRail).toBe(true)
  })

  it('with a goal → no "Set a goal" button', async () => {
    mount()
    await screen.findByTestId('hub-results-verdict')
    expect(screen.queryByTestId('hub-results-set-goal')).toBeNull()
  })
})

// P33b 10b (D-2, D-3): `edited_since_study` on the study record says an edit
// seam was crossed since the study started. One amber line: the edited
// banner subsumes the solved-since one; `false` / `null` / absent say nothing of an edit, and
// `null` / absent say the freshness is unknown (GK B F1).
describe('ResultsCard: edited since the study (P33b)', () => {
  const EDITED = 'The network has been edited since this study, so its verdict, expected shortfall, design cost and risks describe the design as it was. Run again to refresh.'

  it('edited_since_study: true → the edited banner', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ ...study(), edited_since_study: true })
    mount()
    expect((await screen.findByTestId('hub-results-edited')).textContent).toBe(EDITED)
    expect(EDITED_TEXT).toBe(EDITED)
    expect(screen.queryByTestId('hub-results-stale')).toBeNull()
  })

  it('edited and review stale → only the edited banner', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ ...study(), edited_since_study: true })
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review({ stale: true, edited_since_study: true }))
    mount()
    await screen.findByTestId('hub-results-edited')
    await screen.findByTestId('hub-results-verdict')
    expect(screen.queryByTestId('hub-results-stale')).toBeNull()
  })

  it.each([false, null, undefined])('edited_since_study: %s → no edited banner', async (v) => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ ...study(), edited_since_study: v })
    mount()
    await screen.findByTestId('hub-results-verdict')
    expect(screen.queryByTestId('hub-results-edited')).toBeNull()
    expect(screen.queryByTestId('hub-results-stale')).toBeNull()
  })

  it('edited_since_study: false → current, no freshness line', async () => {
    mount()
    await screen.findByTestId('hub-results-verdict')
    expect(screen.queryByTestId('hub-results-freshness-unknown')).toBeNull()
  })

  // GK B F1: the server returns null when it could not compare (an older results_state.pkl);
  // an older record leaves the field out. Neither may read as current.
  it.each([
    ['null', { edited_since_study: null }],
    ['missing', {}],
  ])('edited_since_study %s → freshness unknown, in words', async (_name, over) => {
    const { edited_since_study: _drop, ...bare } = study()
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ ...bare, ...over } as never)
    mount()
    expect((await screen.findByTestId('hub-results-freshness-unknown')).textContent).toBe(FRESHNESS_UNKNOWN_TEXT)
    expect(FRESHNESS_UNKNOWN_TEXT).toContain("Can't tell whether the network changed since this study")
    expect(screen.queryByTestId('hub-results-edited')).toBeNull()
    expect(screen.queryByTestId('hub-results-stale')).toBeNull()
  })
})

describe('ResultsCard before the study (UX S6, G2)', () => {
  it('says the study has not run and offers the way to Goal', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
    const user = mount()
    const card = await screen.findByTestId('hub-card-results')
    await screen.findByText('Go to Goal and run the study')
    expect(card.textContent).toContain('The study writes its verdict, cost and risks here.')
    expect(screen.queryByTestId('hub-results-verdict')).toBeNull()
    await user.click(screen.getByText('Go to Goal and run the study'))
    expect(useHubDesignStore.getState().step).toBe('goal')
  })

  it('says the study is running, with no action', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(
      { status: 'running', archetype: 'weak_flexible' as const } as never)
    mount()
    const card = await screen.findByTestId('hub-card-results')
    await waitFor(() => expect(card.textContent).toContain('The study is running'))
    expect(screen.queryByText('Go to Goal and run the study')).toBeNull()
  })
})

// Control-strategy walkthrough 06 / improvement 2: the headline names its source run; an Expert solve is
// never "Not run yet" in Guided.
describe('ResultsCard source run (control-strategy 06)', () => {
  const solved = (o: Record<string, unknown> = {}) => vi.mocked(simulationApi.getStatus).mockResolvedValue(
    { status: 'completed', objective: 3473806.32, solve_time: 2.8, dispatch: 'fresh',
      solved_at: '2026-11-17T15:42:00Z', ...o } as never)

  it('after an Expert solve with no study, shows the solve cost from the dispatch solve, not "Not run yet"', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
    solved()
    flowWith({ optimisation: 'fresh', study: 'not_run', loadflow: 'not_run' })
    mount()
    const cost = await screen.findByTestId('hub-results-dispatch-cost')
    expect(cost.textContent).toContain('3.5')
    expect(cost.getAttribute('data-state')).toBe('ready')
    expect(screen.getByTestId('hub-results-source-from').textContent).toMatch(/^From the optimisation, 2026-11-1[78] \d\d:\d\d$/)
    expect(screen.getByTestId('hub-results-source-missing').textContent)
      .toBe('The reliability study has not run·Go to Goal and run the study ›')
    // Guided's cost words (UX 34 on #2140): a single period's figure is a year's; no solve, LP or objective.
    expect(cost.textContent).toContain('Yearly cost')
    expect(screen.getByTestId('hub-results-dispatch').textContent).not.toMatch(/solve|LP|objective|Total system cost/)
    // The headline's own "Not run yet" is gone; the other chapters keep theirs.
    expect(screen.getByTestId('hub-card-results').textContent).not.toContain('The study writes its verdict, cost and risks here.')
  })

  it('a discounted multi-period solve reads as the plan\'s cost, discounted, in Guided words (UX 34 on #2140)', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
    solved({ objective_basis: 'lp_objective', objective_discounted_to: 2027 })
    flowWith({ optimisation: 'fresh', study: 'not_run', loadflow: 'not_run' })
    mount()
    const cost = await screen.findByTestId('hub-results-dispatch-cost')
    await waitFor(() => expect(cost.textContent).toContain('Cost over the plan (discounted)'))
    expect(cost.textContent).toContain('discounted to 2027')
    expect(screen.getByTestId('hub-results-dispatch').textContent).not.toMatch(/solve|LP|objective|Total system cost/)
  })

  it('a solve older than the inputs shows its figure out of date, never as current (GK B3 C2)', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
    solved()
    flowWith({ optimisation: 'stale', study: 'not_run', loadflow: 'not_run' })
    mount()
    const cost = await screen.findByTestId('hub-results-dispatch-cost')
    expect(cost.getAttribute('data-state')).toBe('stale')
    expect(screen.getByTestId('hub-results-source-stale').textContent).toContain('Out of date')
  })

  it('a reopened project with no stamp names the solve without a time, never "has not run" (GK B3 C1)', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
    solved({ solved_at: null })
    flowWith({ optimisation: 'fresh', study: 'not_run', loadflow: 'not_run' })
    mount()
    expect((await screen.findByTestId('hub-results-source-from')).textContent).toBe('From the optimisation')
  })

  it('with the study and no solve, names the study and offers the solve', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(
      { status: 'idle', objective: null, solve_time: null, dispatch: 'none', solved_at: null } as never)
    const run = vi.fn()
    useUIStore.setState({ requestDispatchSolve: run })
    mount()
    const line = await screen.findByTestId('hub-results-source')
    expect(within(line).getByTestId('hub-results-source-from').textContent).toBe('From the reliability study')
    await waitFor(() => expect(within(line).getByTestId('hub-results-source-missing').textContent)
      .toBe('The optimisation has not run·Run the optimisation'))
    fireEvent.click(within(line).getByTestId('hub-results-source-action'))
    expect(run).toHaveBeenCalledTimes(1)
  })

  it('with the study and a solve, names the study only', async () => {
    solved()
    mount()
    const line = await screen.findByTestId('hub-results-source')
    await waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalled())
    await waitFor(() => expect(within(line).queryByTestId('hub-results-source-missing')).toBeNull())
  })
})

// WALK-IMPROVE-1: the cheaper battery duration the study's levers found, under
// the verdict, with one link to the design options.
describe('ResultsCard cheaper battery duration', () => {
  const SENTENCE = 'A 24 h battery also meets the energy-not-served cap at a system cost of € 3.1 m a year (design: 4 h, € 3.5 m)'
  function withLevers(best = 24) {
    const rep = { ...REPORT, sections: { ...REPORT.sections, sizing: { status: 'ok', payload: { assets: [
      { component: 'StorageUnit', name: 'battery', carrier: 'battery', built_mw: 2, built_mwh: 8, max_hours: 4 },
    ] } } } } as EhReferenceDesignReport
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study(rep))
    const rv = review()
    vi.mocked(resultsApi.getEhReview).mockResolvedValue({ ...rv, findings: [...rv.findings,
      { id: 'levers_best_option', severity: 'info', title: 'Cheapest lever option',
        evidence: { kind: 'storage_duration', value: best, cost_at_target_eur: best === 4 ? 3_500_000 : 3_100_000,
          spread_eur: 400_000 },
        recommendation: '', actions: [] }] })
    vi.mocked(resultsApi.getEhLevers).mockResolvedValue({ kind: 'storage_duration', options: [
      { kind: 'storage_duration', value: 4, unit: 'h', status: 'ok', cost_at_target_eur: 3_500_000, meets_target: true },
      { kind: 'storage_duration', value: 24, unit: 'h', status: 'ok', cost_at_target_eur: 3_100_000, meets_target: true },
      { kind: 'storage_duration', value: 72, unit: 'h', status: 'ok', cost_at_target_eur: 3_120_000, meets_target: true },
    ] })
  }

  it('shows the sentence under the verdict with one "Design options ›" link', async () => {
    withLevers()
    mount()
    const line = await screen.findByTestId('hub-results-cheaper-duration')
    expect(line.textContent?.replace(/\u00a0/g, ' ')).toBe(`${SENTENCE} Design options ›`)
    expect(line.textContent).not.toMatch(/goal/i)
    expect(line.querySelectorAll('button').length).toBe(1)
  })

  it('Guided: the link opens Results › Adequacy and asks for the levers block, in Guided', async () => {
    withLevers()
    useUIStore.setState({ uiMode: 'guided', resultsSectionRequest: null, flowReturn: null })
    const user = mount()
    await user.click(await screen.findByTestId('hub-results-design-options'))
    const s = useUIStore.getState()
    expect(s.uiMode).toBe('guided')
    // The Results panel's own "Back to Results" is the one way back; a Back
    // pill as well would show two.
    expect(s.flowReturn).toBeNull()
    expect(s.activeSlidePanel).toBe('results')
    expect(s.resultsTabRequest).toBe('adequacy')
    expect(s.resultsSectionRequest).toBe('eh_levers')
  })

  it('Expert: the link goes through showResultsTab, so Back returns', async () => {
    withLevers()
    useUIStore.setState({ uiMode: 'expert', resultsSectionRequest: null, flowReturn: null })
    const user = mount()
    await user.click(await screen.findByTestId('hub-results-design-options'))
    const s = useUIStore.getState()
    expect(s.uiMode).toBe('expert')
    expect(s.activeSlidePanel).toBe('results')
    expect(s.resultsTabRequest).toBe('adequacy')
    expect(s.resultsSectionRequest).toBe('eh_levers')
    expect(s.flowReturn).not.toBeNull()
  })

  it('nothing when the best option is the design duration', async () => {
    withLevers(4)
    mount()
    await screen.findByTestId('hub-results-verdict')
    await waitFor(() => expect(resultsApi.getEhLevers).toHaveBeenCalled())
    expect(screen.queryByTestId('hub-results-cheaper-duration')).toBeNull()
  })

  it('nothing, and no levers read, without the finding', async () => {
    mount()
    await screen.findByTestId('hub-results-verdict')
    expect(screen.queryByTestId('hub-results-cheaper-duration')).toBeNull()
    expect(resultsApi.getEhLevers).not.toHaveBeenCalled()
  })
})

// template-e2e 11 (TE2E-11-PLAIN-1): four templates have no reliability verdict.
describe('no reliability verdict for this site type (template-e2e 11)', () => {
  for (const id of ['eh_h2_hub', 'eh_h2_hub_staged', 'eh_ammonia_staged', 'eh_ipp_hybrid']) {
    it(`${id}: plain statement and check-instead, no verdict line`, async () => {
      vi.mocked(resultsApi.getEhTemplate).mockResolvedValue({ id, name: id, recommended_archetype: 'strong_grid' } as never)
      mount()
      const note = await screen.findByTestId('hub-results-no-verdict')
      expect(note.textContent).toContain('No reliability verdict for this site type.')
      expect(note.textContent).toContain('Check instead:')
      expect(screen.queryByTestId('hub-results-verdict')).toBeNull()
      expect(screen.queryByTestId('hub-results-set-goal')).toBeNull()
    })
  }
  it('a template with a verdict shows none of it', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue({ id: 'eh_datacenter', name: 'DC', recommended_archetype: 'weak_flexible' } as never)
    mount()
    await screen.findByTestId('hub-results-verdict')
    expect(screen.queryByTestId('hub-results-no-verdict')).toBeNull()
    expect(screen.getAllByText('Why this number')).toHaveLength(3)
  })
})

describe('no-verdict template: the shortfall tile agrees with the note (template-e2e 11)', () => {
  it('does not ask for a goal', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue({ id: 'eh_h2_hub', name: 'H2', recommended_archetype: 'strong_grid' } as never)
    mount()
    await screen.findByTestId('hub-results-no-verdict')
    expect(screen.queryByText(/Set an allowed shortfall in Goal/)).toBeNull()
    // One sentence, said once (the Verdict line); no figure, so no "Why this number".
    expect(screen.getAllByText(/No reliability verdict for this site type/)).toHaveLength(1)
    // Only the cost tile keeps its "Why this number"; the shortfall tile has no figure.
    expect(screen.getAllByText('Why this number')).toHaveLength(1)
  })
})

describe('ResultsCard horizon label (issue 28)', () => {
  it('says the cost runs to the year the study recorded, and holds still when the inputs change', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study({ ...REPORT, period_basis: 'npv_multi_period', valued_to_year: 2045 }))
    vi.spyOn(networkApi, 'getInvestmentPeriods').mockResolvedValue({ periods: [2027, 2030, 2035], weightings: [] } as never)
    mount()
    const tile = await screen.findByTestId('hub-results-cost')
    await waitFor(() => expect(tile.textContent).toContain('valued to 2045 over 18 years'))
  })
})

describe('ResultsCard critical load kept on (te-24)', () => {
  beforeEach(() => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(study(REPORT))
  })

  it('states the must-stay-on load once, in the Site card\'s own words', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(SITE_TEMPLATE)
    mount()
    const line = await screen.findByTestId('hub-results-critical')
    expect(line.textContent).toBe('Kept on in an outage: 3 MW in 2035 · 50 % of peak')
    expect(screen.getAllByTestId('hub-results-critical')).toHaveLength(1)
  })

  it('shows no line for a template without a must-stay-on share', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(DC_TEMPLATE)
    mount()
    await screen.findByTestId('hub-results-verdict')
    expect(screen.queryByTestId('hub-results-critical')).toBeNull()
  })

  it('shows no "0 %" line when the share is zero', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue({ ...SITE_TEMPLATE,
      params: { ...SITE_TEMPLATE.params!, critical_share: 0 } })
    mount()
    await screen.findByTestId('hub-results-verdict')
    expect(screen.queryByTestId('hub-results-critical')).toBeNull()
  })
})
