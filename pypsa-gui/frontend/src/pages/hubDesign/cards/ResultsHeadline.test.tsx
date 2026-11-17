// The Guided Results headline (guided-results 01): reliability, NPV and LCOE
// with their own state, never 0 for a missing figure; one number in one place;
// one line when the figures do not all describe the current design (GK B G1).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { financeApi } from '../../../api/finance'
import { campusApi } from '../../../api/campusElectrical'
import { resultsApi, simulationApi, type HeadlineCard, type HeadlineKpis } from '../../../api/simulation'
import { useUIStore } from '../../../store/uiStore'
import { useProjectFlow } from '../../../hooks/useProjectFlow'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from '../hubDesignStore'
import { REPORT, review } from '../testFixtures'
import captured from '../../../test/fixtures/headlineKpis.json'
import { ResultsCard } from './ResultsCard'
import { chartFigure } from '../../../components/charts/chartTheme'
import { headlineFigure } from '../../results/headlineWords'
import { mixedSourceNote, ReliabilityBullet } from './ResultsHeadline'

vi.mock('../../../hooks/useProjectFlow', () => ({ useProjectFlow: vi.fn() }))
vi.mock('../../../api/finance', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/finance')>()
  return { ...actual, financeApi: { ...actual.financeApi, getInvestmentCase: vi.fn(), getReport: vi.fn() } }
})
vi.mock('../../../api/campusElectrical', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, state: vi.fn(), gfmSettings: vi.fn() } }
})
vi.mock('../../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/simulation')>()
  return {
    ...actual,
    simulationApi: { ...actual.simulationApi, getStatus: vi.fn() },
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: vi.fn(), getEhReview: vi.fn(), getEhReferenceDesign: vi.fn(), getEhTemplate: vi.fn(),
      getHeadlineKpis: vi.fn(), getProcessView: vi.fn(),
    },
  }
})

/** The captured `/results/headline_kpis` response (GK B G2): the LCOE card as the server sends it. */
const HEADLINE = captured as HeadlineKpis
const LCOE = HEADLINE.cards.find(c => c.id === 'lcoe') as HeadlineCard
const withLcoe = (over: Partial<HeadlineCard>): HeadlineKpis =>
  ({ ...HEADLINE, cards: HEADLINE.cards.map(c => (c.id === 'lcoe' ? { ...c, ...over } : c)) })

beforeEach(() => {
  vi.mocked(useProjectFlow).mockReturnValue({ flow: null } as unknown as ReturnType<typeof useProjectFlow>)
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: 'hubDesign', uiMode: 'guided',
    resultsTabRequest: null, ehReportRequest: false, flowReturn: null })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true, step: 'results' })
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'done', archetype: 'weak_flexible', report: REPORT } as never)
  vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
  vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(null)
  vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(null)
  vi.mocked(resultsApi.getProcessView).mockResolvedValue(null as never)
  vi.mocked(resultsApi.getHeadlineKpis).mockResolvedValue(HEADLINE)
  vi.mocked(financeApi.getInvestmentCase).mockResolvedValue({ status: 'done' } as never)
  vi.mocked(financeApi.getReport).mockResolvedValue({ npv_at_wacc: 4_200_000 } as never)
  vi.mocked(campusApi.state).mockRejectedValue(Object.assign(new Error('409'), { response: { status: 409 } }))
  vi.mocked(simulationApi.getStatus).mockResolvedValue(
    { running: false, status: 'idle', condition: null, objective: 1, solve_time: null, dispatch: 'optimal' } as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ResultsCard /></QueryClientProvider>)
  return userEvent.setup()
}

describe('mixedSourceNote (GK B G1)', () => {
  it('names the out-of-date figures when others are current', () => {
    expect(mixedSourceNote([
      { label: 'Expected shortfall', state: 'ready' },
      { label: 'What the design is worth', state: 'stale' },
      { label: 'LCOE (real)', state: 'stale' },
    ])).toBe('Not all figures describe the current design: What the design is worth and LCOE (real) are out of date.')
    expect(mixedSourceNote([{ label: 'A', state: 'stale' }, { label: 'B', state: 'ready' }]))
      .toBe('Not all figures describe the current design: A is out of date.')
  })
  it('says nothing when all are current, or all are out of date (the card banner says that once)', () => {
    expect(mixedSourceNote([{ label: 'A', state: 'ready' }, { label: 'B', state: 'not_run' }])).toBeNull()
    expect(mixedSourceNote([{ label: 'A', state: 'stale' }, { label: 'B', state: 'stale' }])).toBeNull()
  })
})

