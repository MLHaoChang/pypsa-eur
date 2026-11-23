// Control-strategy 03 / improvement 1: Guided section 8's Island requirements
// card. At most three decisions (dispatch limits, scenarios, backup), plain
// words only, the reserve "off (assumed)" until a share is set (RESERVE-SHOW-1),
// the backup numbers saved through the island PATCH with the stored revision.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { simulationApi } from '../../../api/simulation'
import { campusApi, type IslandConfig } from '../../../api/campusElectrical'
import { useUIStore } from '../../../store/uiStore'
import { goal } from '../../../test/fixtures/reliabilityGoal'
import { IslandRequirementsCard, isDispatchLimitTarget, reserveWords } from './IslandRequirementsCard'

vi.mock('../../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/simulation')>()
  return { ...actual, simulationApi: { ...actual.simulationApi, getSolverConfig: vi.fn(), updateSolverConfig: vi.fn() } }
})
vi.mock('../../../api/campusElectrical', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, island: vi.fn(), patchIsland: vi.fn() } }
})
const getReliabilityGoal = vi.fn()
vi.mock('../../../api/flow', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/flow')>()
  return { ...actual, getReliabilityGoal: (...a: unknown[]) => getReliabilityGoal(...a) }
})

const CONFIG: IslandConfig = {
  units: {},
  requirements: {
    scenarios: ['ups_bridged'],
    sustained_h: { value: 4, source: 'assumed' },
    bridge_s: { value: 30, source: 'datasheet' },
    fuel_autonomy_h: { value: 48, source: 'datasheet' },
    genset_redundancy_n: { value: 1, source: 'datasheet' },
    rocof_window_ms: { value: 500, source: 'assumed' },
  },
}
const state = (config: IslandConfig | null) =>
  ({ config, revision: 'rev1', study: null, presets: [], services: [], warnings: [] }) as never

function archetype(a: string) {
  getReliabilityGoal.mockResolvedValue(goal({ site_archetype: [a, 'template'] } as never))
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  archetype('off_grid')
  vi.mocked(simulationApi.getSolverConfig).mockResolvedValue({} as never)
  vi.mocked(simulationApi.updateSolverConfig).mockResolvedValue({} as never)
  vi.mocked(campusApi.island).mockResolvedValue(state(CONFIG))
  vi.mocked(campusApi.patchIsland).mockResolvedValue(state(CONFIG))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><IslandRequirementsCard /></QueryClientProvider>)
}

