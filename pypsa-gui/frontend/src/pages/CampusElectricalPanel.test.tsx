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
}))
vi.mock('../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../api/campusElectrical')>('../api/campusElectrical')
  return { ...real, campusApi: api }
})

const YAML = 'campus:\n  pcc: {bus: GRID}\n'

const empty: CampusState = {
  campus_yaml: null, skipped: [], profiles: { eu_rfg_dcc_ce: 'EU RfG (2016/631) and DCC (2016/1388), Continental Europe' },
  settings: null, results: null, stale: false, hub_cost: null, hub_cost_reason: 'no study has run yet',
}

const sized = (): CampusState => ({
  ...empty,
  campus_yaml: YAML,
  skipped: ['storage unit bess_new: built at 0 MW'],
  settings: { k: 3, pf: 0.95, profile: 'eu_rfg_dcc_ce', margin: 0.2, n_minus_1: true, invest: false,
              pcc_switchgear_by_operator: false },
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
      invest: true, pcc_switchgear_by_operator: false,
    }))
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
        pcc_switchgear_by_operator: false,
      }))
      const line = await screen.findByTestId('milp-progress')
      await waitFor(() => expect(line.textContent).toContain('Iteration 2 of 20 · best €80,000/a vs least cost €95,000/a'))
      expect((screen.getByRole('button', { name: MILP }) as HTMLButtonElement).disabled).toBe(true)
      expect((screen.getByRole('button', { name: 'Run study' }) as HTMLButtonElement).disabled).toBe(true)
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
