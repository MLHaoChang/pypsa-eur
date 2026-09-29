// A2 (deferred spec 2026-09-28 §2.1): after a backend restart the tab can
// hold `currentProject = X` while the backend is bound to Y. App compares
// `/network/meta.loaded_project` with `currentProject` on every settled
// sample and, only after TWO consecutive disagreeing samples taken while no
// project switch is in flight, raises the blocking ProjectMismatchBanner.
// One disagreeing sample is ignored: the open / switch / rebind paths all
// have a window where the backend moved before `currentProject` did.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from './store/uiStore'
import { nk } from './utils/queryKeys'

const { stub, load, list, getMeta, switchToProject } = vi.hoisted(() => ({
  stub: (testid: string) => ({ default: () => <div data-testid={testid} /> }),
  load: vi.fn(),
  list: vi.fn(),
  getMeta: vi.fn(),
  switchToProject: vi.fn(),
}))

vi.mock('./components/ChatPanel', () => stub('chat-panel-stub'))
vi.mock('./layout/AppHeader', () => stub('app-header-stub'))
vi.mock('./layout/ProjectTabs', () => stub('project-tabs-stub'))
vi.mock('./layout/Sidebar', () => stub('sidebar-stub'))
vi.mock('./layout/PropertiesPanel', () => stub('properties-stub'))
vi.mock('./layout/BottomPanel', () => stub('bottom-panel-stub'))
vi.mock('./components/StatusBar', () => stub('status-bar-stub'))
vi.mock('./components/MapModeSwitcher', () => stub('map-mode-stub'))
vi.mock('./components/SnapshotPicker', () => stub('snapshot-picker-stub'))
vi.mock('./components/CommandPalette', () => stub('palette-stub'))
vi.mock('./components/RescaleDialogHost', () => stub('rescale-host-stub'))
vi.mock('./components/CrashRecoveryBanner', () => stub('crash-banner-stub'))
vi.mock('./components/LockBanner', () => stub('lock-banner-stub'))
vi.mock('./components/ShortcutsHelp', () => ({ default: () => <div data-testid="shortcuts-stub" /> }))
vi.mock('./pages/TopologyCanvas', () => stub('topology-stub'))
vi.mock('./pages/MapCanvas', () => stub('map-canvas-stub'))
vi.mock('./auth/AuthMismatchGate', () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))
vi.mock('./auth/config', () => ({ authEnabled: false, getAuthEnabled: () => false, setAuthEnabled: () => {} }))
vi.mock('./api/projects', () => ({ projectsApi: { list, load } }))
vi.mock('./api/network', () => ({ networkApi: { getMeta, resetNetwork: vi.fn() } }))
vi.mock('./api/simulation', () => ({
  simulationApi: { getStatus: vi.fn().mockResolvedValue({ running: false }) },
  createLogStream: vi.fn(() => () => {}),
}))
vi.mock('./utils/projectActions', () => ({
  acquireProjectLock: vi.fn(),
  invalidateNetworkQueries: vi.fn(),
  stopLockHeartbeat: vi.fn(),
  switchToProject,
}))

import App from './App'
import { mismatchPoll } from './hooks/useProjectMismatchDetection'

// The test drives every sample itself (`sample()`); the hook's own poll would
// add samples at wall-clock times and make "one sample" / "two samples" racy.
const POLL = { ...mismatchPoll }
beforeAll(() => { mismatchPoll.idleMs = 1e9; mismatchPoll.confirmMs = 1e9 })
afterAll(() => { Object.assign(mismatchPoll, POLL) })

const SENTENCE = 'This tab shows X, but the app is now on Y. Changes from this tab are paused.'
let qc: QueryClient

function renderApp() {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><App /></QueryClientProvider>)
}

/** The first settled sample (App's own recovery effect also calls getMeta,
 *  so "getMeta was called" does not mean the query has its sample yet). */
async function firstSample() {
  await waitFor(() => expect(qc.getQueryState(nk('X', 'meta'))?.dataUpdateCount ?? 0).toBeGreaterThan(0))
}
/** One more settled `/network/meta` sample on the tab's key. */
async function sample() {
  await act(async () => { await qc.refetchQueries({ queryKey: nk('X', 'meta') }) })
  // React Query notifies its observers on a setTimeout(0) tick: let the hook
  // see this sample settled before the next one starts fetching.
  await act(async () => { await new Promise(r => setTimeout(r, 0)) })
}
const meta = (loaded_project: string | null, bus_count = 3) =>
  ({ name: '', snapshot_count: 1, bus_count, loaded_project })

beforeEach(() => {
  localStorage.clear()
  load.mockReset().mockResolvedValue({})
  list.mockReset().mockResolvedValue([{ name: 'X', project_kind: 'network' }, { name: 'Y' }])
  getMeta.mockReset()
  switchToProject.mockReset().mockResolvedValue({ status: 'switched' })
  useUIStore.setState({
    activeSlidePanel: null, assistantDockOpen: false, compareRailOpen: false,
    currentProject: 'X', projectMismatch: null, projectSwitchInProgress: false, uiMode: 'expert',
  })
})
afterEach(() => { cleanup(); useUIStore.setState({ projectMismatch: null }) })

