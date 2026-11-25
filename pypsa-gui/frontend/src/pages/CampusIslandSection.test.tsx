// The island section of the campus electrical panel (campus island plan, I9a).
// It holds the section to the backend's contract:
// - the form edits the requirements and control modes in the sidecar it was
//   sent, and Save sends that whole sidecar; a 422 renders inline;
// - Run asks for the study and the island data is read again;
// - every report table (now shown in Results › Control › Island & ride-through, by
//   IslandReportView) shows its model and the screening caveat, and its assumed rows
//   are tagged; what is missing is said; the section itself points there (W10 R3).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { IslandConfig, IslandReport } from '../api/campusElectrical'
import CampusIslandSection, { IslandReportView } from './CampusIslandSection'
import { useUIStore } from '../store/uiStore'

const api = vi.hoisted(() => ({
  island: vi.fn(), saveIsland: vi.fn(), patchIsland: vi.fn(), runIslandStudy: vi.fn(), islandReport: vi.fn(),
}))
vi.mock('../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../api/campusElectrical')>('../api/campusElectrical')
  return { ...real, campusApi: api }
})
const net = vi.hoisted(() => ({
  getStorageUnits: vi.fn(), getGenerators: vi.fn(), getStores: vi.fn(), getLinks: vi.fn(),
}))
vi.mock('../api/network', async () => {
  const real = await vi.importActual<typeof import('../api/network')>('../api/network')
  return { ...real, networkApi: { ...real.networkApi, ...net } }
})

const a = (value: number) => ({ value, source: 'assumed' as const })
const CONFIG: IslandConfig = {
  units: {
    bess: { kind: 'bess', control: 'gfm_droop', pf_rated: a(0.95) },
    g: { kind: 'genset', governor: 'droop', H_s: a(1.5) },
  },
  requirements: {
    scenarios: ['ups_bridged'], sustained_h: a(24), bridge_s: a(600), genset_redundancy_n: a(1),
    linearised_uc: false, pickup_blocks: a(5),
  },
}
const CAVEAT = 'Screening, not a certificate: a steady-state hub LP.'
const REPORT: IslandReport = {
  caveat: CAVEAT,
  missing: ['campus run with island tables'],
  tables: [
    { id: 'requirements', group: 'inputs', title: 'Island requirements', model: 'island sidecar', caveat: CAVEAT,
      columns: ['field', 'value', 'source'],
      rows: [['bridge_s', 600, 'assumed'], ['sustained_h', 24, 'datasheet'], ['steady_island.f_max_hz', 51, 'datasheet']], assumed_rows: [0] },
    { id: 'costs', group: 'study', title: 'Cost of island capability', model: 'hub LP, plain LOPF (HiGHS)', caveat: CAVEAT,
      columns: ['scenario', 'status', 'objective', 'cost of island capability'],
      rows: [['ups_bridged', 'ok', 39810000, 2270000]], assumed_rows: [] },
    { id: 'static_flow', group: 'static', title: 'Island load flow at the island hours',
      model: 'steady-state island load flow (pandapower)', caveat: CAVEAT,
      columns: ['period', 'hour', 'viable', 'assumed'],
      rows: [[0, 3, true, 'g.droop_pct'], [0, 5, false, null]], assumed_rows: [0] },
    { id: 'freq_events', group: 'frequency', title: 'Frequency events at the island hours (E1–E6)',
      model: 'frequency screening', caveat: CAVEAT,
      columns: ['period', 'hour', 'event', 'ok'], rows: [[0, 3, 'E1', false]], assumed_rows: [] },
  ],
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><CampusIslandSection name="Hub A" /></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  api.island.mockResolvedValue({ config: CONFIG, study: null })
  api.islandReport.mockResolvedValue(REPORT)
  api.saveIsland.mockImplementation((_p: string, config: IslandConfig) => Promise.resolve({ config, study: null }))
  api.patchIsland.mockResolvedValue({ config: CONFIG, study: null })
  net.getStorageUnits.mockResolvedValue([{ name: 'bess' }])
  net.getGenerators.mockResolvedValue([{ name: 'g' }])
  net.getStores.mockResolvedValue([])
  net.getLinks.mockResolvedValue([])
  api.runIslandStudy.mockResolvedValue({})
})
afterEach(() => cleanup())

