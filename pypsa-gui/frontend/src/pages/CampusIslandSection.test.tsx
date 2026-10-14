// The island section of the campus electrical panel (campus island plan, I9a).
// It holds the section to the backend's contract:
// - the form edits the requirements and control modes in the sidecar it was
//   sent, and Save sends that whole sidecar; a 422 renders inline;
// - Run asks for the study and the report is read again;
// - every report table shows its model and the screening caveat, and its
//   assumed rows are tagged; what is missing is said.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { IslandConfig, IslandReport } from '../api/campusElectrical'
import CampusIslandSection from './CampusIslandSection'

const api = vi.hoisted(() => ({
  island: vi.fn(), saveIsland: vi.fn(), runIslandStudy: vi.fn(), islandReport: vi.fn(),
}))
vi.mock('../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../api/campusElectrical')>('../api/campusElectrical')
  return { ...real, campusApi: api }
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
      rows: [['bridge_s', 600, 'assumed'], ['sustained_h', 24, 'datasheet']], assumed_rows: [0] },
    { id: 'costs', group: 'study', title: 'Cost of island capability', model: 'hub LP, plain LOPF (HiGHS)', caveat: CAVEAT,
      columns: ['scenario', 'status', 'objective', 'cost of island capability'],
      rows: [['ups_bridged', 'ok', 39810000, 2270000]], assumed_rows: [] },
    { id: 'static_flow', group: 'static', title: 'Island load flow at the island hours (I4)',
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
  api.runIslandStudy.mockResolvedValue({})
})
afterEach(() => cleanup())

describe('island section: the report', () => {
  it('shows every table with its model and the caveat', async () => {
    renderSection()
    for (const id of ['requirements', 'costs']) {
      const table = await screen.findByTestId(`island-table-${id}`)
      expect(within(table).getByTestId('island-caveat').textContent).toBe(CAVEAT)
      expect(within(table).getByTestId('island-model').textContent).toMatch(/^Model: /)
    }
    expect(within(screen.getByTestId('island-table-costs')).getByTestId('island-model').textContent)
      .toContain('hub LP')
  })

  it('tags the assumed rows and only those', async () => {
    renderSection()
    const table = await screen.findByTestId('island-table-requirements')
    const rows = table.querySelectorAll('tbody tr')
    expect(rows[0].getAttribute('data-assumed')).toBe('yes')
    expect(rows[0].textContent).toMatch(/assumed/i)
    expect(rows[1].getAttribute('data-assumed')).toBe('no')
  })

  it('heads each group once, in the order the report gives', async () => {
    renderSection()
    await screen.findByTestId('island-table-freq_events')
    const heads = screen.getAllByTestId('island-group').map(h => h.textContent)
    expect(heads).toEqual([
      'Inputs', 'Island study (I2)', 'Island steady state and short circuit (I4, I5, I5b)', 'Island frequency (I6)',
    ])
    expect(within(screen.getByTestId('island-table-static_flow')).getByTestId('island-model').textContent)
      .toContain('pandapower')
    const rows = screen.getByTestId('island-table-static_flow').querySelectorAll('tbody tr')
    expect([...rows].map(r => r.getAttribute('data-assumed'))).toEqual(['yes', 'no'])
  })

  it('keeps a long cell to one line with the full text on hover, and scrolls a table in its own box', async () => {
    const long = Array.from({ length: 40 }, (_, i) => `unit_${i}.droop_pct`).join('; ')
    api.islandReport.mockResolvedValue({ ...REPORT, tables: [{ ...REPORT.tables[2], rows: [[0, 3, true, long]] }] })
    renderSection()
    const table = await screen.findByTestId('island-table-static_flow')
    const cell = within(table).getByTitle(long)
    expect(cell.textContent).toBe(long)
    expect(cell.className).toMatch(/truncate/)
    const box = table.querySelector('[data-testid="island-table-scroll"]') as HTMLElement
    expect(box.className).toMatch(/overflow-auto/)
    expect(box.className).toMatch(/max-h-/)
  })

  it('says what is missing', async () => {
    renderSection()
    expect((await screen.findByTestId('island-missing')).textContent).toContain('campus run with island tables')
  })
})

describe('island section: the form', () => {
  it('edits the requirements and control modes, then saves the whole sidecar', async () => {
    renderSection()
    const user = userEvent.setup()
    await screen.findByTestId('island-form')
    await user.click(screen.getByLabelText('seamless'))
    const sustained = screen.getByLabelText('Sustained ride-through (h)')
    await user.clear(sustained)
    await user.type(sustained, '12')
    await user.selectOptions(screen.getByLabelText('Sustained ride-through (h) source'), 'datasheet')
    await user.click(screen.getByLabelText(/Linearised unit commitment/))
    await user.selectOptions(screen.getByLabelText('Control mode of bess'), 'gfm_vsm')
    await user.click(screen.getByRole('button', { name: /Save island config/ }))
    await waitFor(() => expect(api.saveIsland).toHaveBeenCalledTimes(1))
    const [project, sent] = api.saveIsland.mock.calls[0] as [string, IslandConfig]
    expect(project).toBe('Hub A')
    expect(sent.requirements.scenarios).toEqual(['ups_bridged', 'seamless'])
    expect(sent.requirements.sustained_h).toEqual({ value: 12, source: 'datasheet' })
    expect(sent.requirements.linearised_uc).toBe(true)
    expect(sent.units.bess.control).toBe('gfm_vsm')
    // What the form leaves out travels untouched.
    expect(sent.requirements.pickup_blocks).toEqual(a(5))
    expect(sent.units.g).toEqual(CONFIG.units.g)
  })

  it('shows a 422 inline', async () => {
    api.saveIsland.mockRejectedValue({ response: { status: 422, data: { detail: 'requirements: ups_bridged needs pickup_blocks' } } })
    renderSection()
    const user = userEvent.setup()
    await screen.findByTestId('island-form')
    await user.click(screen.getByLabelText('seamless'))
    await user.click(screen.getByRole('button', { name: /Save island config/ }))
    expect((await screen.findByTestId('island-refusal')).textContent).toContain('pickup_blocks')
  })

  it('runs the study on a saved, unchanged config and reads the report again', async () => {
    renderSection()
    const user = userEvent.setup()
    await screen.findByTestId('island-form')
    await waitFor(() => expect(api.islandReport).toHaveBeenCalledTimes(1))
    await user.click(screen.getByRole('button', { name: /Run cost-of-islanding study/ }))
    await waitFor(() => expect(api.runIslandStudy).toHaveBeenCalledWith('Hub A'))
    await waitFor(() => expect(api.islandReport).toHaveBeenCalledTimes(2))
  })

  it('cannot run an unsaved edit', async () => {
    renderSection()
    const user = userEvent.setup()
    await screen.findByTestId('island-form')
    await user.click(screen.getByLabelText('seamless'))
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