describe('IslandRequirementsCard', () => {
  it('is absent on a grid-connected project with no island config', async () => {
    archetype('strong_grid')
    vi.mocked(campusApi.island).mockResolvedValue(state(null))
    const { container } = mount()
    await waitFor(() => expect(campusApi.island).toHaveBeenCalled())
    await waitFor(() => expect(getReliabilityGoal).toHaveBeenCalled())
    expect(container.querySelector('[data-testid="hub-island-requirements"]')).toBeNull()
  })

  it('shows three decisions on an off-grid site with a config, with no engine ids', async () => {
    const { container } = mount()
    await screen.findByText('Backup requirements')
    await screen.findByText('Operating reserve: off (assumed)')
    expect(container.querySelectorAll('[data-decision]')).toHaveLength(3)
    expect(screen.getByTestId('island-renewable').textContent).toBe('Minimum renewable fraction: none (assumed)')
    expect(screen.getByTestId('island-backup').textContent)
      .toBe('Backup: ride-through 4 h · UPS 30 s · fuel 48 h · generators N+1 (assumed)')
    expect(screen.getByTestId('island-remainder').textContent).toContain('Rate-of-change window (ms) 500 (assumed)')
    const text = container.textContent ?? ''
    for (const id of ['ups_bridged', 'reserve_share', 'sustained_h', 'min_renewable', 'rocof_window']) {
      expect(text).not.toContain(id)
    }
  })

  it('restates the reserve once a share is set', () => {
    expect(reserveWords({ reserve_share_load: 0.1, reserve_share_pv: 0.2, reserve_share_wind: 0 }))
      .toBe('Operating reserve: 10 % of load, 20 % of solar')
  })

  it('saves only the changed reserve share, as a fraction', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByTestId('island-dispatch-edit'))
    const load = await screen.findByLabelText('Reserve for load (%)')
    await user.type(load, '10')
    await user.click(screen.getByTestId('island-dispatch-save'))
    await waitFor(() => expect(simulationApi.updateSolverConfig).toHaveBeenCalledWith({ reserve_share_load: 0.1 }))
  })

  it('keeps Save off while a share is refused, the message opening with the label', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByTestId('island-dispatch-edit'))
    await user.type(await screen.findByLabelText('Reserve for solar output (%)'), '150')
    expect(screen.getByTestId('island-dispatch-error-reserve_share_pv').textContent)
      .toMatch(/^Reserve for solar output: /)
    expect((screen.getByTestId('island-dispatch-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('saves the scenarios through the island PATCH with the revision', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByLabelText('Seamless'))
    await waitFor(() => expect(campusApi.patchIsland).toHaveBeenCalledWith(
      'Demo', { requirements: { scenarios: ['ups_bridged', 'seamless'] } }, 'rev1'))
  })

  it('sends a backup edit as plain numbers with the revision', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByTestId('island-backup-edit'))
    const ups = screen.getByLabelText('UPS bridge (s)')
    await user.clear(ups)
    await user.type(ups, '60')
    await user.click(screen.getByTestId('island-backup-save'))
    await waitFor(() => expect(campusApi.patchIsland).toHaveBeenCalledWith(
      'Demo', { requirements: { bridge_s: 60 } }, 'rev1'))
  })

  it('refuses a negative backup number with a sentence opening with its name', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByTestId('island-backup-edit'))
    const fuel = screen.getByLabelText('Fuel autonomy (h)')
    await user.clear(fuel)
    await user.type(fuel, '-2')
    expect(screen.getByRole('alert').textContent).toBe('Fuel autonomy: -2 is below 0.')
    expect((screen.getByTestId('island-backup-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('offers a reload when the island settings changed since they were read', async () => {
    vi.mocked(campusApi.patchIsland).mockRejectedValue(
      { response: { status: 409, data: { detail: { error_kind: 'island_config_changed', message: 'changed' } } } })
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByLabelText('Planned'))
    expect((await screen.findByTestId('island-stale')).textContent)
      .toContain('The island settings changed since you opened them. Reload and try again.')
    await user.click(screen.getByRole('button', { name: 'Reload' }))
    await waitFor(() => expect(campusApi.island).toHaveBeenCalledTimes(2))
  })

  it('shows another 409 (a locked project) as its own refusal, not as a reload', async () => {
    // GK B3 (6) ask 6: only island_config_changed means the file moved.
    vi.mocked(campusApi.patchIsland).mockRejectedValue(
      { response: { status: 409, data: { detail: { error_kind: 'project_locked', message: 'The project is locked by another user.' } } } })
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByLabelText('Planned'))
    expect((await screen.findByTestId('island-save-error')).textContent).toContain('locked by another user')
    expect(screen.queryByTestId('island-stale')).toBeNull()
  })

  it('opens Grid study › Island in Expert, scrolled to the Island block', async () => {
    useUIStore.setState({ uiMode: 'guided', activeSection: 'scenarios', flowReturn: null })
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByTestId('island-open-expert'))
    const ui = useUIStore.getState()
    expect(ui.uiMode).toBe('expert')
    expect(ui.activeSlidePanel).toBe('campusStudy')
    expect(ui.panelSectionRequest).toEqual({ panel: 'campusStudy', section: 'island' })
    expect(ui.flowReturn).not.toBeNull()
  })
})

describe('isDispatchLimitTarget', () => {
  it('matches the two Assumptions rows and nothing else', () => {
    expect(isDispatchLimitTarget({ section: 'scenarios', field: 'reserve_share_load' })).toBe(true)
    expect(isDispatchLimitTarget({ section: 'scenarios', field: 'min_renewable_fraction' })).toBe(true)
    expect(isDispatchLimitTarget({ section: 'scenarios', field: 'voll' })).toBe(false)
    expect(isDispatchLimitTarget({ section: 'connection', field: 'reserve_share_load' })).toBe(false)
    expect(isDispatchLimitTarget(null)).toBe(false)
  })
})
