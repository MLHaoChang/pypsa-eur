// CampusElectricalPanel (plan C6). The panel is thin: everything it shows is
// read from `GET /api/campus-electrical/{name}`, and every action is one
// request. These tests hold it to that:
// - the refusals render inline;
// - each button sends the right request;
// - each result table shows what the backend sent;
// - the "stale" warning appears when the backend says so.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { CampusState, InvestmentRow, MilpJobRecord } from '../api/campusElectrical'
import CampusElectricalPanel from './CampusElectricalPanel'

const store = vi.hoisted(() => ({ currentProject: 'Hub A' as string | null }))
vi.mock('../store/uiStore', () => ({
  useUIStore: (sel: (s: { currentProject: string | null }) => unknown) => sel({ currentProject: store.currentProject }),
}))

const api = vi.hoisted(() => ({
  state: vi.fn(), draft: vi.fn(), save: vi.fn(), run: vi.fn(), gridCodes: vi.fn(),
  library: vi.fn(), saveLibrary: vi.fn(), resetLibrary: vi.fn(),
  startMilp: vi.fn(), milpStatus: vi.fn(), cancelMilp: vi.fn(),
  island: vi.fn(), islandReport: vi.fn(),
}))
vi.mock('../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../api/campusElectrical')>('../api/campusElectrical')
  return { ...real, campusApi: api }
})

const YAML = 'campus:\n  pcc: {bus: GRID}\n'
const PCC_TAPS = "PCC transformer taps held by the grid operator (at the campus file's position)"

const empty: CampusState = {
  campus_yaml: null, skipped: [], profiles: { eu_rfg_dcc_ce: 'EU RfG (2016/631) and DCC (2016/1388), Continental Europe' },
  settings: null, results: null, stale: false, hub_cost: null, hub_cost_reason: 'no study has run yet',
}

const sized = (): CampusState => ({
  ...empty,
  campus_yaml: YAML,
  skipped: ['storage unit bess_new: built at 0 MW'],
  settings: { k: 3, pf: 0.95, profile: 'eu_rfg_dcc_ce', margin: 0.2, n_minus_1: true, invest: false,
              pcc_switchgear_by_operator: false, pcc_tap_control: 'campus' },
  results: {
    selection: [{ period: 2030, hour: 158, reasons: ['max_consumption_mw', 'max_trafo_SITE_TRANSFO_mw'] }],
    transformers: [{
      group: 'GRID_IMPORT', units: 1, unit_rating_mva: 50, max_s_intact_mva: 43.15, max_s_n1_mva: 0,
      required_unit_mva: 51.78, recommended_unit_mva: 63, adequate: false, worst_period: 2030, worst_hour: 158,
      unconverged_hours: 0, margin: 0.2, n_minus_1: false,
    }],
    compensation: [
      { direction: 'capacitive', required_mvar: 3.4, recommended_mvar: 5, worst_period: 2030, worst_hour: 112, margin: 0.2 },
      { direction: 'inductive', required_mvar: 0, recommended_mvar: 0, worst_period: null, worst_hour: null, margin: 0.2 },
    ],
    short_circuit: [{ period: 2030, bus: 'IT_BUS', vn_kv: 11, ikss_max_ka: 23.41, ip_max_ka: 41.65, ikss_min_ka: 7.12,
                      rated_ka: 25, rating_source: 'datasheet', adequate: true, detail: 'within' }],
    compliance: [
      { check: 'pcc_reactive', status_as_is: 'fail', status_with_measures: 'pass', value: 16.75, limit: 13.15,
        unit: 'Mvar', worst_period: 2030, worst_hour: 158, clause: 'study connection agreement: power factor 0.95',
        source: 'assumed', detail: 'compensation recommended: 5 Mvar capacitive, 0 Mvar inductive' },
      { check: 'pcc_voltage', status_as_is: 'pass', status_with_measures: 'not_rechecked', value: 1.0, limit: 0.9,
        unit: 'pu', worst_period: 2030, worst_hour: 0, clause: 'DCC Annex II (first table)', source: 'code', detail: 'x' },
    ],
    requirement: { q_limit_mvar: 13.15, clause: 'study connection agreement: power factor 0.95', source: 'assumed',
                   profile: 'eu_rfg_dcc_ce', pf: 0.95, p_ref_mw: 40, p_ref_from: 'peak |import| over every hour' },
    investment: null, cost: null, compliance_invested: null, history: null, unresolved: null, scope: null,
  },
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><CampusElectricalPanel /></QueryClientProvider>)
}

