// guided-results 02b-2: "Why this number" under the study's figures. The responses are the real 02a route's,
// captured in __fixtures__/ehStudyWhy.json (see its README). GK B W6–W9 and UX 2 points 1–6 are named where tested.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { getEhStudyWhy, type EhStudyWhy, type WhyStep } from '../../../api/results'
import { resultsApi, type EhStudyAssumption } from '../../../api/simulation'
import { openFlowTargetInPlace, withReturnFrom } from '../../../layout/flowNavigation'
import { useUIStore } from '../../../store/uiStore'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from '../hubDesignStore'
import { kpiMoney } from '../plainWords'
import FIXTURE from './__fixtures__/ehStudyWhy.json'
import { editFor, failureWords, isoTime, recordedAndNow, shownState, WhyThisNumber } from './WhyThisNumber'

vi.mock('../../../api/results', () => ({ getEhStudyWhy: vi.fn() }))
vi.mock('../../../layout/flowNavigation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../layout/flowNavigation')>()
  return { ...actual, openFlowTargetInPlace: vi.fn(), withReturnFrom: vi.fn((jump: () => void) => jump()) }
})
vi.mock('../../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/simulation')>()
  return { ...actual, resultsApi: { ...actual.resultsApi, getEhStudy: vi.fn() } }
})

type Captured = { status: number; body: unknown }
const fx = FIXTURE as unknown as Record<string, Record<string, Captured>>
const body = (state: string, figure: string) => structuredClone(fx[state][figure].body) as EhStudyWhy
const httpError = (state: string, figure: string) => {
  const c = fx[state][figure]
  return Object.assign(new Error(`HTTP ${c.status}`), { response: { status: c.status, data: c.body } })
}

// Three of #1645's real rows (tests/fixtures/eh_study_run_assumptions.json): a goal row, a row joining two units,
// and a single unit's field.
const ROWS: EhStudyAssumption[] = [
  { key: 'mc_draws', label: 'Simulation runs per reliability check', value: 500, shown: '500 runs', source: 'default',
    reason: 'the engine default', section: 'scenarios', card: 'reliability', component: '', entity: '', field: 'mc_draws' },
  { key: 'outage_rate:Generator:gas', label: 'Outage rate of gas generating units (d1, d2)', value: 0.05,
    shown: '5 % forced outage rate on demand, repair 50 h', source: 'carrier_default',
    reason: 'the typical value for gas units, about NERC GADS 2019–2023 gas CT/CC fleet class average (forced outage rate on demand)',
    section: 'assets', card: 'reliability', component: 'Generator', entity: 'd1, d2', field: 'outage_rate_value' },
  { key: 'outage_basis:Generator', label: 'Outage rate basis of generating units (o1)', value: 'forced', shown: 'forced outage rate',
    source: 'default', reason: 'an outage rate without a basis is read as a forced outage rate', section: 'assets',
    card: 'reliability', component: '', entity: '', field: 'outage_rate_basis' },
]

const assumedStep = (node: string, label: string, state: WhyStep['state'] = 'fresh'): WhyStep => ({
  node: `input:eh_study/assumption/${node}`, kind: 'input', label, value: 1, unit: null, state, stamp: null,
  stamp_missing: [], via: { edge: 'derived_from', from: 'run:study', recorded: { label, recorded: 1 } }, broken: null,
})

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: 'hubDesign', uiMode: 'guided' })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true, step: 'results' })
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
    status: 'done', finished_at: Date.UTC(2026, 10, 18, 14, 5) / 1000, edited_since_study: false, assumptions: ROWS,
  })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount(figure: 'expected_shortfall' | 'design_cost' | 'verdict' = 'expected_shortfall', value?: number) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><WhyThisNumber figure={figure} value={value} /></QueryClientProvider>)
  return userEvent.setup()
}
const T = (figure = 'expected_shortfall') => `hub-why-${figure}`

async function openWith(why: EhStudyWhy | Error, figure: 'expected_shortfall' | 'design_cost' | 'verdict' = 'expected_shortfall') {
  if (why instanceof Error) vi.mocked(getEhStudyWhy).mockRejectedValue(why)
  else vi.mocked(getEhStudyWhy).mockResolvedValue(why)
  const user = mount(figure, figure === 'design_cost' ? 1234.5 : undefined)
  await user.click(screen.getByTestId(`${T(figure)}-toggle`))
  return user
}

describe('the disclosure (UX 2, 6)', () => {
  it('is a closed button that reads nothing until opened, then says it is busy in a status region', async () => {
    let resolve: (v: EhStudyWhy) => void = () => {}
    vi.mocked(getEhStudyWhy).mockReturnValue(new Promise(r => { resolve = r }))
    const user = mount()
    const toggle = screen.getByRole('button', { name: /Why this number/ })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(getEhStudyWhy).not.toHaveBeenCalled()
    await user.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(getEhStudyWhy).toHaveBeenCalledWith('Demo', 'expected_shortfall')
    const busy = screen.getByTestId(`${T()}-loading`)
    expect(busy.getAttribute('role')).toBe('status')
    expect(busy.getAttribute('aria-busy')).toBe('true')
    await act(async () => resolve(body('current', 'expected_shortfall')))
    expect(await screen.findByTestId(`${T()}-outcome`)).toBeTruthy()
    expect(screen.queryByTestId(`${T()}-loading`)).toBeNull()
  })

  it('names the study run and when it finished, as an ISO date', async () => {
    await openWith(body('current', 'expected_shortfall'))
    expect((await screen.findByTestId(`${T()}-source`)).textContent).toContain(
      'The expected shortfall comes from the reliability study, finished 2026-11-18 14:05 UTC.')
    expect(isoTime(null)).toBeNull()
  })
})