describe('tab / backend project mismatch (A2)', () => {
  it('two settled samples on Y while the tab shows X → the banner, and no load', async () => {
    getMeta.mockResolvedValue(meta('Y'))
    renderApp()
    await firstSample()
    await sample()
    const banner = await screen.findByTestId('project-mismatch')
    expect(banner.getAttribute('role')).toBe('alert')
    expect(banner.textContent).toContain(SENTENCE)
    expect(useUIStore.getState().projectMismatch).toEqual({ tab: 'X', backend: 'Y' })
    expect(load).not.toHaveBeenCalled()
  })

  it('one disagreeing sample is ignored', async () => {
    getMeta.mockResolvedValue(meta('Y'))
    renderApp()
    await waitFor(() => expect(qc.getQueryData(nk('X', 'meta'))).toEqual(meta('Y')))
    getMeta.mockResolvedValue(meta('X'))
    await sample()
    await sample()
    expect(screen.queryByTestId('project-mismatch')).toBeNull()
    expect(useUIStore.getState().projectMismatch).toBeNull()
  })

  it('a disagreeing sample during a project switch does not count', async () => {
    useUIStore.setState({ projectSwitchInProgress: true })
    getMeta.mockResolvedValue(meta('Y'))
    renderApp()
    await firstSample()
    await sample()
    expect(screen.queryByTestId('project-mismatch')).toBeNull()
    // the switch ends; the next sample agrees → still nothing
    act(() => { useUIStore.setState({ projectSwitchInProgress: false }) })
    getMeta.mockResolvedValue(meta('X'))
    await sample()
    expect(screen.queryByTestId('project-mismatch')).toBeNull()
    // two settled disagreeing samples after the switch → the banner
    getMeta.mockResolvedValue(meta('Y'))
    await sample()
    expect(screen.queryByTestId('project-mismatch')).toBeNull()
    await sample()
    expect(await screen.findByTestId('project-mismatch')).toBeTruthy()
  })

  it('an agreeing sample clears it', async () => {
    getMeta.mockResolvedValue(meta('Y'))
    renderApp()
    await firstSample()
    await sample()
    await screen.findByTestId('project-mismatch')
    getMeta.mockResolvedValue(meta('X'))
    await sample()
    await waitFor(() => expect(screen.queryByTestId('project-mismatch')).toBeNull())
  })

  it('Reload → load(X) and the banner clears', async () => {
    getMeta.mockResolvedValue(meta('Y'))
    renderApp()
    await firstSample()
    await sample()
    const reload = await screen.findByTestId('project-mismatch-reload')
    expect(reload.textContent).toBe('Reload X')
    getMeta.mockResolvedValue(meta('X'))
    fireEvent.click(reload)
    await waitFor(() => expect(load).toHaveBeenCalledWith('X'))
    await waitFor(() => expect(screen.queryByTestId('project-mismatch')).toBeNull())
    expect(useUIStore.getState().projectMismatch).toBeNull()
    expect(switchToProject).not.toHaveBeenCalled()
  })

  it('Reload refused with study_in_flight → its sentence under the button, Switch still enabled', async () => {
    const message = 'Cannot load a project while a FMEA sweep is running — wait for it to finish, or abort it, and retry.'
    load.mockRejectedValue(Object.assign(new Error('Request failed with status code 409'), {
      response: { status: 409, data: { detail: { error_kind: 'study_in_flight', study: 'fmea_sweep', message } } },
    }))
    getMeta.mockResolvedValue(meta('Y'))
    renderApp()
    await firstSample()
    await sample()
    fireEvent.click(await screen.findByTestId('project-mismatch-reload'))
    expect((await screen.findByTestId('project-mismatch-reload-error')).textContent).toBe(message)
    expect(screen.getByTestId('project-mismatch')).toBeTruthy()
    expect((screen.getByTestId('project-mismatch-switch') as HTMLButtonElement).disabled).toBe(false)
  })

  it('Switch → switchToProject(Y) and the banner clears', async () => {
    getMeta.mockResolvedValue(meta('Y'))
    renderApp()
    await firstSample()
    await sample()
    const sw = await screen.findByTestId('project-mismatch-switch')
    expect(sw.textContent).toBe('Switch to Y')
    fireEvent.click(sw)
    await waitFor(() => expect(switchToProject).toHaveBeenCalledWith('Y', expect.anything()))
    await waitFor(() => expect(useUIStore.getState().projectMismatch).toBeNull())
    expect(load).not.toHaveBeenCalled()
  })

  it('loaded_project null with an empty network → the old reload path, no banner', async () => {
    getMeta.mockResolvedValue(meta(null, 0))
    renderApp()
    await waitFor(() => expect(load).toHaveBeenCalledWith('X'))
    await sample()
    expect(screen.queryByTestId('project-mismatch')).toBeNull()
  })

  it('a study tab (no network by design) never raises the banner', async () => {
    list.mockResolvedValue([{ name: 'X', project_kind: 'planning_dynamics' }])
    getMeta.mockResolvedValue(meta('Y'))
    renderApp()
    await waitFor(() => expect(list).toHaveBeenCalled())
    await sample()
    await sample()
    expect(screen.queryByTestId('project-mismatch')).toBeNull()
  })

  it('the tab moving to the backend\'s project (a switch elsewhere) clears it on the new key\'s first sample', async () => {
    getMeta.mockResolvedValue(meta('Y'))
    renderApp()
    await firstSample()
    await sample()
    await screen.findByTestId('project-mismatch')
    act(() => { useUIStore.getState().setCurrentProject('Y') })
    await waitFor(() => expect(screen.queryByTestId('project-mismatch')).toBeNull())
  })

  it('an empty network bound to ANOTHER project is not silently re-loaded over that binding', async () => {
    getMeta.mockResolvedValue(meta('Y', 0))
    renderApp()
    await firstSample()
    await sample()
    await screen.findByTestId('project-mismatch')
    expect(load).not.toHaveBeenCalled()
  })
})
