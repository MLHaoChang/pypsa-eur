// W10 R3: the Control card's two study tabs. They show what the campus island study already
// holds, read-only, labelled with where it came from; they never look like a Run output
// (UX answer A: "Not yet available" with its why when there is nothing, the "Out of date" pill
// when the inputs moved, no Run button). The smoothing screen is the scorecard's: linked, not copied.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'

const api = vi.hoisted(() => ({
  island: vi.fn(), islandReport: vi.fn(), state: vi.fn(), islandCuts: vi.fn(), islandCutsStatus: vi.fn(),
  gfmSettings: vi.fn(), gfmApplyCard: vi.fn(), recommendGfmSettings: vi.fn(), applyGfmSettings: vi.fn(),
}))
vi.mock('../../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../../api/campusElectrical')>('../../api/campusElectrical')
  return { ...real, campusApi: { ...real.campusApi, ...api } }
})
const flow = vi.hoisted(() => ({ reliability: true }))
vi.mock('../../components/flowScope/useFlowScope', () => ({
  useFlowScope: () => ({
    isSuccess: true,
    scope: { version: 1, groups: flow.reliability ? ['control', 'reliability'] : ['control'],
      options: { control: ['presets'], reliability: flow.reliability ? ['goal_study'] : [] }, source: 'project', preset_id: 'p' },
    registry: { cards: [{ id: 'control', options: [
      { id: 'island_checks', status: 'not_yet_available', reason: 'The one Run does not request this yet.' }] }] },
  }),
  useInScope: () => false,
}))

import { IslandTab, GridFormingTab } from './ControlStudyTabs'

const REPORT = {
  caveat: 'screening', missing: [],
  tables: [{ id: 'requirements', title: 'Requirements', group: 'inputs', model: 'hub LP', caveat: 'screening',
    columns: ['requirement', 'value'], rows: [['sustained_h', 4]], assumed_rows: [0] }],
}
const CUTS = { limit: 'Bridge-energy and pickup failures get no cut', max_trials: 3, proposed: [], applied: [], trials: [] }
const notHub = () => Object.assign(new Error('x'), { response: { status: 409, data: { detail: 'not a hub project' } } })

function mount(el: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{el}</QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Hub A', activeSlidePanel: null, uiMode: 'expert', fieldDrawer: null,
    resultsTabRequest: null, gridReadinessRequest: false } as never)
  Object.values(api).forEach(f => f.mockReset())
  flow.reliability = true
  api.island.mockResolvedValue({ config: {}, study: { model: 'hub LP' }, study_at: '2026-10-21T09:30:00+00:00' })
  api.islandReport.mockResolvedValue(REPORT)
  api.state.mockResolvedValue({ stale: false, run_job: null })
  api.islandCuts.mockResolvedValue(CUTS)
  api.islandCutsStatus.mockResolvedValue(null)
})
afterEach(cleanup)