describe('Results headline', () => {
  it('shows reliability, the design cost, NPV and LCOE in one row', async () => {
    mount()
    const head = await screen.findByTestId('hub-results-headline')
    // The tile rounds as every KPI tile does; the goal arrives with the review.
    await waitFor(() => expect(within(head).getByTestId('hub-results-reliability').textContent).toContain('the goal is 3 h/yr'))
    expect(within(head).getByTestId('hub-results-reliability').textContent).toContain('12.4')
    expect(within(head).getByTestId('hub-results-cost')).toBeTruthy()
    expect((await within(head).findByTestId('hub-results-npv')).textContent).toMatch(/4\.2/)
    const lcoe = await within(head).findByTestId('hub-results-lcoe')
    // Rounded as Expert's strip rounds it (the shared tile), so the two modes read alike.
    expect(lcoe.textContent).toContain(chartFigure(headlineFigure(LCOE).value).split(' ')[0])
  })

  it('one number, one place: the chapter tiles leave out the figures the headline shows', async () => {
    mount()
    await screen.findByTestId('hub-results-headline')
    expect(await screen.findByTestId('hub-chapter-reliability-headlined')).toBeTruthy()
    expect(screen.getByTestId('hub-chapter-investment-headlined')).toBeTruthy()
    expect(screen.queryByTestId('hub-chapter-reliability-kpi')).toBeNull()
    expect(screen.queryByTestId('hub-chapter-investment-kpi')).toBeNull()
    expect(within(screen.getByTestId('hub-chapters')).queryByText(/12\.38/)).toBeNull()
    // The tiles the headline does not show keep their figure.
    expect(screen.getByTestId('hub-chapter-operation-kpi')).toBeTruthy()
  })

  it('before the study has results, the tiles keep every figure and there is no headline', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null as never)
    mount()
    expect(await screen.findByTestId('hub-chapter-reliability-kpi')).toBeTruthy()
    expect(screen.queryByTestId('hub-results-headline')).toBeNull()
  })

  it.each([
    ['not_run', 'The investment case has not run on this solve.'],
    ['running', 'The investment case is running.'],
    ['unavailable', 'This run cannot give this figure.'],
  ] as const)('an LCOE card in state %s shows its reason, never 0', async (state, reason) => {
    vi.mocked(resultsApi.getHeadlineKpis).mockResolvedValue(withLcoe({ state, value: null, reason }))
    mount()
    const lcoe = await screen.findByTestId('hub-results-lcoe')
    expect(lcoe.getAttribute('data-state')).toBe(state)
    expect(lcoe.querySelector('[data-kpi-value]')).toBeNull()
    expect(lcoe.textContent).toContain(reason)
    expect(lcoe.textContent).not.toMatch(/\b0(\.00)?\b/)
  })

  it('an NPV the case did not establish reads unavailable with its reason', async () => {
    vi.mocked(financeApi.getReport).mockResolvedValue({ npv_at_wacc: null } as never)
    mount()
    const npv = await screen.findByTestId('hub-results-npv')
    expect(npv.getAttribute('data-state')).toBe('unavailable')
    expect(npv.querySelector('[data-kpi-value]')).toBeNull()
  })

  it('a project type without an LCOE card shows no LCOE slot', async () => {
    vi.mocked(resultsApi.getHeadlineKpis).mockResolvedValue({ ...HEADLINE, cards: HEADLINE.cards.filter(c => c.id !== 'lcoe') })
    mount()
    await screen.findByTestId('hub-results-npv')
    expect(screen.queryByTestId('hub-results-lcoe')).toBeNull()
  })

  it('mixed revisions: a stale LCOE beside a current study says so once (G1)', async () => {
    vi.mocked(resultsApi.getHeadlineKpis).mockResolvedValue(withLcoe({ state: 'stale' }))
    mount()
    const note = await screen.findByTestId('hub-results-mixed')
    expect(note.textContent).toContain('is out of date')
    expect(note.textContent).toContain(LCOE ? 'LCOE' : '')
    expect(screen.getAllByTestId('hub-results-mixed')).toHaveLength(1)
  })

  it('each figure opens the Expert tab that holds it, with a way back', async () => {
    const user = mount()
    await user.click(await screen.findByTestId('hub-results-lcoe-open'))
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().resultsTabRequest).toBe('investment')
    expect(useUIStore.getState().flowReturn).not.toBeNull()
  })
})

describe('ReliabilityBullet', () => {
  it('puts the numbers in the title and marks the goal and the interval', () => {
    render(<ReliabilityBullet lole={12.38} target={3} ci={[10, 14.5]} stale={false} />)
    const frame = screen.getByTestId('hub-results-reliability-chart')
    expect(frame.textContent).toContain('Expected shortfall 12.4 h/yr, goal 3 h/yr')
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('likely between 10 h/yr and 14.5 h/yr')
    expect(screen.getByTestId('hub-results-reliability-goal')).toBeTruthy()
    expect(screen.getByTestId('hub-results-reliability-bar').className).toContain('bg-danger')
  })
  it('with no goal: the bar alone, no pass or fail colour', () => {
    render(<ReliabilityBullet lole={0.4} target={null} ci={null} stale={false} />)
    expect(screen.getByTestId('hub-results-reliability-chart').textContent).toContain('no goal set')
    expect(screen.queryByTestId('hub-results-reliability-goal')).toBeNull()
    expect(screen.getByTestId('hub-results-reliability-bar').className).toContain('bg-accent')
  })
  it('out of date: the source line carries the one mark', () => {
    render(<ReliabilityBullet lole={0.4} target={1} ci={null} stale />)
    expect(screen.getByTestId('hub-results-reliability-chart').textContent).toContain('Out of date')
  })
})
