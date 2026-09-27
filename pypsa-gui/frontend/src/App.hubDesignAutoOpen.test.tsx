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