describe('Island & ride-through', () => {
  it('shows the island study read-only, labelled with its run date, and no Run output styling', async () => {
    mount(<IslandTab />)
    expect(await screen.findByTestId('island-table-requirements')).toBeTruthy()
    expect(screen.getByTestId('island-from').textContent).toContain('From the island study (run 2026-10-21).')
    expect(screen.queryByTestId('control-stale')).toBeNull()
    expect(screen.queryByText(/From the latest run/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Run |Apply cuts|Save island config/ })).toBeNull()
  })

  it('wears the Out of date pill, and no other stale wording, when the inputs moved since', async () => {
    api.state.mockResolvedValue({ stale: true, run_job: null })
    mount(<IslandTab />)
    expect((await screen.findByTestId('control-stale')).textContent).toBe('Out of date')
    expect(screen.queryByText(/stale/i)).toBeNull()
  })

  it('shows the cuts read-only and links to Grid study to apply them', async () => {
    mount(<IslandTab />)
    fireEvent.click(await screen.findByTestId('island-cuts-open'))
    expect(useUIStore.getState().activeSlidePanel).toBe('campusStudy')
  })

  it('links to the scorecard for the load-smoothing screen and copies none of it', async () => {
    mount(<IslandTab />)
    fireEvent.click(await screen.findByTestId('island-scorecard-link'))
    expect(useUIStore.getState().gridReadinessRequest).toBe(true)
    expect(api.state).toHaveBeenCalled()
  })

  it('with no island results says "Not yet available" with its why (not "Not offered"), never an empty table', async () => {
    api.island.mockResolvedValue({ config: null, study: null })
    api.islandReport.mockResolvedValue({ caveat: '', tables: [], missing: ['island_config.json'] })
    mount(<IslandTab />)
    expect((await screen.findByTestId('island-gate-why')).textContent)
      .toBe('Not yet available: The one Run does not request this yet.')
    expect(screen.queryByTestId('island-report')).toBeNull()
    expect(screen.queryByTestId('island-table-requirements')).toBeNull()
  })

  it('does not take the island inputs a project carries for results: they alone read "Not yet available"', async () => {
    api.island.mockResolvedValue({ config: {}, study: null })      // REPORT holds only `inputs` tables
    mount(<IslandTab />)
    expect((await screen.findByTestId('island-gate-why')).textContent).toMatch(/^Not yet available/)
    expect(screen.queryByTestId('island-table-requirements')).toBeNull()
    expect(screen.queryByTestId('island-from')).toBeNull()
  })

  it('shows the island-hours tables as results even without the cost study', async () => {
    api.island.mockResolvedValue({ config: {}, study: null })
    api.islandReport.mockResolvedValue({ ...REPORT, tables: [{ ...REPORT.tables[0], id: 'freq', group: 'frequency' }] })
    mount(<IslandTab />)
    expect(await screen.findByTestId('island-from')).toBeTruthy()
  })

  it('says a project of another kind has no island study', async () => {
    api.island.mockRejectedValue(notHub())
    mount(<IslandTab />)
    await waitFor(() => expect(screen.getByTestId('control-tab-state').textContent).toContain('capacity-expansion (hub) projects'))
  })

  it('has no project to read: says so', () => {
    useUIStore.setState({ currentProject: null } as never)
    mount(<IslandTab />)
    expect(screen.getByTestId('control-tab-state').textContent).toContain('Open a project')
  })

  it('Next is Reliability when it is in scope, else Hand-off; Guided and Expert show the same', async () => {
    mount(<IslandTab />)
    fireEvent.click(await screen.findByTestId('control-next-open'))
    expect(useUIStore.getState().resultsTabRequest).toBe('adequacy')
    cleanup()
    flow.reliability = false
    useUIStore.setState({ uiMode: 'guided' } as never)
    mount(<IslandTab />)
    expect((await screen.findByTestId('control-next')).textContent).toContain('hand off')
    expect(screen.getByTestId('island-table-requirements')).toBeTruthy()
  })
})

describe('Grid-forming', () => {
  const rec = {
    label: 'heuristic screening', ledger: [], status: 'recommended', achievable: true, changed: true,
    settings: { BESS1: { model: 'gfm_droop', current: { droop_pct: 4, i_max_pu: 1.2, H_equivalent_s: 0.4 },
      recommended: { droop_pct: 2.5, i_max_pu: 1.2, H_equivalent_s: 1.2 } } },
    cases: [], capacity: [], comparison: [], simulations: 3, max_simulations: 400, margin: 0.1,
    allow_i_max: false, notes: [], excluded: [],
  }

  it('shows the recommendation, labelled heuristic, and applies only through the edit card', async () => {
    api.gfmSettings.mockResolvedValue({ state: 'ready', reason: '', recommendation: rec })
    mount(<GridFormingTab />)
    expect(await screen.findByTestId('gfm-recommendation')).toBeTruthy()
    expect(screen.getByTestId('grid-forming-from').textContent).toContain('heuristic screening, not a certificate')
    expect(screen.queryByRole('button', { name: /^Apply to the island data/ })).toBeNull()   // the card comes first
    expect(screen.getByTestId('gfm-review')).toBeTruthy()
  })

  it('says a project of another kind has no recommendation', async () => {
    api.gfmSettings.mockRejectedValue(notHub())
    mount(<GridFormingTab />)
    await waitFor(() => expect(screen.getByTestId('control-tab-state').textContent).toContain('capacity-expansion (hub) projects'))
  })

  it('a moved recommendation wears the Out of date pill', async () => {
    api.gfmSettings.mockResolvedValue({ state: 'stale', reason: 'The campus run changed; recommend them again', recommendation: rec })
    mount(<GridFormingTab />)
    expect((await screen.findByTestId('gfm-reason')).textContent).toContain('Out of date')
    expect(screen.queryByTestId('gfm-review')).toBeNull()
  })
})