function renderReport(report: IslandReport = REPORT) {
  return render(<IslandReportView report={report} />)
}

describe('island section: the report', () => {
  it('shows every table with its model and the caveat', async () => {
    renderReport()
    for (const id of ['requirements', 'costs']) {
      const table = await screen.findByTestId(`island-table-${id}`)
      expect(within(table).getByTestId('island-caveat').textContent).toBe(CAVEAT)
      expect(within(table).getByTestId('island-model').textContent).toMatch(/^Model: /)
    }
    expect(within(screen.getByTestId('island-table-costs')).getByTestId('island-model').textContent)
      .toContain('hub LP')
  })

  it('starts every requirement row with a capital', async () => {
    renderReport()
    const table = await screen.findByTestId('island-table-requirements')
    const first = [...table.querySelectorAll('tbody tr')].map(r => r.querySelector('td')?.textContent ?? '')
    expect(first[2]).toBe('Steady island: highest frequency (Hz)')
    for (const t of first) expect(t[0]).toBe(t[0].toUpperCase())
  })

  it('tags the assumed rows and only those', async () => {
    renderReport()
    const table = await screen.findByTestId('island-table-requirements')
    const rows = table.querySelectorAll('tbody tr')
    expect(rows[0].getAttribute('data-assumed')).toBe('yes')
    expect(rows[0].textContent).toMatch(/assumed/i)
    expect(rows[1].getAttribute('data-assumed')).toBe('no')
  })

  it('heads each group once, in the order the report gives', async () => {
    renderReport()
    await screen.findByTestId('island-table-freq_events')
    const heads = screen.getAllByTestId('island-group').map(h => h.textContent)
    expect(heads).toEqual([
      'Inputs', 'Island study', 'Island steady state and short circuit', 'Island frequency',
    ])
    expect(within(screen.getByTestId('island-table-static_flow')).getByTestId('island-model').textContent)
      .toContain('pandapower')
    const rows = screen.getByTestId('island-table-static_flow').querySelectorAll('tbody tr')
    expect([...rows].map(r => r.getAttribute('data-assumed'))).toEqual(['yes', 'no'])
  })

  it('keeps a long cell to one line with the full text on hover, and scrolls a table in its own box', async () => {
    const long = Array.from({ length: 40 }, (_, i) => `unit_${i}.droop_pct`).join('; ')
    renderReport({ ...REPORT, tables: [{ ...REPORT.tables[2], rows: [[0, 3, true, long]] }] })
    const table = await screen.findByTestId('island-table-static_flow')
    const cell = within(table).getByTitle(long)
    expect(cell.textContent).toBe(long)
    expect(cell.className).toMatch(/truncate/)
    const box = table.querySelector('[data-testid="island-table-scroll"]') as HTMLElement
    expect(box.className).toMatch(/overflow-auto/)
    expect(box.className).toMatch(/max-h-/)
  })

  it('heads columns and names requirement fields in plain words, never as raw ids', async () => {
    renderReport()
    const heads = (id: string) => [...screen.getByTestId(`island-table-${id}`).querySelectorAll('th')].map(h => h.textContent)
    await screen.findByTestId('island-table-requirements')
    expect(heads('requirements')).toEqual(['Setting', 'Value', 'Source', ''])
    expect(heads('costs')).toContain('Cost of island capability (per year)')
    expect(heads('static_flow')).toEqual(['Period', 'Hour', 'Viable', 'Assumed inputs', ''])
    const first = screen.getByTestId('island-table-requirements').querySelector('tbody tr td')
    expect(first?.textContent).toBe('UPS bridge (s)')
    expect(screen.getByTestId('island-report').textContent).not.toMatch(/bridge_s|\(I\d/)
  })

  it('leaves out a table without rows and says so once', async () => {
    renderReport({ ...REPORT, tables: [...REPORT.tables, { ...REPORT.tables[3], id: 'freq_ups', title: 'UPS ride-through', rows: [] },
      { ...REPORT.tables[3], id: 'freq_planned', title: 'Planned islanding', rows: [] }] })
    await screen.findByTestId('island-table-freq_events')
    expect(screen.queryByTestId('island-table-freq_ups')).toBeNull()
    expect(screen.getByTestId('island-empty').textContent).toBe('Nothing to report for this run: UPS ride-through; planned islanding.')
  })

  it('puts € per year on the money columns and the limit in words', async () => {
    const binding = { ...REPORT.tables[1], id: 'binding', title: 'Island limits that were reached, and what each costs',
      columns: ['scenario', 'row', 'hours binding', 'sum of duals'], rows: [['seamless', 'Island-n1-down-bess_new', 7, 83338]],
      assumed_rows: [] }
    renderReport({ ...REPORT, tables: [...REPORT.tables, binding] })
    const costs = await screen.findByTestId('island-table-costs')
    expect(costs.textContent).toContain('Cost of island capability (per year)')
    expect(costs.textContent).toMatch(/€\u00a0\d/)
    const table = await screen.findByTestId('island-table-binding')
    expect(table.textContent).toContain('Island N-1: loss of BESS new, downward reserve')
    expect(table.textContent).toContain('Cost of the limit (per year)')
    expect(within(table).getByTestId('island-binding-note').textContent).toContain('do not add up')
  })

  it('says what ran, what has not, and links to Grid connection', async () => {
    renderReport()
    const note = await screen.findByTestId('island-missing')
    expect(note.textContent).toMatch(/Ran: .*Not run yet: .*campus run with island tables/)
    expect(within(note).getByTestId('island-missing-link').textContent).toContain('Open Grid connection')
  })

  it('says what is missing', async () => {
    renderReport()
    expect((await screen.findByTestId('island-missing')).textContent).toContain('campus run with island tables')
  })
})

describe('island section: the form', () => {
  it('saves a PATCH of the requirements that changed, as plain values, and never the units', async () => {
    // GK B3 K4, K19: the server sets each source; a stored {value, source} is never sent back.
    renderSection()
    const user = userEvent.setup()
    await screen.findByTestId('island-form')
    await user.click(screen.getByLabelText('Seamless'))
    const sustained = screen.getByLabelText('Sustained ride-through (h)')
    await user.clear(sustained)
    await user.type(sustained, '12')
    await user.click(screen.getByLabelText(/Linearised unit commitment/))
    await user.click(screen.getByRole('button', { name: /Save island config/ }))
    await waitFor(() => expect(api.patchIsland).toHaveBeenCalledTimes(1))
    const [project, patch] = api.patchIsland.mock.calls[0] as [string, Record<string, unknown>]
    expect(project).toBe('Hub A')
    expect(patch).toEqual({ requirements: { scenarios: ['ups_bridged', 'seamless'], sustained_h: 12, linearised_uc: true } })
    expect(api.saveIsland).not.toHaveBeenCalled()
  })

  it('has nothing to save while nothing changed, so no request can be refused', async () => {
    renderSection()
    await screen.findByTestId('island-form')
    expect((screen.getByRole('button', { name: /Save island config/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows the requirements only in the raw editor, and refuses a source typed there in words', async () => {
    renderSection()
    await screen.findByTestId('island-form')
    const raw = screen.getByLabelText('Island config') as HTMLTextAreaElement
    expect(JSON.parse(raw.value)).toEqual(CONFIG.requirements)
    const edited = { ...CONFIG.requirements, sustained_h: { value: 24, source: 'measured' } }
    fireEvent.change(raw, { target: { value: JSON.stringify(edited) } })
    expect((await screen.findByTestId('island-raw-refusals')).textContent)
      .toContain('Sustained ride-through: the server sets sources. Enter the value only.')
    expect((screen.getByRole('button', { name: /Save island config/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows a 422 inline', async () => {
    api.patchIsland.mockRejectedValue({ response: { status: 422, data: { detail: 'requirements: ups_bridged needs pickup_blocks' } } })
    renderSection()
    const user = userEvent.setup()
    await screen.findByTestId('island-form')
    await user.click(screen.getByLabelText('Seamless'))
    await user.click(screen.getByRole('button', { name: /Save island config/ }))
    expect((await screen.findByTestId('island-refusal')).textContent).toContain('pickup_blocks')
  })

  it('sends the revision it read, and a 409 offers a reload instead of overwriting', async () => {
    api.island.mockResolvedValue({ config: CONFIG, study: null, revision: 'r1' })
    api.patchIsland.mockRejectedValue({ response: { status: 409, data: { detail: {
      error_kind: 'island_config_changed', message: 'changed' } } } })
    renderSection()
    const user = userEvent.setup()
    await screen.findByTestId('island-form')
    await user.click(screen.getByLabelText('Seamless'))
    await user.click(screen.getByRole('button', { name: /Save island config/ }))
    await waitFor(() => expect(api.patchIsland).toHaveBeenCalledTimes(1))
    expect(api.patchIsland.mock.calls[0][2]).toBe('r1')
    const stale = await screen.findByTestId('island-stale')
    expect(stale.textContent).toContain('changed since you opened them')
    expect(screen.queryByTestId('island-refusal')).toBeNull()
    const before = api.island.mock.calls.length
    await user.click(within(stale).getByRole('button', { name: 'Reload' }))
    await waitFor(() => expect(api.island.mock.calls.length).toBeGreaterThan(before))
    expect(screen.queryByTestId('island-stale')).toBeNull()
  })

  it('shows another 409 (a locked project) as its own refusal, not as a reload', async () => {
    api.patchIsland.mockRejectedValue({ response: { status: 409, data: { detail: {
      error_kind: 'project_locked', message: 'The project is locked by another user.' } } } })
    renderSection()
    const user = userEvent.setup()
    await screen.findByTestId('island-form')
    await user.click(screen.getByLabelText('Seamless'))
    await user.click(screen.getByRole('button', { name: /Save island config/ }))
    expect((await screen.findByTestId('island-refusal')).textContent).toContain('locked by another user')
    expect(screen.queryByTestId('island-stale')).toBeNull()
  })

  it('shows each unit control mode read-only, in words, with a link to its asset form', async () => {
    api.island.mockResolvedValue({ config: CONFIG, study: null, unit_controls: {
      bess: { kind: 'bess', control: 'gfm_droop', values: {}, sources: {}, preset_scope: null },
      gone: { kind: 'pv', control: 'gfl_qu', values: {}, sources: {}, preset_scope: null },
    } })
    useUIStore.setState({ uiMode: 'expert', selectedComponent: null })
    renderSection()
    const row = await screen.findByTestId('island-unit-control-bess')
    expect(row.textContent).toContain('BESS: Grid-forming, droop')
    expect(row.textContent).not.toContain('gfm_droop')
    expect(screen.queryByLabelText(/Control mode of/)).toBeNull()      // no editor here (design DC-04)
    const gone = await screen.findByTestId('island-unit-control-gone')
    await waitFor(() => expect(gone.textContent).toContain('no asset of this name in the network'))
    expect(screen.queryByTestId('island-unit-edit-gone')).toBeNull()
    await userEvent.click(await screen.findByTestId('island-unit-edit-bess'))
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'StorageUnit', name: 'bess' })
    expect(useUIStore.getState().flowReturn).not.toBeNull()            // Back returns to the Island section
  })

  it('runs the study on a saved, unchanged config and reads the island data again', async () => {
    renderSection()
    const user = userEvent.setup()
    await screen.findByTestId('island-form')
    await waitFor(() => expect(api.island).toHaveBeenCalledTimes(1))
    await user.click(screen.getByRole('button', { name: /Run cost-of-islanding study/ }))
    await waitFor(() => expect(api.runIslandStudy).toHaveBeenCalledWith('Hub A'))
    await waitFor(() => expect(api.island).toHaveBeenCalledTimes(2))
  })

  it('keeps no report here: it points to Results › Control, which opens on the island tab', async () => {
    renderSection()
    await screen.findByTestId('island-form')
    expect(screen.queryByTestId('island-report')).toBeNull()
    await userEvent.click(screen.getByTestId('island-results-open'))
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().resultsTabRequest).toBe('island')
  })

  it('cannot run an unsaved edit', async () => {
    renderSection()
    const user = userEvent.setup()
    await screen.findByTestId('island-form')
    await user.click(screen.getByLabelText('Seamless'))
    expect((screen.getByRole('button', { name: /Run cost-of-islanding study/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('says when the project has no island requirements', async () => {
    api.island.mockResolvedValue({ config: null, study: null })
    api.islandReport.mockResolvedValue({ caveat: CAVEAT, tables: [], missing: ['island_config.json'] })
    renderSection()
    await screen.findByText(/no island requirements yet/i)
    expect(screen.queryByTestId('island-form')).toBeNull()
  })
})
