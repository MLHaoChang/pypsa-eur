import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useEffect } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from './store/uiStore'

// Guided-mode spec §3.6 — the `hubDesign` panel slot and its default-open
// rule: in Guided, with a project open and no panel showing, App opens the
// hub-design panel ONCE per project per session. Closing it is respected; a
// different project opens it again; Expert never auto-opens anything.
//
// Same stubbing as App.dock.test.tsx (App's children are not the subject);
// the HubDesignPanel placeholder is kept real — it is the slot's content.

const { chatPanelMounts, stub } = vi.hoisted(() => ({
  chatPanelMounts: { current: 0 },
  // The returned component closure runs at RENDER time, by which point the
  // jsx-runtime import is long since initialised — only the factory itself is
  // hoist-sensitive.
  stub: (testid: string) => ({ default: () => <div data-testid={testid} /> }),
}))

// The one component kept REAL is AssistantDock — it is the subject. Its
// ChatPanel child is stubbed, but with a mount counter (same technique and
// rationale as AssistantDock.test.tsx) plus a real <textarea>, because the
// Escape tests below turn on the event target being an editable element.
vi.mock('./components/ChatPanel', () => ({
  default: () => {
    useEffect(() => {
      chatPanelMounts.current += 1
    }, [])
    return <textarea data-testid="chat-input" defaultValue="" />
  },
}))

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
vi.mock('./pages/TimeSeriesManager', () => stub('timeseries-stub'))
vi.mock('./pages/SolverSettings', () => stub('solver-stub'))
vi.mock('./pages/ModelHorizon', () => stub('horizon-stub'))
vi.mock('./pages/CapacityBoundsEditor', () => stub('capacity-stub'))
vi.mock('./pages/Results', () => stub('results-stub'))
vi.mock('./pages/SnapshotsPanel', () => stub('snapshots-stub'))
vi.mock('./pages/IssuesPanel', () => stub('issues-stub'))
vi.mock('./pages/OverviewPanel', () => stub('overview-stub'))
vi.mock('./pages/ScenariosPanel', () => stub('scenarios-stub'))
vi.mock('./pages/WorkspacePanel', () => stub('workspace-stub'))
vi.mock('./pages/CompareView', () => stub('compare-stub'))
vi.mock('./pages/SolveQueuePanel', () => stub('solve-queue-stub'))
vi.mock('./pages/LocalSettings', () => stub('local-settings-stub'))

vi.mock('./auth/AuthMismatchGate', () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))
vi.mock('./auth/config', () => ({ authEnabled: false, getAuthEnabled: () => false, setAuthEnabled: () => {} }))

vi.mock('./api/projects', () => ({ projectsApi: { list: vi.fn().mockResolvedValue([]), load: vi.fn() } }))
vi.mock('./api/network', () => ({
  networkApi: {
    getMeta: vi.fn().mockResolvedValue({ bus_count: 1 }),
    resetNetwork: vi.fn().mockResolvedValue({}),
  },
}))
vi.mock('./api/simulation', () => ({
  simulationApi: { getStatus: vi.fn().mockResolvedValue({ running: false }) },
  createLogStream: vi.fn(() => () => {}),
}))
vi.mock('./utils/projectActions', () => ({
  acquireProjectLock: vi.fn(),
  invalidateNetworkQueries: vi.fn(),
  stopLockHeartbeat: vi.fn(),
  switchToProject: vi.fn().mockResolvedValue(undefined),
}))

import App from './App'

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  chatPanelMounts.current = 0
  useUIStore.setState({
    activeSlidePanel: null,
    assistantDockOpen: false,
    compareRailOpen: false,
    currentProject: 'Demo',
  })
})

describe('the hubDesign panel slot', () => {
  it('renders the placeholder full-screen with its breadcrumb', () => {
    useUIStore.setState({ uiMode: 'expert', activeSlidePanel: 'hubDesign' })
    renderApp()
    const panel = screen.getByTestId('hub-design-panel')
    expect(panel.textContent).toContain('Coming in the next step')
    const container = screen.getByTestId('panel-container')
    expect(container.textContent).toContain('GUIDED')
    expect(container.textContent).toContain('Hub design')
    // Full-screen: the panel takes the whole area (FULL_SCREEN_TABS).
    expect(container.className).toContain('flex-1')
    expect(container.className).not.toContain('w-1/2')
  })
})

