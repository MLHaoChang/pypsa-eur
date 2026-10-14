// The frequency cuts in the island section (campus island plan, I7; owner Q6).
// It holds the section to the backend's contract:
// - the limit is stated: bridge-energy failures get no cut;
// - each proposed cut shows what it bounds, by how much, and its estimate
//   labelled as one; a cut the hub cannot take shows why, and no estimate;
// - Apply starts the loop, the job is polled while it runs, and the cuts and
//   the report are read again when it ends; Cancel asks it to stop;
// - the trials and the applied cuts are listed, and cuts can be taken back.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { CutJobRecord, IslandCuts, ProposedCut } from '../api/campusElectrical'
import CampusIslandCutsSection from './CampusIslandCutsSection'

const api = vi.hoisted(() => ({
  islandCuts: vi.fn(), applyIslandCuts: vi.fn(), islandCutsStatus: vi.fn(), cancelIslandCuts: vi.fn(),
  removeIslandCut: vi.fn(), clearIslandCuts: vi.fn(), islandReport: vi.fn(),
}))
vi.mock('../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../api/campusElectrical')>('../api/campusElectrical')
  return { ...real, campusApi: api }
})

const LIMIT = 'Bridge-energy and pickup failures (E4, ups_bridged) get no cut: …'
const cut = (over: Partial<ProposedCut> = {}): ProposedCut => ({
  period: 2030, hour: 3, scenario: 'seamless', event: 'E1', kind: 'inertia', required: 25, current: 12.5,
  delta: 12.5, unit: 'MW·s', binding: 'transition RoCoF 1.2 Hz/s', derivation: 'rocof: f0·ΔP/RoCoF_max',
  heuristic: true, feasible: true, note: null, applicable: true, reason: '', candidate: 'bess',
  added_mw: 10, estimate: 1000, label: 'estimate (primal): …', ...over,
})
const CUTS: IslandCuts = {
  limit: LIMIT, max_trials: 3,
  proposed: [cut(), cut({ hour: 5, kind: 'droop_gain', required: 'inf', feasible: false, applicable: false,
                          reason: 'collapse: no grid-forming unit', candidate: null, added_mw: null, estimate: null })],
  applied: [{ period: 2030, hour: 1, scenario: 'seamless', kind: 'headroom_up', required: 12 }],
  trials: [{ trial: 1, added: [{ period: 2030, hour: 1, scenario: 'seamless', kind: 'headroom_up', required: 12 }],
             objective: 2000, verdict: 'fails at 1 hour' }],
}
const running: CutJobRecord = { state: 'running', started_at: 1, finished_at: null, trials: 1, max_trials: 3,
                                stopped: null, error: null, message: 'applying the cuts' }

let client: QueryClient
function renderSection() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><CampusIslandCutsSection name="Hub A" /></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  api.islandCuts.mockResolvedValue(CUTS)
  api.islandCutsStatus.mockResolvedValue(null)
  api.islandReport.mockResolvedValue({ caveat: '', tables: [], missing: [] })
  api.removeIslandCut.mockResolvedValue(CUTS)
  api.clearIslandCuts.mockResolvedValue({ ...CUTS, applied: [], trials: [] })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => cleanup())