describe('the state (GK B W6, UX 2, 3)', () => {
  it('maps only the server\'s fresh to current; every other state, known or not, is never current', () => {
    // Kills the hand mutant "uncompared → current".
    expect(shownState('fresh')).toBe('current')
    expect(shownState('stale')).toBe('out_of_date')
    expect(shownState('uncompared')).toBe('not_compared')
    expect(shownState('busy')).toBe('not_compared')
    expect(shownState(null)).toBe('not_compared')
  })

  it('reads current with every run setting recorded and now', async () => {
    await openWith(body('current', 'expected_shortfall'))
    const outcome = await screen.findByTestId(`${T()}-outcome`)
    expect(outcome.getAttribute('data-state')).toBe('current')
    expect(outcome.textContent).toContain('Current')
    const rows = screen.getAllByTestId(`${T()}-setting`)
    const draws = rows.find(r => r.textContent?.startsWith('Draws')) as HTMLElement
    expect(within(draws).getAllByRole('cell').map(c => c.textContent)).toEqual(['500', '500'])
    expect(rows.map(r => within(r).getByRole('rowheader').textContent)).toEqual([
      'Reliability goal', 'Site type', 'Draws', 'Years modelled', 'Random seed', 'Convergence target', 'Network version'])
  })

  it('reads out of date, naming what changed, with recorded and now as two labelled values', async () => {
    await openWith(body('out_of_date', 'expected_shortfall'))
    const outcome = await screen.findByTestId(`${T()}-outcome`)
    expect(outcome.getAttribute('data-state')).toBe('out_of_date')
    expect(screen.getByTestId(`${T()}-changed`).textContent).toContain('Out of date Changed since the study: Draws.')
    expect(outcome.textContent).not.toContain('Current')
    const draws = screen.getAllByTestId(`${T()}-setting`).find(r => r.textContent?.startsWith('Draws')) as HTMLElement
    expect(draws.getAttribute('data-state')).toBe('out_of_date')
    expect(within(draws).getAllByRole('cell').map(c => c.textContent)).toEqual(['500', '1000'])
    expect(screen.getByRole('columnheader', { name: 'Recorded' })).toBeTruthy()
    expect(screen.getByRole('columnheader', { name: 'Now' })).toBeTruthy()
  })

  it('reads not compared in one line naming the inputs, never as current', async () => {
    await openWith(body('not_compared', 'expected_shortfall'))
    const outcome = await screen.findByTestId(`${T()}-outcome`)
    expect(outcome.getAttribute('data-state')).toBe('not_compared')
    expect(screen.getByTestId(`${T()}-unchecked`).textContent).toContain('Not compared: Assumed inputs could not be checked.')
    expect(outcome.textContent).not.toContain('Current')
    expect(screen.getAllByTestId(`${T()}-unchecked`)).toHaveLength(1)
  })

  it('shows a seed the study did not record as not recorded, not compared, never current (02b-1 W3)', async () => {
    const why = body('current', 'expected_shortfall')
    const seed = why.steps.find(s => s.node === 'input:eh_study/mc/seed') as WhyStep
    Object.assign(seed, { state: 'uncompared', value: null,
      via: { ...seed.via, recorded: { unrecorded: 'seed: not recorded' } } })
    why.outcome = { ...why.outcome, state: 'uncompared', uncompared: [seed.node] }
    await openWith(why)
    expect((await screen.findByTestId(`${T()}-outcome`)).getAttribute('data-state')).toBe('not_compared')
    expect(screen.getByTestId(`${T()}-unchecked`).textContent).toContain('Not compared: Random seed could not be checked.')
    const row = screen.getAllByTestId(`${T()}-setting`).find(r => r.textContent?.startsWith('Random seed')) as HTMLElement
    expect(within(row).getAllByRole('cell').map(c => c.textContent)).toEqual(['not recorded', 'not compared'])
    expect(recordedAndNow(seed)).toEqual({ recorded: 'not recorded', now: 'not compared' })
  })

  it('names the design cost\'s finance and prices as not compared, the cost in the tiles\' money', async () => {
    await openWith(body('current', 'design_cost'), 'design_cost')
    expect((await screen.findByTestId(`${T('design_cost')}-unchecked`)).textContent).toContain(
      "Not compared: Finance and currency settings, The site type's prices could not be checked.")
    // UX 2 (3): the disclosure's money is the tile's notation (kpiMoney); kpiMoney vs money() is issue 06.
    expect(screen.getByTestId(`${T('design_cost')}-source`).textContent).toContain(`The design cost (${kpiMoney(1234.5)})`)
  })

  it('writes no ids, engine values or route words', async () => {
    await openWith(body('out_of_date', 'expected_shortfall'))
    const text = (await screen.findByTestId(T())).textContent ?? ''
    for (const raw of ['input:', 'mc/', 'MC ', 'strong_grid', 'h_per_year', 'lole_ci', 'eh_study', 'DtC', 'pack'])
      expect(text).not.toContain(raw)
  })
})

