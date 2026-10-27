// CampusElectricalPanel (plan C6). The panel is thin: everything it shows is
// read from `GET /api/campus-electrical/{name}`, and every action is one
// request. These tests hold it to that:
// - the refusals render inline;
// - each button sends the right request;
// - each result table shows what the backend sent;
// - the "stale" warning appears when the backend says so.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { CampusRunJob, CampusState, InvestmentRow, MilpJobRecord } from '../api/campusElectrical'
import CampusElectricalPanel from './campusTestParts'
import OnePart, { CampusResultsPanel as ResultsOnly, CampusStudyPanel as StudyOnly } from './CampusElectricalPanel'
import { useCampusRunNotice, useCampusSettingsDraft } from '../store/campusSettingsDraft'

const store = vi.hoisted(() => ({ currentProject: 'Hub A' as string | null, requestRunSolve: (() => {}) as () => void,
  activeSlidePanel: null as string | null }))
vi.mock('../store/uiStore', () => ({
  useUIStore: (sel: (s: { currentProject: string | null; requestRunSolve: () => void; setSlidePanel: (p: string | null) => void }) => unknown) =>
    sel({ currentProject: store.currentProject, requestRunSolve: () => store.requestRunSolve(),
          setSlidePanel: (p: string | null) => { store.activeSlidePanel = p } } as never),
}))

const api = vi.hoisted(() => ({
  state: vi.fn(), draft: vi.fn(), save: vi.fn(), run: vi.fn(), gridCodes: vi.fn(),
  library: vi.fn(), saveLibrary: vi.fn(), resetLibrary: vi.fn(),
  startMilp: vi.fn(), milpStatus: vi.fn(), cancelMilp: vi.fn(),
  island: vi.fn(), islandReport: vi.fn(),
}))
const queue = vi.hoisted(() => ({ abort: vi.fn() }))
vi.mock('../api/solveQueue', async () => {
  const real = await vi.importActual<typeof import('../api/solveQueue')>('../api/solveQueue')
  return { ...real, solveQueueApi: { ...real.solveQueueApi, abort: queue.abort } }
})

const commercial = vi.hoisted(() => ({ getCommercial: vi.fn() }))
vi.mock('../api/commercial', async () => {
  const real = await vi.importActual<typeof import('../api/commercial')>('../api/commercial')
  return { ...real, commercialApi: { ...real.commercialApi, getCommercial: commercial.getCommercial } }
})

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

// The site type heads the inputs (W3b-R): the stored reliability goal's.
const goalApi = vi.hoisted(() => ({ getReliabilityGoal: vi.fn() }))
vi.mock('../api/flow', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/flow')>()),
  getReliabilityGoal: (...a: unknown[]) => goalApi.getReliabilityGoal(...a),
}))

function renderPanel(Panel: typeof CampusElectricalPanel = CampusElectricalPanel) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><Panel /></QueryClientProvider>)
}

beforeEach(() => {
  store.currentProject = 'Hub A'
  useCampusSettingsDraft.setState({ drafts: {} })
  useCampusRunNotice.setState({ notices: {} })
  vi.clearAllMocks()
  api.state.mockResolvedValue(empty)
  goalApi.getReliabilityGoal.mockResolvedValue({
    target_lole_h: { value: null, source: 'default' }, ens_cap_permyriad: { value: null, source: 'default' },
    mc_draws: { value: 500, source: 'default' }, site_archetype: { value: 'off_grid', source: 'stored' } })
  commercial.getCommercial.mockResolvedValue({ poc_link: 'grid_import' })
  api.gridCodes.mockResolvedValue({ shipped: {}, published: [], drafts: [], documents: [], extraction_available: false })
  api.milpStatus.mockResolvedValue(null)
  api.library.mockResolvedValue({ yaml: 'discount_rate: {value: 0.07, source: assumed}\n', is_default: true })
})
afterEach(() => cleanup())

