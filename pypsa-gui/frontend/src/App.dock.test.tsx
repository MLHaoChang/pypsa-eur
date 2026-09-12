import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useEffect } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from './store/uiStore'

// App-level composition tests.
//
// AssistantDock.eviction.test.tsx renders the dock standalone, so it pins the
// dock's own independence from `activeSlidePanel` but says nothing about WHERE
// App mounts it — and where it is mounted is half the fix. Nesting
// `<AssistantDock />` inside App's `{activeSlidePanel && …}` panel container
// reproduces the original bug (the dock inherits that container's mount
// lifetime and its ErrorBoundary key) while leaving every other test in the
// suite green. These tests close that gap.
//
// Everything expensive is stubbed. The subjects are App's own JSX structure
// and its global keydown handler; canvases, panels and network calls are not
// involved in either, and rendering them for real would make this a slow test
// about recharts.

// Both of these have to come from vi.hoisted, not from plain top-level
// consts: App.tsx's own imports trigger these mock factories during the
// import phase, before this file's body has run, so a `const stub = …`
// declared below would still be in its temporal dead zone. (`chatPanelMounts`
// needs it for the separate documented reason — see AssistantDock.test.tsx.)
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
  localStorage.clear()
  chatPanelMounts.current = 0
  useUIStore.setState({
    activeSlidePanel: null,
    assistantDockOpen: true,
    compareRailOpen: false,
    currentProject: 'Demo',
  })
})

describe('where App mounts the assistant dock', () => {
  it('renders the dock with no slide panel open', () => {
    renderApp()
    expect(screen.getByTestId('assistant-dock')).toBeTruthy()
  })

  it('keeps the dock on screen while a full-screen tab owns the main area', () => {
    useUIStore.setState({ activeSlidePanel: 'results' })
    renderApp()

    // Results is a FULL_SCREEN_TABS member — the case the user reported.
    expect(screen.getByTestId('results-stub')).toBeTruthy()
    expect(screen.getByTestId('assistant-dock')).toBeTruthy()
    // And genuinely outside the SLIDE-PANEL CONTAINER — the div App mounts
    // under `{activeSlidePanel && …}` and wraps in an ErrorBoundary keyed on
    // `${activeSlidePanel}-${currentProject}`. Asserting against the container
    // rather than the stubbed tab body matters: the tab body is a leaf, so a
    // dock nested as its sibling inside the container would not be "inside"
    // it and this assertion would pass under the very regression it names.
    // `data-testid="panel-container"` exists in App.tsx for this.
    expect(screen.getByTestId('panel-container').contains(screen.getByTestId('assistant-dock'))).toBe(false)
  })

  // The load-bearing one. Presence checks alone stay green if the dock is
  // moved inside the panel container while a panel happens to be open; what
  // that move actually costs is mount identity, because App keys that
  // container's ErrorBoundary on `${activeSlidePanel}-${currentProject}`. A
  // ChatPanel remount mid-turn kills the stream exactly like the unmount this
  // branch exists to remove — so navigating between panels, and closing back
  // to none, must not disturb the instance.
  it('keeps one ChatPanel instance across navigation between panels', () => {
    renderApp()
    expect(chatPanelMounts.current).toBe(1)

    act(() => { useUIStore.getState().setSlidePanel('results') })
    expect(screen.getByTestId('assistant-dock')).toBeTruthy()
    expect(chatPanelMounts.current).toBe(1)

    act(() => { useUIStore.getState().setSlidePanel('overview') })
    expect(chatPanelMounts.current).toBe(1)

    act(() => { useUIStore.getState().setSlidePanel(null) })
    expect(screen.getByTestId('assistant-dock')).toBeTruthy()
    expect(chatPanelMounts.current).toBe(1)
  })
})

// ── Escape must not reach past the composer ────────────────────────────────
//
// The keyboard twin of the dock's `data-no-panel-close` exemption. App's
// global keydown handler closes the compare rail on Escape, then the active
// slide panel. The assistant's composer is now permanently on screen, and
// ChatPanel binds Escape in it to "stop dictation" — so an unguarded handler
// means the keystroke that stops the mic also closes the compare rail, and a
// second one closes the panel the agent just opened. Impossible before this
// branch, because the assistant WAS the slide panel.
describe('Escape handling with the composer always on screen', () => {
  it('ignores Escape typed into the chat composer', () => {
    useUIStore.setState({ activeSlidePanel: 'results', compareRailOpen: true })
    renderApp()

    const composer = screen.getByTestId('chat-input')
    fireEvent.keyDown(composer, { key: 'Escape' })
    // Twice: the first Escape would have taken the rail, the second the panel.
    fireEvent.keyDown(composer, { key: 'Escape' })

    expect(useUIStore.getState().compareRailOpen).toBe(true)
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
  })

  // The negative control. A guard that swallowed Escape everywhere would pass
  // the test above while breaking the shortcut for everyone, so the existing
  // behaviour has to stay pinned: rail first, then panel.
  it('still closes the compare rail and then the panel from a non-editable target', () => {
    useUIStore.setState({ activeSlidePanel: 'results', compareRailOpen: true })
    renderApp()

    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(useUIStore.getState().compareRailOpen).toBe(false)
    expect(useUIStore.getState().activeSlidePanel).toBe('results')

    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
  })
})
