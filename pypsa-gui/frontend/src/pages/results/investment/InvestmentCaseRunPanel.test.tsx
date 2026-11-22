// Template-e2e issue 22, slice S2: the investment case's run line, shared by
// Guided and Expert. GK B3 F1 (the checklist keyed on real codes; unknown →
// generic), F2 (one POST per double click), F3 (what it compares, in our words).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { financeApi, SolverInFlightError, StudyBusyError } from '../../../api/finance'
import type { InvestmentCaseReportPayload } from '../../../api/types'
import InvestmentCaseRunPanel from './InvestmentCaseRunPanel'
import { useInvestmentCaseRun } from './useInvestmentCaseRun'
import { BEFORE_RUN, blockerOf, comparedLine, needOf, NEED_ORDER } from './investmentCaseNeeds'
import table from './investmentCaseNeeds.json'

vi.mock('../../../api/finance', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/finance')>()
  return { ...actual, financeApi: { ...actual.financeApi, getInvestmentCase: vi.fn(),
    startInvestmentCase: vi.fn() } }
})
const openPanel = vi.fn()
vi.mock('../openPanel', () => ({ openPanel: (...a: unknown[]) => openPanel(...a) }))
const openSiteConnection = vi.fn()
vi.mock('../../economics/SiteConnectionLink', () => ({ openSiteConnection: () => openSiteConnection() }))

const api = vi.mocked(financeApi)

const reportWith = (payload: Record<string, unknown>) =>
  ({ sections: { project: { payload } } }) as unknown as InvestmentCaseReportPayload
const http = (status: number, detail: unknown) => Object.assign(new Error('http'), { response: { status, data: { detail } } })