describe('CampusElectricalPanel', () => {
  it('says what to do without a project', () => {
    store.currentProject = null
    renderPanel(OnePart)
    expect(screen.getByText(/open a solved hub project/i)).toBeTruthy()
  })

  it('renders a project of another kind inline, not as an error toast', async () => {
    api.state.mockRejectedValue({ response: { status: 409, data: { detail: 'is a planning_dynamics project' } } })
    renderPanel(OnePart)
    expect(await screen.findByText(/for capacity-expansion \(hub\) projects/i)).toBeTruthy()
  })

  // Economics and Investment send the user here to set the site connection (audit #13):
  // the editor must not depend on the campus read.
  it('still shows the Site connection editor when the campus read says the project is of another kind', async () => {
    api.state.mockRejectedValue({ response: { status: 409, data: { detail: 'is a planning_dynamics project' } } })
    renderPanel(OnePart)
    expect(await screen.findByText(/for capacity-expansion \(hub\) projects/i)).toBeTruthy()
    expect(await screen.findByTestId('ic-site-connection')).toBeTruthy()
  })

  it('still shows the Site connection editor while the campus read fails or loads', async () => {
    api.state.mockRejectedValue(new Error('boom'))
    renderPanel(OnePart)
    expect(await screen.findByTestId('ic-site-connection')).toBeTruthy()
  })

  it('heads the inputs with the site type, the one Guided\'s Site card sets (W3b-R)', async () => {
    renderPanel(OnePart)
    const select = await screen.findByTestId('campus-site-type') as HTMLSelectElement
    await waitFor(() => expect(select.value).toBe('off_grid'))
    expect(goalApi.getReliabilityGoal).toHaveBeenCalledWith('Hub A')
    // above the Site connection editor: the top of the panel
    const site = await screen.findByTestId('ic-site-connection')
    expect(select.compareDocumentPosition(site) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('does not show the Site connection editor on the study part', async () => {
    renderPanel(StudyOnly)
    await screen.findByText(/Grid study|Study settings|Draft the campus file/i)
    expect(screen.queryByTestId('ic-site-connection')).toBeNull()
  })

  it('drafts the campus from the project, and shows a refusal inline', async () => {
    api.draft.mockRejectedValueOnce({ response: { status: 422, data: { detail: "Project 'Hub A' is not solved — solve it and save, then draft" } } })
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Draft from project' }))
    // Guided has no header run button: the refusal is a state with the action (UX review N1).
    expect(await screen.findByTestId('campus-not-solved')).toBeTruthy()
    expect(screen.getByText('Not run yet')).toBeTruthy()
    const requestRunSolve = vi.fn()
    store.requestRunSolve = requestRunSolve
    await userEvent.click(screen.getByRole('button', { name: 'Run the solve' }))
    expect(requestRunSolve).toHaveBeenCalled()
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

  it('leaves the island report to Results › Control: the panel no longer reads it, a run only marks it out of date (I9b, W10 R3)', async () => {
    api.state.mockResolvedValue({ ...empty, campus_yaml: YAML })
    api.run.mockResolvedValue(sized())
    api.island.mockResolvedValue({ config: null, study: null })
    api.islandReport.mockResolvedValue({ caveat: '', tables: [], missing: [] })
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Run study' }))
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1))
    expect(api.islandReport).not.toHaveBeenCalled()
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
    useCampusSettingsDraft.setState({ drafts: {} })
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
    expect(await screen.findByLabelText('Equipment library')).toBeTruthy()
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

  it('shows a check whose limit the profile does not set as "no limit", never pass or fail', async () => {
    const state = sized()
    state.results!.compliance = [...state.results!.compliance, {
      check: 'pcc_power_factor', status_as_is: 'unknown', status_with_measures: 'unknown', value: NaN, limit: NaN,
      unit: '', worst_period: null, worst_hour: null, clause: '', source: 'assumed', detail: 'The grid code sets no limit for this.',
    } as never]
    api.state.mockResolvedValue(state)
    renderPanel()
    const row = await screen.findByTestId('compliance-pcc_power_factor')
    expect(row.textContent).toContain('Minimum power factor at the PCC')
    expect(row.textContent).toContain('no limit')
    expect(row.textContent).not.toContain('unknown')
    expect(row.textContent).toContain('The grid code sets no limit for this.')
    expect(row.textContent).not.toMatch(/pass|fail/)
    // it assumes nothing, so it carries no source tag
    expect(row.textContent).not.toContain('assumed')
  })

  it('prints a Q band with a negative edge as "−a to b", never with an en dash', async () => {
    const state = sized()
    state.results!.compliance = [...state.results!.compliance, {
      check: 'pcc_q_band', status_as_is: 'fail', status_with_measures: 'fail', value: 0.05, limit: 0.045,
      limit_min: -0.045, limit_max: 0.045, unit: 'Mvar', worst_period: 2030, worst_hour: 1, clause: 'generic clause',
      source: 'code', detail: 'worst at P 1.00 MW (0.500 pu); band \u22120.045 to 0.045 Mvar there',
    } as never]
    api.state.mockResolvedValue(state)
    renderPanel()
    await screen.findByTestId('compliance-pcc_q_band')
    expect(screen.getByTestId('compliance-limit-pcc_q_band').textContent?.trim()).toBe('\u22120.045 to 0.045 Mvar')
  })

  it('prints both negative Q-band edges with the same minus sign', async () => {
    const state = sized()
    state.results!.compliance = [...state.results!.compliance, {
      check: 'pcc_q_band', status_as_is: 'fail', status_with_measures: 'fail', value: -0.05, limit: -0.1,
      limit_min: -0.2, limit_max: -0.1, unit: 'Mvar', worst_period: 2030, worst_hour: 1, clause: 'generic clause',
      source: 'code', detail: 'Q band',
    } as never]
    api.state.mockResolvedValue(state)
    renderPanel()
    expect((await screen.findByTestId('compliance-limit-pcc_q_band')).textContent?.trim()).toBe('−0.2 to −0.1 Mvar')
  })

  it('never prints a unitless row\'s missing unit as "null"', async () => {
    const state = sized()
    // a CSV round trip turns the power factor's empty unit into null
    state.results!.compliance = [...state.results!.compliance, {
      check: 'pcc_power_factor', status_as_is: 'pass', status_with_measures: 'pass', value: 0.97, limit: 0.95,
      unit: null, worst_period: 2030, worst_hour: 1, clause: 'generic clause', source: 'code', detail: 'lagging',
    } as never]
    api.state.mockResolvedValue(state)
    renderPanel()
    const row = await screen.findByTestId('compliance-pcc_power_factor')
    expect(row.textContent).not.toContain('null')
    expect(screen.getByTestId('compliance-limit-pcc_power_factor').textContent?.trim()).toBe('0.950')
  })

  it('shows an unknown check that lacks an input as "not checked", not "no limit"', async () => {
    const state = sized()
    state.results!.compliance = [...state.results!.compliance, {
      check: 'pcc_q_band', status_as_is: 'unknown', status_with_measures: 'unknown', value: NaN, limit: NaN,
      unit: 'Mvar', worst_period: null, worst_hour: null, clause: 'generic clause', source: 'code',
      detail: 'no PCC active power for the studied hours',
    } as never]
    api.state.mockResolvedValue(state)
    renderPanel()
    const row = await screen.findByTestId('compliance-pcc_q_band')
    expect(row.textContent).toContain('not checked')
    expect(row.textContent).toContain('no PCC active power')
    expect(row.textContent).not.toContain('no limit')
  })

  it('names what needs an RMS study under the compliance table, and says nothing when nothing does', async () => {
    const state = sized()
    state.results!.reported_requirements = [
      { id: 'frt', requirement: 'Fault ride-through', value: 'generic', status: 'reported', detail: '', clause: '', source: 'code' },
      { id: 'fsm', requirement: 'Frequency sensitive mode', value: 'generic', status: 'reported', detail: '', clause: '', source: 'code' },
    ] as never
    api.state.mockResolvedValue(state)
    const { unmount } = renderPanel()
    expect((await screen.findByTestId('compliance-rms')).textContent)
      .toBe('Needs an RMS study (not checked here): Fault ride-through · Frequency sensitive mode')
    unmount()
    api.state.mockResolvedValue(sized())
    renderPanel()
    await screen.findByTestId('compliance-pcc_reactive')
    expect(screen.queryByTestId('compliance-rms')).toBeNull()
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
    expect(v.textContent).toContain('bought as 3 × TR 33 11 25 for N-1')
    const g = screen.getByTestId('trafo-verdict-GRID_IMPORT')
    expect(g.textContent).toMatch(/planned/i)
    expect(g.textContent).toContain('drafted rating undersized')
    expect(g.textContent).toContain('bought as 1 × TR 132 33 63')
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


describe('the run is a solve-queue job (E2E demo plan 2026-10-14, D1)', () => {
  const job = (over: Partial<CampusRunJob>): CampusRunJob => ({
    id: 'j1', kind: 'campus', status: 'running', position: null, error: null, enqueued_at: 1, started_at: Date.now() / 1000 - 75,
    finished_at: null, stage: 'island_checks', stage_done: 2, stage_total: 5, ...over,
  })

  it('says it is running, at which stage, asks the user to wait, and aborts through the queue', async () => {
    api.state.mockResolvedValue({ ...sized(), run_job: job({}) })
    queue.abort.mockResolvedValue({})
    renderPanel()
    const status = await screen.findByTestId('campus-run-status')
    expect(status.textContent).toMatch(/island checks: the frequency sweep at the island hours/)
    expect(status.textContent).toMatch(/step 3 of 5/)
    expect(status.textContent).toMatch(/please wait/i)
    expect(status.textContent).toMatch(/1:1[45]/)
    const runBtn = screen.getByRole('button', { name: /Running…/ }) as HTMLButtonElement
    expect(runBtn.disabled).toBe(true)
    await userEvent.click(within(status).getByRole('button', { name: /Abort/ }))
    await waitFor(() => expect(queue.abort).toHaveBeenCalledWith('j1'))
  })

  it('names the cuts as their own step', async () => {
    api.state.mockResolvedValue({ ...sized(), run_job: job({ stage: 'cuts', stage_done: 3, stage_total: 6 }) })
    renderPanel()
    const status = await screen.findByTestId('campus-run-status')
    expect(status.textContent).toMatch(/the frequency cuts/)
    expect(status.textContent).toMatch(/step 4 of 6/)
  })

  it('says a queued run is waiting, with its place', async () => {
    api.state.mockResolvedValue({ ...sized(), run_job: job({ status: 'queued', position: 2, started_at: null, stage: null }) })
    renderPanel()
    const status = await screen.findByTestId('campus-run-status')
    expect(status.textContent).toMatch(/waiting for the solve queue \(#2\)/)
  })

  it('says why the last run failed and that the results are older', async () => {
    api.state.mockResolvedValue({ ...sized(), run_job: job({ status: 'failed', error: 'HTTPException: no PCC' }) })
    renderPanel()
    const status = await screen.findByTestId('campus-run-status')
    expect(status.textContent).toMatch(/failed in .* after \d+:\d\d: HTTPException: no PCC/)
    expect(status.textContent).toMatch(/earlier run/)
    expect((screen.getByRole('button', { name: 'Run study' }) as HTMLButtonElement).disabled).toBe(false)
  })

  // #303 review: a GFM-settings job is a campus job, but not a run: the campus
  // results stay current whatever becomes of it.
  it.each<['running' | 'failed' | 'aborted', RegExp]>([
    ['running', /Recommending grid-forming settings.*campus results below are current/],
    ['failed', /grid-forming settings search failed: ContractError: no units.*campus run's results are unchanged/],
    ['aborted', /grid-forming settings search was stopped.*campus run's results are unchanged/],
  ])('a %s GFM-settings job says the campus results are unchanged', async (status, want) => {
    api.state.mockResolvedValue({ ...sized(), run_job: job({ status, stage: 'gfm_settings',
                                                             error: status === 'failed' ? 'ContractError: no units' : null }) })
    renderPanel()
    const el = await screen.findByTestId('campus-run-status')
    expect(el.textContent).toMatch(want)
    expect(el.textContent).not.toMatch(/earlier run|previous run/)
  })

  it('shows nothing for a finished run: its results are what shows', async () => {
    api.state.mockResolvedValue({ ...sized(), run_job: job({ status: 'completed', stage: 'done' }) })
    renderPanel()
    await screen.findByTestId('trafo-GRID_IMPORT')
    expect(screen.queryByTestId('campus-run-status')).toBeNull()
  })
})

describe('the study split over three panels (3 → 8 → 10)', () => {
  const YAML_ = 'campus:\n  pcc: x\n'
  beforeEach(() => { api.state.mockResolvedValue({ ...empty, campus_yaml: YAML_ }) })

  it('section 3 holds the grid code, power factor and PCC ownership; the settings and run are not there', async () => {
    renderPanel(OnePart)
    expect(await screen.findByLabelText('Grid code')).toBeTruthy()
    expect(screen.getByLabelText('Connection-agreement power factor')).toBeTruthy()
    expect(screen.getByLabelText('PCC switchgear owned by the grid operator (not costed)')).toBeTruthy()
    expect(screen.queryByLabelText('Hours per criterion')).toBeNull()
    expect(screen.queryByRole('button', { name: /Run study/ })).toBeNull()
  })

  it('section 8 holds the study settings and the run, not the grid code', async () => {
    renderPanel(StudyOnly)
    expect(await screen.findByLabelText('Hours per criterion')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Run study/ })).toBeTruthy()
    expect(screen.queryByLabelText('Grid code')).toBeNull()
  })

  it('a grid code chosen in section 3 is the one the run in section 8 sends', async () => {
    api.state.mockResolvedValue({ ...empty, campus_yaml: YAML_, profiles: { eu_rfg_dcc_ce: 'EU', other_code: 'Other' } })
    api.run.mockResolvedValue(undefined)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><OnePart /><StudyOnly /></QueryClientProvider>)
    const pick = await screen.findByLabelText('Grid code')
    await userEvent.selectOptions(pick, 'other_code')
    await userEvent.click(await screen.findByRole('button', { name: /Run study/ }))
    await waitFor(() => expect(api.run).toHaveBeenCalledWith('Hub A', expect.objectContaining({ profile: 'other_code' })))
  })

  it('results show only in section 10, and each part links to the next', async () => {
    api.state.mockResolvedValue({ ...empty, campus_yaml: YAML_, results: { ...(empty.results ?? {}) } as never })
    renderPanel(OnePart)
    fireEvent.click(await screen.findByTestId('campus-next-inputs'))
    expect(store.activeSlidePanel).toBe('campusStudy')
    cleanup()
    renderPanel(StudyOnly)
    fireEvent.click(await screen.findByTestId('campus-next-study'))
    expect(store.activeSlidePanel).toBe('campusResults')
  })

  it('section 10 without results says so and holds no inputs', async () => {
    renderPanel(ResultsOnly)
    expect(await screen.findByTestId('campus-no-results')).toBeTruthy()
    expect(screen.queryByLabelText('Hours per criterion')).toBeNull()
  })
})


it('words the bought transformer id and retains its raw identity in the tooltip', async () => {
  const state = sized()
  state.results!.investment = [{ need: 'transformer GRID_IMPORT', library_id: 'campus:TR_110_20_63',
    kind: 'transformer', units: 1, length_km: null, invest_period: 2030,
    capex_eur: 0, opex_eur_per_a: 0, annualised_eur_per_a: 0, existing: false, status: 'chosen', reason: null }]
  api.state.mockResolvedValue(state)
  renderPanel()
  const verdict = await screen.findByTestId('trafo-verdict-GRID_IMPORT')
  const asset = within(verdict).getByTitle('campus:TR_110_20_63')
  expect(asset.textContent).toBe('TR 110 20 63')
})
