import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import {
  FrontierPanel, kneeMessage,
  type FrontierPayload, type FrontierRow,
} from './FrontierPanel'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getFrontier: vi.fn(), startFrontier: vi.fn(), abortFrontier: vi.fn(),
    },
  }
})

const row = (target: number, ens: number, cost: number): FrontierRow => ({
  target_permyriad: target, status: 'ok',
  point: {
    cap_mwh: ens, achieved_ens_mwh: ens, achieved_shed_hours: 24,
    total_system_cost_eur: cost, engine: 'lp_proxy',
    fidelity: 'deterministic_scenario',
  },
})

describe('kneeMessage', () => {
  // Steps: 30->20 MWh costs 100 (10 EUR/MWh avoided); 20->10 costs 900 (90).
  const rows = [row(300, 30, 1000), row(150, 20, 1100), row(80, 10, 2000)]

  it('names the target where tightening stops paying for itself', () => {
    const m = kneeMessage(1, rows, 50)
    expect(m).toMatch(/Economic knee at 150‱/)
    expect(m).toMatch(/90/)          // EUR/MWh avoided on that step
  })

  it('distinguishes "already past the optimum" from a knee in the middle', () => {
    // knee at index 0 means even the LOOSEST target swept is uneconomic —
    // reading that as "tighten to here" would be exactly backwards.
    const m = kneeMessage(0, rows, 5)
    expect(m).toMatch(/already past the economic optimum/i)
    expect(m).toMatch(/sweep looser targets/i)
    expect(m).not.toMatch(/Economic knee at/)
  })

  it('says the knee is outside the range rather than inventing one', () => {
    const m = kneeMessage(null, rows, 5000)
    expect(m).toMatch(/No economic knee inside the swept range/i)
    expect(m).toMatch(/sweep tighter/i)
  })

  it('stays silent when there is not enough of a curve to read', () => {
    expect(kneeMessage(null, [row(300, 30, 1000)], 50)).toBeNull()
    expect(kneeMessage(null, [], 50)).toBeNull()
  })

  it('ignores unreachable points when counting usable ones', () => {
    const withFailure: FrontierRow[] = [
      row(300, 30, 1000),
      { target_permyriad: 1, status: 'infeasible', point: null },
    ]
    // only one usable point -> nothing to say
    expect(kneeMessage(null, withFailure, 50)).toBeNull()
  })
})


// ── Phase 12e: the abort, and what a stopped sweep says on screen ──────────
//
// Shipped-code review, finding 14: the abort button and the states it produces
// had no frontend test. This file tested only `kneeMessage` — a pure helper —
// so nothing here rendered the panel at all.

const DONE: FrontierPayload = {
  status: 'done',
  points: [row(300, 30, 1000), row(150, 20, 1100), row(80, 10, 2000)],
  error: null, warning: null, knee: 1, voll_eur_per_mwh: 50,
  base_restored: true, base_restore_status: 'ok',
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}><FrontierPanel /></QueryClientProvider>)
}

/** Ships collapsed, like McPanel; open it before asserting. */
async function openPanel() {
  const user = userEvent.setup()
  renderPanel()
  await user.click(screen.getByRole('button', { name: /cost vs availability/i }))
  return user
}

describe('FrontierPanel — aborting a sweep', () => {
  afterEach(() => { cleanup(); vi.clearAllMocks() })

  // ★ Bite: drop the `running &&` guard, or wire `onClick` to the run mutation.
  it('offers Abort only while the sweep is running, and calls the abort route', async () => {
    vi.mocked(resultsApi.getFrontier).mockResolvedValue(DONE as never)
    renderPanel()
    await userEvent.setup().click(screen.getByRole('button', { name: /cost vs availability/i }))
    await waitFor(() => expect(resultsApi.getFrontier).toHaveBeenCalled())
    expect(screen.queryByTestId('frontier-abort')).toBeNull()
    cleanup()

    vi.mocked(resultsApi.getFrontier).mockResolvedValue(
      { ...DONE, status: 'running', points: [] } as never)
    vi.mocked(resultsApi.abortFrontier).mockResolvedValue(undefined as never)
    const user = await openPanel()
    await user.click(await screen.findByTestId('frontier-abort'))
    await waitFor(() => expect(resultsApi.abortFrontier).toHaveBeenCalledTimes(1))
  })

  // ★ Bite: render nothing for `status: "aborted"`. A stopped sweep then reads
  // as the whole frontier, and its knee — the knee of the targets that HAPPENED
  // to be swept — reads as the economic optimum of the full curve.
  it('says a stopped sweep is stopped, and says it only then', async () => {
    vi.mocked(resultsApi.getFrontier).mockResolvedValue(DONE as never)
    renderPanel()
    await userEvent.setup().click(screen.getByRole('button', { name: /cost vs availability/i }))
    await waitFor(() => expect(resultsApi.getFrontier).toHaveBeenCalled())
    expect(screen.queryByTestId('frontier-aborted')).toBeNull()
    cleanup()

    vi.mocked(resultsApi.getFrontier).mockResolvedValue(
      { ...DONE, status: 'aborted' } as never)
    await openPanel()
    const note = (await screen.findByTestId('frontier-aborted')).textContent ?? ''
    expect(note).toMatch(/stopped/i)
    expect(note).toMatch(/not the whole frontier/i)
  })

  // ★ Bite: render nothing for `base_restored === false`, or drop the word
  // from the copy. A sweep whose closing re-solve did not put the plan back
  // leaves the network on the last swept target while the chart describes
  // another one — and WHICH failure it was is what tells the user whether to
  // re-run or to change the config, so the solver's word has to be on screen.
  //
  // The panel does NOT decide what counts as restored. `base_restored` is the
  // backend's judgement, made on the termination condition, because
  // `SolverStatus.ok` also covers `time_limit` and `suboptimal` — a reading
  // this panel used to re-derive for itself and get wrong (review finding S1).
  it('warns, with the solver\'s word, when the plan was not put back', async () => {
    vi.mocked(resultsApi.getFrontier).mockResolvedValue(DONE as never)
    renderPanel()
    await userEvent.setup().click(screen.getByRole('button', { name: /cost vs availability/i }))
    await waitFor(() => expect(resultsApi.getFrontier).toHaveBeenCalled())
    expect(screen.queryByTestId('frontier-not-restored')).toBeNull()
    cleanup()

    vi.mocked(resultsApi.getFrontier).mockResolvedValue(
      { ...DONE, base_restored: false,
        base_restore_status: 'time_limit' } as never)
    await openPanel()
    const note = (await screen.findByTestId('frontier-not-restored'))
      .textContent ?? ''
    expect(note).toMatch(/time_limit/)
    expect(note).toMatch(/did not restore your plan/i)
  })
})
