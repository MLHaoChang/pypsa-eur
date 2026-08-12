import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import Sidebar from './Sidebar'

// Where the Assistant lives in the nav.
//
// Reported twice by the user, in these words: "assistant still hides under
// simulation section". It did — the only nav entry for it was the last row of
// SimulationSectionContent, below Solver Settings / Model Horizon / Capacity
// Bounds / Issues / Results / Solve Queue.
//
// Two things are wrong with that, and they are separable:
//
//   1. CATEGORY. The assistant is not a simulation feature. It answers
//      questions about the network, drives every tool surface, and opens the
//      panels in all three sections. Filing it under SIMULATION tells the user
//      it belongs to the solve, which is the opposite of what the dock exists
//      to say.
//   2. REACHABILITY. Sidebar sections collapse (`toggleSection`), and the icon
//      strip replaces them with three section buttons that open a flyout. So
//      the single entry point could be behind a collapsed header, or two
//      clicks deep behind a flyout — for the affordance the design spec calls
//      "always-visible".
//
// These tests pin the fix as behaviour rather than as markup position: the
// Assistant control survives collapsing SIMULATION, and it exists in the icon
// strip without opening a flyout first.

vi.mock('../api/network', () => ({
  networkApi: { getMeta: vi.fn().mockResolvedValue({ bus_count: 3 }) },
}))
vi.mock('../api/io', () => ({ ioApi: {} }))
vi.mock('../api/projects', () => ({
  projectsApi: { list: vi.fn().mockResolvedValue([]), save: vi.fn() },
}))
vi.mock('../api/simulation', () => ({
  simulationApi: { preflight: vi.fn().mockResolvedValue({ errors: 0, warnings: 0 }) },
}))
vi.mock('../api/solveQueue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/solveQueue')>()
  return { ...actual, solveQueueApi: { list: vi.fn().mockResolvedValue({ jobs: [] }) } }
})
vi.mock('../api/localSettings', () => ({
  fetchLocalSettings: vi.fn().mockResolvedValue({ api_key_hint: null, log_path: '/tmp/app.log' }),
}))
vi.mock('../pages/ImportExport', () => ({ ImportZone: () => <div /> }))
vi.mock('./NewProjectWizard', () => ({ default: () => <div /> }))
vi.mock('../components/ProjectPicker', () => ({ default: () => <div /> }))
vi.mock('../utils/projectActions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/projectActions')>()
  return {
    ...actual,
    invalidateNetworkQueries: vi.fn(),
    saveProjectQuietly: vi.fn(),
    resetBackendNetwork: vi.fn(),
    downloadProjectBundle: vi.fn(),
    abortRunningSim: vi.fn(),
    switchToProject: vi.fn(),
  }
})

function renderSidebar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  cleanup()
  useUIStore.setState({
    sidebarMode: 'expanded',
    currentProject: 'Demo',
    projectName: 'Demo',
    activeSlidePanel: null,
    assistantDockOpen: false,
  })
})

describe('the Assistant nav entry', () => {
  it('is reachable in the expanded sidebar', () => {
    renderSidebar()
    expect(screen.getByTestId('sidebar-assistant')).toBeTruthy()
  })

  // The load-bearing one. A row inside SimulationSectionContent disappears
  // with the section; a top-level row does not. Collapsing the section is the
  // cheapest way to ask "is this filed under SIMULATION?" without asserting on
  // DOM ancestry, which would go stale the moment the markup is reshuffled.
  it('survives collapsing the SIMULATION section', () => {
    renderSidebar()
    fireEvent.click(screen.getByText('SIMULATION'))

    // Sanity: the section really did collapse.
    expect(screen.queryByText('Solve Queue')).toBeNull()
    expect(screen.getByTestId('sidebar-assistant')).toBeTruthy()
  })

  // Moving the row without removing the old one leaves two controls for one
  // piece of state, which read as two different features.
  it('is offered exactly once', () => {
    renderSidebar()
    expect(screen.getAllByTestId('sidebar-assistant')).toHaveLength(1)
  })

  it('toggles the dock rather than opening a slide panel', () => {
    renderSidebar()
    fireEvent.click(screen.getByTestId('sidebar-assistant'))

    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
  })

  // The icon strip is the sidebar's default on a narrow window and a common
  // user preference. It carries three section buttons, each of which opens a
  // flyout — so before this, reaching the assistant there was: click
  // Simulation, scan seven rows, click Assistant. The dock's own collapsed
  // strip is not a substitute: it is a different surface on the other side of
  // the screen, and the user reported not finding it.
  it('is reachable in the icon strip without opening a flyout', () => {
    useUIStore.setState({ sidebarMode: 'icon' })
    renderSidebar()

    fireEvent.click(screen.getByTestId('sidebar-assistant'))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
  })
})