describe('a failed read (GK B W7, UX 2, 6)', () => {
  it.each([
    ['not_run', 'verdict', 'No study has run in this session, so there is nothing to explain yet.'],
    ['running', 'verdict', 'The study is running. Open this again when it finishes.'],
    ['other_project', 'verdict', 'This is read from the open project. Open the project again, then retry.'],
  ] as const)('%s has its own words', async (state, figure, words) => {
    await openWith(httpError(state, figure), figure)
    expect((await screen.findByTestId(`${T(figure)}-failed`)).textContent).toContain(words)
  })

  it('any other failure says so and Retry reads again', async () => {
    const user = await openWith(new Error('Network Error'))
    expect((await screen.findByTestId(`${T()}-failed`)).textContent).toContain('The explanation could not be read.')
    expect(failureWords({ response: { status: 500, data: {} } })).toBe('The explanation could not be read.')
    vi.mocked(getEhStudyWhy).mockResolvedValue(body('current', 'expected_shortfall'))
    await user.click(screen.getByTestId(`${T()}-retry`))
    expect((await screen.findByTestId(`${T()}-outcome`)).getAttribute('data-state')).toBe('current')
    expect(getEhStudyWhy).toHaveBeenCalledTimes(2)
  })
})

describe('assumed defaults (UX 2, 1–2; GK B W9)', () => {
  function withRows(state: WhyStep['state'] = 'fresh') {
    const why = body('current', 'expected_shortfall')
    why.steps = [...why.steps.filter(s => !s.node.includes('/assumption/')),
      assumedStep('mc_draws', ROWS[0].label),
      assumedStep('outage_rate:Generator:gas/d1', `${ROWS[1].label}: d1`, state),
      assumedStep('outage_basis:Generator', ROWS[2].label)]
    return why
  }

  it('lists each with the shared Assumed chip and its source as visible text', async () => {
    await openWith(withRows())
    const rows = await screen.findAllByTestId(`${T()}-assumed-row`)
    expect(rows).toHaveLength(3)
    expect(rows[0].textContent).toContain('Simulation runs per reliability check: 500 runs')
    const chips = screen.getAllByTestId(`${T()}-chip`)
    expect(chips[0].getAttribute('data-state')).toBe('assumed')
    expect(chips[0].textContent).toContain('Assumed')
    expect(screen.getAllByTestId(`${T()}-chip-source`)[0].textContent).toContain('the engine default')
  })

  it('marks a changed default out of date on its own row', async () => {
    await openWith(withRows('stale'))
    const rows = await screen.findAllByTestId(`${T()}-assumed-row`)
    expect(rows[1].getAttribute('data-state')).toBe('out_of_date')
    expect(rows[1].textContent).toContain('Out of date')
  })

  it('Edit on a section with its own Guided card is a user move to it, with a way back', async () => {
    const user = await openWith(withRows())
    const edits = await screen.findAllByTestId(`${T()}-edit`)
    expect(edits.map(e => e.textContent)).toEqual(['Edit ›', 'Edit ›', 'Edit in Expert ›'])
    await user.click(edits[0])
    expect(withReturnFrom).toHaveBeenCalledTimes(1)
    expect(useHubDesignStore.getState().step).toBe('goal')
    expect(useHubDesignStore.getState().userMovedRail).toBe(true)
  })

  it('Edit on one unit\'s field opens it in place; a field naming no unit opens in Expert', async () => {
    const user = await openWith(withRows())
    const edits = await screen.findAllByTestId(`${T()}-edit`)
    await user.click(edits[1])
    expect(openFlowTargetInPlace).toHaveBeenLastCalledWith(
      { section: 'assets', panel: null, component: 'Generator', entity: 'd1', field: 'outage_rate_value' })
    await user.click(edits[2])
    expect(openFlowTargetInPlace).toHaveBeenLastCalledWith(
      { section: 'assets', panel: null, component: null, entity: null, field: 'outage_rate_basis' })
  })

  it('a row naming a section the flow does not know has no Edit, never a broken link', () => {
    expect(editFor({ ...ROWS[0], section: '5' }, null)).toBeNull()
    expect(editFor(ROWS[0], null)?.label).toBe('Edit ›')
  })

  it('a record with no assumptions lists none', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'done', edited_since_study: false })
    await openWith(withRows())
    await waitFor(() => expect(screen.getAllByTestId(`${T()}-assumed-row`)).toHaveLength(3))
    expect(screen.queryAllByTestId(`${T()}-edit`)).toHaveLength(0)
  })
})