describe('frequency cuts: the preview', () => {
  it('states the limit', async () => {
    renderSection()
    expect((await screen.findByTestId('cuts-limit')).textContent).toContain('get no cut')
  })

  it('shows each cut with its estimate labelled, and why one has no row', async () => {
    renderSection()
    const rows = (await screen.findByTestId('cuts-proposed')).querySelectorAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toMatch(/inertia/)
    expect(rows[0].textContent).toMatch(/25/)
    expect(rows[0].textContent).toMatch(/bess/)
    expect(rows[0].textContent).toMatch(/1[,.\s]?000/)
    expect(rows[0].textContent).toMatch(/heuristic/i)
    expect(rows[0].getAttribute('data-applicable')).toBe('yes')
    expect(rows[1].getAttribute('data-applicable')).toBe('no')
    expect(rows[1].textContent).toContain('collapse: no grid-forming unit')
    expect(screen.getByTestId('cuts-estimate-note').textContent).toMatch(/estimate/i)
  })

  it('cannot apply when no proposed cut is applicable', async () => {
    api.islandCuts.mockResolvedValue({ ...CUTS, proposed: [CUTS.proposed[1]] })
    renderSection()
    await screen.findByTestId('cuts-proposed')
    expect((screen.getByRole('button', { name: /Apply cuts/ }) as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('frequency cuts: the loop', () => {
  it('starts the loop, polls it, and reads the cuts and the report again when it ends', async () => {
    api.applyIslandCuts.mockResolvedValue(running)
    api.islandCutsStatus.mockResolvedValueOnce(null).mockResolvedValueOnce(running)
      .mockResolvedValue({ ...running, state: 'done', stopped: 'all pass', finished_at: 2 })
    renderSection()
    const invalidated = vi.spyOn(client, 'invalidateQueries')
    const user = userEvent.setup()
    await screen.findByTestId('cuts-proposed')
    await user.click(screen.getByRole('button', { name: /Apply cuts/ }))
    await waitFor(() => expect(api.applyIslandCuts).toHaveBeenCalledWith('Hub A'))
    expect((await screen.findByTestId('cuts-job')).textContent).toMatch(/trial 1 of 3/)
    await waitFor(() => expect(screen.getByTestId('cuts-job').textContent).toMatch(/all pass/), { timeout: 8000 })
    await waitFor(() => expect(api.islandCuts.mock.calls.length).toBeGreaterThan(1))
    // the report and the campus state are mounted elsewhere in the panel: invalidated here
    const keys = invalidated.mock.calls.map(c => JSON.stringify(c[0]?.queryKey))
    expect(keys).toContain(JSON.stringify(['campusElectrical', 'islandReport', 'Hub A']))
    expect(keys).toContain(JSON.stringify(['campusElectrical', 'state', 'Hub A']))
  }, 15000)

  it('cancels a running loop', async () => {
    api.islandCutsStatus.mockResolvedValue(running)
    api.cancelIslandCuts.mockResolvedValue(running)
    renderSection()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /Cancel/ }))
    await waitFor(() => expect(api.cancelIslandCuts).toHaveBeenCalledWith('Hub A'))
  })

  it('shows a refusal inline', async () => {
    api.applyIslandCuts.mockRejectedValue({ response: { status: 422, data: { detail: 'island constraints are off' } } })
    renderSection()
    const user = userEvent.setup()
    await screen.findByTestId('cuts-proposed')
    await user.click(screen.getByRole('button', { name: /Apply cuts/ }))
    expect((await screen.findByTestId('cuts-refusal')).textContent).toContain('island constraints are off')
  })
})

describe('frequency cuts: the record', () => {
  it('lists the trials', async () => {
    renderSection()
    const row = (await screen.findByTestId('cuts-trials')).querySelector('tbody tr') as HTMLElement
    expect(row.textContent).toMatch(/1/)
    expect(row.textContent).toMatch(/headroom_up/)
    expect(row.textContent).toMatch(/2[,.\s]?000/)
    expect(row.textContent).toContain('fails at 1 hour')
  })

  it('takes one cut back, or all of them', async () => {
    renderSection()
    const user = userEvent.setup()
    const applied = await screen.findByTestId('cuts-applied')
    await user.click(within(applied).getByRole('button', { name: /Remove/ }))
    await waitFor(() => expect(api.removeIslandCut).toHaveBeenCalledWith('Hub A',
      { period: 2030, hour: 1, kind: 'headroom_up' }))
    await user.click(screen.getByRole('button', { name: /Clear all cuts/ }))
    await waitFor(() => expect(api.clearIslandCuts).toHaveBeenCalledWith('Hub A'))
  })
})