describe('auto-open in Guided', () => {
  it('opens hubDesign once when a project is open', () => {
    useUIStore.setState({ uiMode: 'guided' })
    renderApp()
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
    expect(screen.getByTestId('hub-design-panel')).toBeTruthy()
  })

  it('closing it does not reopen it for the same project', () => {
    useUIStore.setState({ uiMode: 'guided' })
    renderApp()
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
    fireEvent.click(screen.getByTitle('Close (Esc)'))
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    // Unrelated store churn re-runs the effect; it must stay closed.
    act(() => { useUIStore.setState({ assistantDockOpen: true }) })
    act(() => { useUIStore.setState({ uiMode: 'guided' }) })
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
  })

  it('a different project opens it again', () => {
    useUIStore.setState({ uiMode: 'guided' })
    renderApp()
    act(() => { useUIStore.getState().setSlidePanel(null) })
    act(() => { useUIStore.setState({ currentProject: 'Other' }) })
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
  })

  it('does not replace a panel that is already open', () => {
    useUIStore.setState({ uiMode: 'guided', activeSlidePanel: 'results' })
    renderApp()
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
  })

  it('needs a project', () => {
    useUIStore.setState({ uiMode: 'guided', currentProject: null })
    renderApp()
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
  })

  it('switching from Expert to Guided with a project and no panel opens it', () => {
    useUIStore.setState({ uiMode: 'expert' })
    renderApp()
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    act(() => { useUIStore.getState().setUiMode('guided', { explicit: true }) })
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
  })
})

describe('Expert never auto-opens', () => {
  it('leaves the canvas alone with a project open', () => {
    useUIStore.setState({ uiMode: 'expert' })
    renderApp()
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    expect(screen.queryByTestId('hub-design-panel')).toBeNull()
    act(() => { useUIStore.setState({ currentProject: 'Other' }) })
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
  })
})

// ── §10 addendum (gate P23 B2): the tour always wins over the auto-open ────
// The first case is the QA gate's repro verbatim (qa23/repro/App.qaTourRepro).
import { prepareTaggingTour } from './pages/results/prepareTaggingTour'
import { nk } from './utils/queryKeys'
import { GuideButton } from './components/GuidedTour'
describe('QA repro: tagging tour in Guided after an Expert->Guided switch on Results', () => {
  it('the tour prepare step closes the panel so the Properties bus card can show', async () => {
    useUIStore.setState({ uiMode: 'expert', activeSlidePanel: 'results' })
    renderApp()
    act(() => { useUIStore.getState().setUiMode('guided', { explicit: true }) })
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    const qc = new QueryClient()
    qc.setQueryData(nk('Demo', 'buses'), [{ name: 'B1', eh_poc: true }])
    await act(async () => { await prepareTaggingTour(qc, 'Demo', { waitMs: 50 }).catch(() => {}) })
    // Expect the canvas + PropertiesPanel (tour target host) — not a full-screen panel.
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    expect(screen.queryByTestId('properties-stub')).toBeTruthy()
  })
})

describe('a project counts as auto-opened once any panel was open for it in Guided', () => {
  it('Results open in Guided, then closed → hubDesign does not appear', () => {
    useUIStore.setState({ uiMode: 'guided', activeSlidePanel: 'results' })
    renderApp()
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    act(() => { useUIStore.getState().setSlidePanel(null) })
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
  })

  it('a panel open only in Expert does not count', () => {
    useUIStore.setState({ uiMode: 'expert', activeSlidePanel: 'results' })
    renderApp()
    act(() => { useUIStore.getState().setSlidePanel(null) })
    act(() => { useUIStore.getState().setUiMode('guided', { explicit: true }) })
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
  })
})

describe('the auto-open never fires while a guided tour is preparing or running', () => {
  it('a held tour blocks it, and releasing the hold does not open it afterwards', () => {
    useUIStore.setState({ uiMode: 'guided', currentProject: 'Demo', activeSlidePanel: 'results' })
    renderApp()
    act(() => { useUIStore.getState().holdGuidedTour() })
    act(() => { useUIStore.setState({ currentProject: 'Other', activeSlidePanel: null }) })
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    act(() => { useUIStore.getState().releaseGuidedTour() })
    expect(useUIStore.getState().guidedTourHolds).toBe(0)
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
  })

  it('the tagging tour started from Results in Guided (real GuideButton) keeps the canvas and Properties', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(nk('Fresh', 'buses'), [{ name: 'B1', eh_poc: true }])
    // A project that has never had a panel in Guided: Results opens in Expert,
    // the user switches to Guided on Results, then starts the tour there.
    useUIStore.setState({ uiMode: 'expert', currentProject: 'Fresh', activeSlidePanel: 'results' })
    render(
      <QueryClientProvider client={qc}>
        <App />
        <GuideButton tourId="eh_tagging" testId="eh-tagging-guide-button"
          prepare={() => prepareTaggingTour(qc, 'Fresh', { waitMs: 50 })} />
      </QueryClientProvider>,
    )
    act(() => { useUIStore.getState().setUiMode('guided', { explicit: true }) })
    await act(async () => {
      fireEvent.click(screen.getByTestId('eh-tagging-guide-button'))
      await new Promise(r => setTimeout(r, 150))
    })
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    expect(screen.queryByTestId('hub-design-panel')).toBeNull()
    expect(screen.getByTestId('properties-stub')).toBeTruthy()
    // The launched tour holds the auto-open off while it is on screen.
    expect(useUIStore.getState().guidedTourHolds).toBeGreaterThan(0)
  })
})