beforeEach(() => {
  store.currentProject = 'Hub A'
  vi.clearAllMocks()
  api.state.mockResolvedValue(empty)
  api.gridCodes.mockResolvedValue({ shipped: {}, published: [], drafts: [], documents: [], extraction_available: false })
  api.milpStatus.mockResolvedValue(null)
  api.library.mockResolvedValue({ yaml: 'discount_rate: {value: 0.07, source: assumed}\n', is_default: true })
})
afterEach(() => cleanup())

describe('CampusElectricalPanel', () => {
  it('says what to do without a project', () => {
    store.currentProject = null
    renderPanel()
    expect(screen.getByText(/open a solved hub project/i)).toBeTruthy()
  })

  it('renders a project of another kind inline, not as an error toast', async () => {
    api.state.mockRejectedValue({ response: { status: 409, data: { detail: 'is a planning_dynamics project' } } })
    renderPanel()
    expect(await screen.findByText(/for capacity-expansion \(hub\) projects/i)).toBeTruthy()
  })

  it('drafts the campus from the project, and shows a refusal inline', async () => {
    api.draft.mockRejectedValueOnce({ response: { status: 422, data: { detail: "Project 'Hub A' is not solved — solve it and save, then draft" } } })
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Draft from project' }))
    expect(await screen.findByText(/not solved/)).toBeTruthy()
    api.draft.mockResolvedValueOnce({ campus_yaml: YAML, skipped: [] })
    api.state.mockResolvedValue({ ...empty, campus_yaml: YAML })
    await userEvent.click(screen.getByRole('button', { name: 'Draft from project' }))
    await waitFor(() => expect(api.draft).toHaveBeenLastCalledWith('Hub A', false))
    expect(await screen.findByLabelText('Campus file')).toBeTruthy()
  })

  it('saves the edited campus file and shows the field a refusal names', async () => {
    api.state.mockResolvedValue({ ...empty, campus_yaml: YAML })
    api.save.mockRejectedValueOnce({ response: { status: 422, data: { detail: 'pcc: sk_min_mva (9) is above sk_max_mva (5)' } } })
    renderPanel()
    const editor = await screen.findByLabelText('Campus file')
    await userEvent.type(editor, '# edited')
    await userEvent.click(screen.getByRole('button', { name: 'Save campus file' }))
    await waitFor(() => expect(api.save).toHaveBeenCalledWith('Hub A', `${YAML}# edited`))
    expect(await screen.findByText(/sk_min_mva/)).toBeTruthy()
  })

  it('drafting again asks before overwriting the edits', async () => {
    api.state.mockResolvedValue({ ...empty, campus_yaml: YAML })
    api.draft.mockResolvedValue({ campus_yaml: YAML, skipped: [] })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Draft again' }))
    expect(confirm).toHaveBeenCalled()
    await waitFor(() => expect(api.draft).toHaveBeenCalledWith('Hub A', true))
    confirm.mockRestore()
  })

  it('runs with the chosen settings', async () => {
    api.state.mockResolvedValue({ ...empty, campus_yaml: YAML })
    api.run.mockResolvedValue(sized())
    renderPanel()
    const pf = await screen.findByLabelText('Connection-agreement power factor')
    await userEvent.type(pf, '0.95')
    await userEvent.clear(screen.getByLabelText('Hours per criterion'))
    await userEvent.type(screen.getByLabelText('Hours per criterion'), '2')
    await userEvent.click(screen.getByRole('button', { name: 'Run study' }))
    await waitFor(() => expect(api.run).toHaveBeenCalledWith('Hub A', {
      k: 2, pf: 0.95, profile: 'eu_rfg_dcc_ce', margin: 0.2, n_minus_1: true,
      invest: true, pcc_switchgear_by_operator: false, pcc_tap_control: 'campus',
    }))
  })

  it('reads the island report again after a run, which writes the island-hours results (I9b)', async () => {
    api.state.mockResolvedValue({ ...empty, campus_yaml: YAML })
    api.run.mockResolvedValue(sized())
    api.island.mockResolvedValue({ config: null, study: null })
    api.islandReport.mockResolvedValue({ caveat: '', tables: [], missing: [] })
    renderPanel()
    await waitFor(() => expect(api.islandReport).toHaveBeenCalledTimes(1))
    await userEvent.click(await screen.findByRole('button', { name: 'Run study' }))
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(api.islandReport).toHaveBeenCalledTimes(2))
  })

  it('buys assets from the library by default, and sends the setting when it is switched off', async () => {
    api.state.mockResolvedValue({ ...empty, campus_yaml: YAML })
    api.run.mockResolvedValue(sized())
    renderPanel()
    const box = (await screen.findByLabelText('Buy assets from the library (least cost, AC-checked)')) as HTMLInputElement
    expect(box.checked).toBe(true)
    await userEvent.click(box)
    await userEvent.click(screen.getByRole('button', { name: 'Run study' }))
    await waitFor(() => expect(api.run).toHaveBeenCalledWith('Hub A', expect.objectContaining({ invest: false })))
  })

  it('leaves the PCC switchgear to the campus by default, and sends the operator setting when it is ticked', async () => {
    api.state.mockResolvedValue({ ...empty, campus_yaml: YAML })
    api.run.mockResolvedValue(sized())
    renderPanel()
    const box = (await screen.findByLabelText('PCC switchgear owned by the grid operator (not costed)')) as HTMLInputElement
    expect(box.checked).toBe(false)
    await userEvent.click(box)
    await userEvent.click(screen.getByRole('button', { name: 'Run study' }))
    await waitFor(() => expect(api.run).toHaveBeenCalledWith('Hub A', expect.objectContaining({
      invest: true, pcc_switchgear_by_operator: true,
    })))
  })

  it('lets the campus move the PCC transformer taps by default, and sends the operator setting when ticked', async () => {
    api.state.mockResolvedValue({ ...empty, campus_yaml: YAML })
    api.run.mockResolvedValue(sized())
    renderPanel()
    const box = (await screen.findByLabelText(PCC_TAPS)) as HTMLInputElement
    expect(box.checked).toBe(false)
    await userEvent.click(box)
    await userEvent.click(screen.getByRole('button', { name: 'Run study' }))
    await waitFor(() => expect(api.run).toHaveBeenCalledWith('Hub A', expect.objectContaining({
      invest: true, pcc_tap_control: 'grid_operator',
    })))
  })

  it('takes the PCC tap setting from the last run, and a run saved before it existed means the campus', async () => {
    api.state.mockResolvedValue({
      ...empty, campus_yaml: YAML,
      settings: { k: 3, pf: null, profile: 'eu_rfg_dcc_ce', margin: 0.2, n_minus_1: true, invest: true,
                  pcc_switchgear_by_operator: false, pcc_tap_control: 'grid_operator' },
    })
    const first = renderPanel()
    expect(((await screen.findByLabelText(PCC_TAPS)) as HTMLInputElement).checked).toBe(true)
    first.unmount()
    api.state.mockResolvedValue({
      ...empty, campus_yaml: YAML,
      settings: { k: 3, pf: null, profile: 'eu_rfg_dcc_ce', margin: 0.2, n_minus_1: true, invest: true,
                  pcc_switchgear_by_operator: false } as never,
    })
    renderPanel()
    expect(((await screen.findByLabelText(PCC_TAPS)) as HTMLInputElement).checked).toBe(false)
  })

  it('takes the checkboxes from the last run, and a run saved before they existed means the defaults', async () => {
    api.state.mockResolvedValue({
      ...empty, campus_yaml: YAML,
      settings: { k: 3, pf: null, profile: 'eu_rfg_dcc_ce', margin: 0.2, n_minus_1: true, invest: false, pcc_switchgear_by_operator: true },
    })
    const first = renderPanel()
    expect(((await screen.findByLabelText('Buy assets from the library (least cost, AC-checked)')) as HTMLInputElement).checked).toBe(false)
    expect((screen.getByLabelText('PCC switchgear owned by the grid operator (not costed)') as HTMLInputElement).checked).toBe(true)
    first.unmount()
    api.state.mockResolvedValue({
      ...empty, campus_yaml: YAML,
      settings: { k: 3, pf: null, profile: 'eu_rfg_dcc_ce', margin: 0.2, n_minus_1: true } as never,
    })
    renderPanel()
    expect(((await screen.findByLabelText('Buy assets from the library (least cost, AC-checked)')) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByLabelText('PCC switchgear owned by the grid operator (not costed)') as HTMLInputElement).checked).toBe(false)
  })

  it('shows the library section between the grid codes and the settings', async () => {
    api.state.mockResolvedValue({ ...empty, campus_yaml: YAML })
    renderPanel()
    expect(await screen.findByLabelText('Asset library')).toBeTruthy()
  })

  it('shows compliance as is and with measures, with the clause and its tag', async () => {
    api.state.mockResolvedValue(sized())
    renderPanel()
    const row = await screen.findByTestId('compliance-pcc_reactive')
    expect(row.textContent).toContain('fail')
    expect(row.textContent).toContain('pass')
    expect(row.textContent).toContain('16.75')
    expect(row.textContent).toContain('13.15')
    expect(row.textContent).toContain('power factor 0.95')
    expect(row.textContent).toContain('assumed')
    expect(screen.getByTestId('compliance-pcc_voltage').textContent).toContain('not re-checked')
  })

  it('labels a transformer group the investment buys as planned, not as a bare verdict', async () => {
    // A drafted rating can pass while the investment still buys the group
    // (a placeholder 1 x 100 MVA beside 3 x 25 MVA bought for N-1): the verdict
    // describes the drafted rating, so it is shown as context, not as the answer.
    const s = sized()
    const site = { ...s.results!.transformers[0], group: 'SITE_TRANSFO', unit_rating_mva: 100, adequate: true,
                   n_minus_1: true, recommended_unit_mva: 50 }
    const buy = (need: string, library_id: string, units: number): InvestmentRow => ({
      need, library_id, kind: 'transformer', units, length_km: null, invest_period: 2030, capex_eur: 1,
      opex_eur_per_a: 0, annualised_eur_per_a: 0, existing: false, status: 'chosen', reason: null,
      action: 'buy', purchase: 1, build_year: 2030,
    })
    api.state.mockResolvedValue({ ...s, results: { ...s.results!,
      transformers: [s.results!.transformers[0], site],
      investment: [buy('transformer SITE_TRANSFO', 'TR_33_11_25', 3), buy('transformer GRID_IMPORT', 'TR_132_33_63', 1)],
      cost: [], history: [], unresolved: [] } })
    renderPanel()
    const v = await screen.findByTestId('trafo-verdict-SITE_TRANSFO')
    expect(v.textContent).toMatch(/planned/i)
    expect(v.textContent).toContain('drafted rating adequate')
    expect(v.textContent).toContain('bought as 3 × TR_33_11_25 for N-1')
    const g = screen.getByTestId('trafo-verdict-GRID_IMPORT')
    expect(g.textContent).toMatch(/planned/i)
    expect(g.textContent).toContain('drafted rating undersized')
    expect(g.textContent).toContain('bought as 1 × TR_132_33_63')
    expect(g.textContent).not.toContain('N-1')
  })

  it('keeps the bare verdict when the run bought nothing', async () => {
    api.state.mockResolvedValue(sized())
    renderPanel()
    const v = await screen.findByTestId('trafo-verdict-GRID_IMPORT')
    expect(v.textContent).toBe('undersized')
  })

  it('shows the transformer and compensation recommendations', async () => {
    api.state.mockResolvedValue(sized())
    renderPanel()
    const tr = await screen.findByTestId('trafo-GRID_IMPORT')
    expect(tr.textContent).toContain('50')
    expect(tr.textContent).toContain('63')
    expect(tr.textContent).toContain('undersized')
    expect(screen.getByTestId('comp-capacitive').textContent).toContain('5')
    expect(screen.getByTestId('fault-IT_BUS-2030').textContent).toContain('23.41')
    expect(screen.getByText(/max_consumption_mw/)).toBeTruthy()
    expect(screen.getByText(/bess_new: built at 0 MW/)).toBeTruthy()
  })

  it('lets the compliance, transformer and short-circuit tables scroll sideways rather than clip', async () => {
    api.state.mockResolvedValue(sized())
    renderPanel()
    for (const id of ['compliance-pcc_reactive', 'trafo-GRID_IMPORT', 'fault-IT_BUS-2030']) {
      const wrap = (await screen.findByTestId(id)).closest('table')!.parentElement!
      expect(wrap.getAttribute('data-testid')).toBe('campus-table-scroll')
      expect(wrap.className).toContain('overflow-x-auto')
    }
    // the free-text column keeps a readable width: the table scrolls rather than squeezing it
    const cells = screen.getByTestId('compliance-pcc_reactive').querySelectorAll('td')
    expect(cells[cells.length - 1].className).toContain('min-w-[16rem]')
  })

  it('warns when the results are stale', async () => {
    api.state.mockResolvedValue({ ...sized(), stale: true })
    renderPanel()
    expect(await screen.findByText(/changed since this run/i)).toBeTruthy()
  })

  it('says plainly that this is a steady-state study', async () => {
    renderPanel()
    const intro = await screen.findByTestId('campus-intro')
    expect(within(intro).getByText(/steady-state/i)).toBeTruthy()
  })

  describe('a run that gets no answer (it timed out or the connection dropped)', () => {
    const TIMEOUT = { code: 'ECONNABORTED', message: 'timeout of 900000ms exceeded' }
    const asked = { k: 3, pf: 0.95, profile: 'eu_rfg_dcc_ce', margin: 0.2, n_minus_1: true, invest: false,
                    pcc_switchgear_by_operator: false, pcc_tap_control: 'campus' as const }

    it('re-reads the state and never shows the previous results as the new run while the backend runs it', async () => {
      api.state.mockResolvedValue(sized())
      api.run.mockRejectedValue(TIMEOUT)
      renderPanel()
      await screen.findByTestId('compliance-pcc_reactive')
      expect(screen.queryByTestId('campus-run-pending')).toBeNull()
      const reads = api.state.mock.calls.length
      api.state.mockResolvedValue({ ...sized(), run_in_progress: true })
      await userEvent.click(screen.getByRole('button', { name: 'Run study' }))
      await waitFor(() => expect(api.state.mock.calls.length).toBeGreaterThan(reads))
      expect((await screen.findByTestId('campus-run-lost')).textContent).toMatch(/no answer.*timeout of 900000ms exceeded/i)
      const pending = await screen.findByTestId('campus-run-pending')
      expect(pending.textContent).toMatch(/previous run/)
      expect((screen.getByRole('button', { name: 'Run study' }) as HTMLButtonElement).disabled).toBe(true)
      // the backend finishes: the next read has the new run, saved with the settings asked for
      const done = sized()
      done.results!.compliance[0] = { ...done.results!.compliance[0], value: 12.5 }
      api.state.mockResolvedValue({ ...done, settings: asked, run_in_progress: false })
      await waitFor(() => expect(screen.queryByTestId('campus-run-pending')).toBeNull(), { timeout: 5000 })
      expect(screen.getByTestId('compliance-pcc_reactive').textContent).toContain('12.5')
      expect(screen.queryByTestId('campus-run-lost')).toBeNull()
      expect(screen.getByTestId('campus-run-outcome').textContent).toMatch(/finished the run/)
    })

    it('says when the backend saved no run with the settings asked for', async () => {
      api.state.mockResolvedValue(sized())
      api.run.mockRejectedValue({ code: 'ERR_NETWORK', message: 'Network Error' })
      renderPanel()
      await screen.findByTestId('compliance-pcc_reactive')
      await userEvent.clear(screen.getByLabelText('Hours per criterion'))
      await userEvent.type(screen.getByLabelText('Hours per criterion'), '5')
      await userEvent.click(screen.getByRole('button', { name: 'Run study' }))
      // the backend is not running it (it failed, or never got it): the earlier run's settings stand
      const outcome = await screen.findByTestId('campus-run-outcome', {}, { timeout: 5000 })
      expect(outcome.textContent).toMatch(/no results for the run asked for/)
      expect(outcome.textContent).toMatch(/earlier run/)
    })

    it('keeps reading while the backend cannot be reached, until it answers', async () => {
      api.state.mockResolvedValue(sized())
      api.run.mockRejectedValue({ code: 'ERR_NETWORK', message: 'Network Error' })
      renderPanel()
      await screen.findByTestId('compliance-pcc_reactive')
      api.state.mockRejectedValue({ code: 'ERR_NETWORK', message: 'Network Error' })
      await userEvent.click(screen.getByRole('button', { name: 'Run study' }))
      expect(await screen.findByTestId('campus-run-lost')).toBeTruthy()
      const reads = api.state.mock.calls.length
      await waitFor(() => expect(api.state.mock.calls.length).toBeGreaterThan(reads + 1), { timeout: 5000 })
      expect(screen.getByTestId('campus-run-pending')).toBeTruthy()
      api.state.mockResolvedValue({ ...sized(), settings: asked })
      expect((await screen.findByTestId('campus-run-outcome', {}, { timeout: 5000 })).textContent).toMatch(/finished the run/)
    })

    it('keeps a refusal the backend answered as a refusal, without re-reading', async () => {
      api.state.mockResolvedValue(sized())
      api.run.mockRejectedValue({ code: 'ERR_BAD_REQUEST', response: { status: 422, data: { detail: 'k is too big' } } })
      renderPanel()
      await screen.findByTestId('compliance-pcc_reactive')
      const reads = api.state.mock.calls.length
      await userEvent.click(screen.getByRole('button', { name: 'Run study' }))
      expect(await screen.findByText('k is too big')).toBeTruthy()
      expect(screen.queryByTestId('campus-run-lost')).toBeNull()
      expect(api.state.mock.calls.length).toBe(reads)
    })

    it('shows the pending warning whenever the backend says a run is going on', async () => {
      api.state.mockResolvedValue({ ...sized(), run_in_progress: true })
      renderPanel()
      expect((await screen.findByTestId('campus-run-pending')).textContent).toMatch(/previous run/)
    })
  })

  describe('the joint optimisation (MILP) job', () => {
    const MILP = 'Joint optimisation (MILP, slow — minutes)'
    const bought: InvestmentRow = {
      need: 'transformer GRID_IMPORT', library_id: 'TR63', kind: 'transformer', units: 1, length_km: null,
      invest_period: 2030, capex_eur: 2e6, opex_eur_per_a: 3e4, annualised_eur_per_a: 95_000, existing: false,
      status: 'chosen', reason: null,
    }
    const leastCost = (): CampusState => {
      const s = sized()
      return { ...s, results: { ...s.results!, investment: [bought], cost: [], history: [], unresolved: [] } }
    }
    const record = (over: Partial<MilpJobRecord> = {}): MilpJobRecord => ({
      state: 'running', project: 'Hub A', started_at: 1, finished_at: null, iteration: 2, max_iter: 20,
      best_cost: 80_000, c8_cost: 95_000, stop: null, error: null, message: 'iteration 2 of 20', ...over,
    })

    it('is disabled until a least-cost run has bought assets, and says why', async () => {
      api.state.mockResolvedValue(sized())
      renderPanel()
      const btn = (await screen.findByRole('button', { name: MILP })) as HTMLButtonElement
      expect(btn.disabled).toBe(true)
      expect(screen.getByText(/needs a least-cost run first/)).toBeTruthy()
      expect(screen.getByText(/takes minutes/)).toBeTruthy()
    })

    it('starts with the run settings, then shows the iteration and the best cost against the least cost', async () => {
      api.state.mockResolvedValue(leastCost())
      api.startMilp.mockResolvedValue(record({ iteration: null, max_iter: null, best_cost: null,
                                               message: 'the least-cost run first' }))
      api.milpStatus.mockResolvedValue(null)
      renderPanel()
      const btn = (await screen.findByRole('button', { name: MILP })) as HTMLButtonElement
      expect(btn.disabled).toBe(false)
      api.milpStatus.mockResolvedValue(record())
      await userEvent.click(btn)
      await waitFor(() => expect(api.startMilp).toHaveBeenCalledWith('Hub A', {
        k: 3, pf: 0.95, profile: 'eu_rfg_dcc_ce', margin: 0.2, n_minus_1: true, invest: false,
        pcc_switchgear_by_operator: false, pcc_tap_control: 'campus',
      }))
      const line = await screen.findByTestId('milp-progress')
      await waitFor(() => expect(line.textContent).toContain('Iteration 2 of 20 · best €80,000/a vs least cost €95,000/a'))
      expect((screen.getByRole('button', { name: MILP }) as HTMLButtonElement).disabled).toBe(true)
      expect((screen.getByRole('button', { name: 'Run study' }) as HTMLButtonElement).disabled).toBe(true)
    })

    it('names the warm start rather than counting it as iteration 0', async () => {
      api.state.mockResolvedValue(leastCost())
      api.milpStatus.mockResolvedValue(record({ iteration: 0, best_cost: 95_000 }))
      renderPanel()
      const line = await screen.findByTestId('milp-progress')
      expect(line.textContent).toContain('Warm start solved, 0 of 20 iterations · best €95,000/a vs least cost €95,000/a')
      expect(line.textContent).not.toContain('Iteration 0')
    })

    it('says a finished result is stale once the least-cost run it came from has changed', async () => {
      const s = leastCost()
      for (const [state, stop] of [['done', 'converged'], ['cancelled', 'cancelled']] as const) {
        api.state.mockResolvedValue({ ...s, milp: { status: null, results: null, stale: true } })
        api.milpStatus.mockResolvedValue(record({ state, stop, finished_at: 2 }))
        const view = renderPanel()
        const line = await screen.findByTestId('milp-progress')
        await waitFor(() => expect(line.textContent).toMatch(/stale/))
        expect(line.textContent).not.toMatch(/shown under/)
        view.unmount()
      }
    })

    it('says where a fresh result is shown', async () => {
      api.state.mockResolvedValue({ ...leastCost(), milp: { status: null, results: null, stale: false } })
      api.milpStatus.mockResolvedValue(record({ state: 'done', stop: 'converged', finished_at: 2 }))
      renderPanel()
      expect((await screen.findByTestId('milp-progress')).textContent).toMatch(/shown under Joint optimisation/)
    })

    it('says what it is doing before the first iteration', async () => {
      api.state.mockResolvedValue(leastCost())
      api.milpStatus.mockResolvedValue(record({ iteration: null, max_iter: null, best_cost: null,
                                                message: 'the least-cost run first' }))
      renderPanel()
      expect((await screen.findByTestId('milp-progress')).textContent).toContain('the least-cost run first')
    })

    it('cancels through the API and says it is cancelling', async () => {
      api.state.mockResolvedValue(leastCost())
      api.milpStatus.mockResolvedValue(record())
      api.cancelMilp.mockResolvedValue({ state: 'running', cancelling: true })
      renderPanel()
      await screen.findByTestId('milp-progress')
      await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
      await waitFor(() => expect(api.cancelMilp).toHaveBeenCalledWith('Hub A'))
      expect(await screen.findByText(/cancelling…/)).toBeTruthy()
    })

    it('re-reads the state when the job ends, and says how it ended', async () => {
      api.state.mockResolvedValue(leastCost())
      api.startMilp.mockResolvedValue(record())
      renderPanel()
      const btn = await screen.findByRole('button', { name: MILP })
      api.milpStatus.mockResolvedValue(record())
      await userEvent.click(btn)
      await waitFor(() => expect(screen.getByTestId('milp-progress').textContent).toContain('Iteration 2 of 20'))
      const reads = api.state.mock.calls.length
      api.milpStatus.mockResolvedValue(record({ state: 'cancelled', stop: 'cancelled', finished_at: 2 }))
      // the next poll sees the job ended
      await waitFor(() => expect(screen.getByTestId('milp-progress').textContent).toMatch(/was cancelled/), { timeout: 4000 })
      await waitFor(() => expect(api.state.mock.calls.length).toBeGreaterThan(reads))
    })

    it('re-reads the job and the state when its start gets no answer: it may have started', async () => {
      api.state.mockResolvedValue(leastCost())
      api.milpStatus.mockResolvedValue(null)
      api.startMilp.mockRejectedValue({ code: 'ECONNABORTED', message: 'timeout of 900000ms exceeded' })
      renderPanel()
      const btn = await screen.findByRole('button', { name: MILP })
      const reads = { state: api.state.mock.calls.length, milp: api.milpStatus.mock.calls.length }
      api.milpStatus.mockResolvedValue(record({ iteration: null, max_iter: null, best_cost: null,
                                                message: 'the least-cost run first' }))
      await userEvent.click(btn)
      expect(await screen.findByText(/no answer from the backend to the start of the joint optimisation/i)).toBeTruthy()
      await waitFor(() => expect(api.milpStatus.mock.calls.length).toBeGreaterThan(reads.milp))
      await waitFor(() => expect(api.state.mock.calls.length).toBeGreaterThan(reads.state))
      expect((await screen.findByTestId('milp-progress')).textContent).toContain('the least-cost run first')
    })

    it('shows a failed job with its reason and a refused start inline', async () => {
      api.state.mockResolvedValue(leastCost())
      api.milpStatus.mockResolvedValue(record({ state: 'failed', error: 'the MILP solver gave up' }))
      api.startMilp.mockRejectedValue({ response: { status: 409, data: { detail: 'a joint optimisation (MILP) is already running' } } })
      renderPanel()
      expect((await screen.findByTestId('milp-progress')).textContent).toContain('failed: the MILP solver gave up')
      await userEvent.click(screen.getByRole('button', { name: MILP }))
      expect(await screen.findByText(/already running/)).toBeTruthy()
    })
  })
})