function Panel({ mode, report }: { mode: 'guided' | 'expert'; report?: InvestmentCaseReportPayload | null }) {
  const run = useInvestmentCaseRun()
  return <InvestmentCaseRunPanel run={run} mode={mode} report={report === undefined ? null : report} />
}
function renderPanel(mode: 'guided' | 'expert' = 'guided', report?: InvestmentCaseReportPayload | null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><Panel mode={mode} report={report} /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  api.getInvestmentCase.mockResolvedValue(null)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('what the case compares (F3)', () => {
  it('analytic names the site without the new equipment, existing equipment not re-planned', () => {
    expect(comparedLine(reportWith({ has_counterfactual: true,
      counterfactual: { present: true, counterfactual_basis: 'analytic', basis: 'engine words' } })))
      .toBe("Compared with: the same site's energy bill and connection costs without the owner's new "
            + 'equipment. Existing equipment runs as it did; it is not re-planned.')
  })
  it('another or an unknown basis falls to the first sentence; the engine text never shows', () => {
    for (const basis of ['site_today', 'something_new', undefined]) {
      const line = comparedLine(reportWith({ counterfactual: { present: true, counterfactual_basis: basis,
                                                               basis: 'engine words' } }))
      expect(line).toBe("Compared with: the same site's energy bill and connection costs without the owner's new equipment.")
    }
    expect(comparedLine(reportWith({}))).toMatch(/^Compared with: /)
  })
  it('no counterfactual says it is not compared with anything', () => {
    const words = "Not compared with anything. This is the design's own cash flow, not a saving against today."
    expect(comparedLine(reportWith({ has_counterfactual: false }))).toBe(words)
    expect(comparedLine(reportWith({ counterfactual: { present: false, basis: 'x' } }))).toBe(words)
  })
})

describe('the needs (F1)', () => {
  it('maps a code family; an unknown or missing code has no need', () => {
    expect(needOf('not_solved')).toBe('solve')
    expect(needOf('stream_unmapped:ppa')).toBe('connection')
    expect(needOf('staged_cod_not_active_period:bess')).toBe('cod')
    expect(needOf('finance_inputs_missing')).toBe('finance')
    expect(needOf('template_not_annual:8760')).toBeNull()
    expect(needOf('never_heard_of_it')).toBeNull()
    expect(needOf(null)).toBeNull()
    for (const need of NEED_ORDER) expect((table[need] as string[]).length).toBeGreaterThan(0)
  })
  it('a busy 409 waits with the server sentence; a solve in flight waits too', () => {
    expect(blockerOf(new StudyBusyError('a frontier study is running'), null))
      .toEqual({ kind: 'wait', text: 'a frontier study is running' })
    expect(blockerOf(new SolverInFlightError('x'), null)?.kind).toBe('wait')
  })
  it('a coded 409 or 422 is a need, and only the server names it', () => {
    expect(blockerOf(new StudyBusyError('solve first', 'not_solved'), null))
      .toEqual({ kind: 'needs', needs: ['solve'] })
    expect(blockerOf(http(422, { code: 'finance_inputs_missing', message: 'PUT /api/simulation/finance' }), null))
      .toEqual({ kind: 'needs', needs: ['finance'] })
  })
  it('a missing or unknown code falls to "could not finish"', () => {
    expect(blockerOf(http(500, 'boom'), null)).toEqual({ kind: 'unknown', detail: null })
    expect(blockerOf(null, { status: 'refused' })).toEqual({ kind: 'unknown', detail: null })
    expect(blockerOf(null, { status: 'failed', error_code: 'internal_error', error: 'kaput' }))
      .toEqual({ kind: 'unknown', detail: 'kaput' })
    // A refusal is stored as "<code>: <detail>"; the code never reaches the words.
    expect(blockerOf(null, { status: 'refused', error_code: 'replacement_unknown_asset',
                             error: 'replacement_unknown_asset: bess' }))
      .toEqual({ kind: 'unknown', detail: 'bess' })
    expect(blockerOf(null, { status: 'refused', error_code: 'template_missing', error: 'template_missing' }))
      .toEqual({ kind: 'unknown', detail: null })
    expect(blockerOf(null, { status: 'done' })).toBeNull()
  })
})

describe('InvestmentCaseRunPanel', () => {
  it('before a run, says what it compares and offers the run', async () => {
    renderPanel()
    expect((await screen.findByTestId('ic-not-run')).textContent).toBe(BEFORE_RUN)
    expect(screen.getByRole('button', { name: 'Run the investment case' })).toBeTruthy()
  })

  it('a double click sends one POST (F2)', async () => {
    let resolve: (v: unknown) => void = () => {}
    api.startInvestmentCase.mockReturnValue(new Promise(r => { resolve = r }) as never)
    renderPanel()
    const button = await screen.findByRole('button', { name: 'Run the investment case' })
    fireEvent.click(button)
    fireEvent.click(button)
    await waitFor(() => expect(api.startInvestmentCase).toHaveBeenCalledTimes(1))
    fireEvent.click(button)
    await new Promise(r => setTimeout(r, 20))
    expect(api.startInvestmentCase).toHaveBeenCalledTimes(1)
    resolve({ status: 'running' })
    api.getInvestmentCase.mockResolvedValue({ status: 'running' })
    expect(await screen.findByRole('button', { name: 'Running the investment case…' })).toBeTruthy()
  })

  it('a refusal lists the need it names with its link; never the code or the 422 message', async () => {
    api.startInvestmentCase.mockRejectedValue(http(422, { code: 'finance_inputs_missing',
                                                         message: 'set the finance inputs first (PUT /api/simulation/finance)' }))
    const { container } = renderPanel('expert')
    fireEvent.click(await screen.findByRole('button', { name: 'Run the investment case' }))
    const needs = await screen.findByTestId('ic-needs')
    expect(needs.textContent).toMatch(/^The investment case needs:/)
    expect(screen.getAllByRole('listitem').map(li => li.getAttribute('data-testid'))).toEqual(['ic-need-finance'])
    expect(container.textContent).not.toMatch(/finance_inputs_missing|PUT \/api/)
    fireEvent.click(within(needs).getByRole('button', { name: 'Set the finance inputs ›' }))
    expect(openPanel).toHaveBeenCalledWith('commercial', 'economics')
  })

  it('an unsolved project: the solve link asks the header to run it', async () => {
    api.startInvestmentCase.mockRejectedValue(new StudyBusyError('solve the network first', 'not_solved'))
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Run the investment case' }))
    const item = await screen.findByTestId('ic-need-solve')
    const before = useUIStore.getState().runSolveRequest
    fireEvent.click(within(item).getByRole('button', { name: 'Run the solve' }))
    expect(useUIStore.getState().runSolveRequest).toBe(before + 1)
    expect(screen.queryByTestId('ic-blocked')).toBeNull()
  })

  it('a stored refusal names the connection and the start dates by their links', async () => {
    api.getInvestmentCase.mockResolvedValue({ status: 'refused', error_code: 'value_flows_not_configured',
                                              error: 'value_flows is not set' })
    renderPanel()
    const item = await screen.findByTestId('ic-need-connection')
    fireEvent.click(within(item).getByRole('button', { name: 'Check the site connection ›' }))
    expect(openSiteConnection).toHaveBeenCalled()
    expect(screen.getByTestId('ic-needs').textContent).not.toMatch(/value_flows/)
    cleanup()
    api.getInvestmentCase.mockResolvedValue({ status: 'refused', error_code: 'cod_missing',
                                              error: "cod_by_asset names no date for ['bess']" })
    renderPanel()
    expect((await screen.findByTestId('ic-need-cod')).textContent)
      .toBe('A start date for each piece of new equipment — Set the start dates ›')
    expect(screen.getByTestId('ic-needs').textContent).not.toMatch(/bess|cod_/)
  })

  it('an unknown code: both modes give the run its own reason, never the code (issue 03)', async () => {
    api.getInvestmentCase.mockResolvedValue({ status: 'refused', error_code: 'template_not_annual:8784',
                                              error: 'template_not_annual:8784: the template is not one year' })
    for (const mode of ['guided', 'expert'] as const) {
      renderPanel(mode)
      expect((await screen.findByTestId('ic-error')).textContent)
        .toBe('The investment case could not finish: the template is not one year.')
      cleanup()
    }
  })

  it('an unknown code with no reason: Guided points to Expert', async () => {
    api.getInvestmentCase.mockResolvedValue({ status: 'error', error_code: 'internal_error' })
    renderPanel('guided')
    expect((await screen.findByTestId('ic-error')).textContent)
      .toBe('The investment case could not finish. Open it in Expert for the details.')
    cleanup()
    renderPanel('expert')
    expect((await screen.findByTestId('ic-error')).textContent).toBe('The investment case could not finish.')
  })

  it('a busy 409 shows the server sentence once, and the button says it can be tried again', async () => {
    api.startInvestmentCase.mockRejectedValue(new StudyBusyError('a frontier study is running — wait for it to finish'))
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Run the investment case' }))
    expect((await screen.findByTestId('ic-blocked')).textContent)
      .toBe('A frontier study is running — wait for it to finish.')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })

  it('a need another read states joins the refusal in one ordered checklist', async () => {
    api.startInvestmentCase.mockRejectedValue(new StudyBusyError('solve the network first', 'not_solved'))
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    function Known() {
      const run = useInvestmentCaseRun()
      return <InvestmentCaseRunPanel run={run} mode="expert" report={null} known={['connection']} />
    }
    render(<QueryClientProvider client={qc}><Known /></QueryClientProvider>)
    // Before a run, the stated need is the panel's only line.
    await screen.findByTestId('ic-need-connection')
    expect(screen.queryByTestId('ic-not-run')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Run the investment case' }))
    await screen.findByTestId('ic-need-solve')
    expect(screen.getAllByRole('listitem').map(li => li.getAttribute('data-testid')))
      .toEqual(['ic-need-solve', 'ic-need-connection'])
  })

  it('a stated need stays listed while the panel waits or could not finish (GK B3 4)', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    function Known() {
      const run = useInvestmentCaseRun()
      return <InvestmentCaseRunPanel run={run} mode="guided" report={null} known={['connection']} />
    }
    api.startInvestmentCase.mockRejectedValue(new StudyBusyError('a frontier study is running — wait for it to finish'))
    render(<QueryClientProvider client={qc}><Known /></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Run the investment case' }))
    await screen.findByTestId('ic-blocked')
    expect(screen.getByTestId('ic-need-connection')).toBeTruthy()
    cleanup()
    api.startInvestmentCase.mockRejectedValue(http(500, 'boom'))
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Known /></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Run the investment case' }))
    await screen.findByTestId('ic-error')
    expect(screen.getByTestId('ic-need-connection')).toBeTruthy()
  })

  it('after a run, the compared line replaces "Not run yet"', async () => {
    renderPanel('guided', reportWith({ counterfactual: { present: true, counterfactual_basis: 'analytic' } }))
    expect((await screen.findByTestId('ic-compared')).textContent).toMatch(/not re-planned\.$/)
    expect(screen.queryByTestId('ic-not-run')).toBeNull()
  })
})
