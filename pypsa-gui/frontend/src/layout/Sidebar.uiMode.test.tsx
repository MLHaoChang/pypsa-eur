import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import Sidebar from './Sidebar'

// Guided-mode spec §3.5 — what the Sidebar hides in Guided. The Expert DOM
// itself is pinned by Sidebar.expertUnchanged.test.tsx (snapshot from base).

vi.mock('../api/network', () => ({
  networkApi: {
    getMeta: vi.fn().mockResolvedValue({ bus_count: 3 }),
    undoInfo: vi.fn().mockResolvedValue({ depth: 0 }),
  },
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

async function renderSidebar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  await act(async () => { await new Promise(r => setTimeout(r, 10)) })
  return utils
}

// The six PROJECT rows Guided hides (§3.5) and the four it keeps.
const HIDDEN_PROJECT_ROWS = ['Project info', 'Snapshots', 'Scenarios', 'Duplicate project', 'Export bundle', 'Workspace panel']
const KEPT_PROJECT_ROWS = ['Save', 'Other', 'Projects home']

beforeEach(() => {
  useUIStore.setState({
    currentProject: 'Demo',
    projectName: 'Demo',
    recents: ['Demo', 'Other'],
    lastSavedByProject: {},
    activeSlidePanel: null,
    assistantDockOpen: false,
  })
})

describe('Sidebar in Guided mode — expanded', () => {
  beforeEach(() => useUIStore.setState({ uiMode: 'guided', sidebarMode: 'expanded' }))

  it('hides DATA, SIMULATION and the canvas mode switcher', async () => {
    await renderSidebar()
    expect(screen.queryByTestId('sidebar-section-data')).toBeNull()
    expect(screen.queryByTestId('sidebar-section-simulation')).toBeNull()
    expect(screen.queryByTestId('sidebar-mode-switcher')).toBeNull()
    // Their rows go with them — Solve Queue lives in SIMULATION.
    expect(screen.queryByText('Assets')).toBeNull()
    expect(screen.queryByText('Solve Queue')).toBeNull()
    expect(screen.queryByText('Select / Pan')).toBeNull()
  })

  it('keeps the Assistant and adds Hub design above PROJECT', async () => {
    const { container } = await renderSidebar()
    expect(screen.getByTestId('sidebar-assistant')).toBeTruthy()
    const hub = screen.getByTestId('sidebar-hub-design')
    const project = screen.getByTestId('sidebar-section-project')
    // DOM order: Hub design comes before the PROJECT header.
    const all = Array.from(container.querySelectorAll('[data-testid]'))
    expect(all.indexOf(hub)).toBeLessThan(all.indexOf(project))
    expect(hub.textContent).toContain('Hub design')
  })

  it('Hub design opens the hubDesign panel', async () => {
    await renderSidebar()
    fireEvent.click(screen.getByTestId('sidebar-hub-design'))
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
  })

  it('PROJECT shows only the header card, Save, Recent and Projects home', async () => {
    await renderSidebar()
    for (const label of KEPT_PROJECT_ROWS) expect(screen.getByText(label)).toBeTruthy()
    for (const label of HIDDEN_PROJECT_ROWS) expect(screen.queryByText(label)).toBeNull()
    // The header card (project name + autosave) is still there.
    expect(screen.getByTitle('Rename project (header field)').textContent).toContain('Demo')
    expect(screen.getByText(/Autosave/)).toBeTruthy()
  })

  it('keeps the preferences footer', async () => {
    await renderSidebar()
    expect(screen.getByTitle(/theme/i)).toBeTruthy()
  })
})

describe('Sidebar in Guided mode — icon strip', () => {
  beforeEach(() => useUIStore.setState({ uiMode: 'guided', sidebarMode: 'icon' }))

  it('shows Assistant, Hub design and Project; hides Data, Simulation and the mode switcher', async () => {
    await renderSidebar()
    expect(screen.getByTestId('sidebar-assistant')).toBeTruthy()
    expect(screen.getByTestId('sidebar-hub-design')).toBeTruthy()
    expect(screen.getByTestId('sidebar-section-project')).toBeTruthy()
    expect(screen.queryByTestId('sidebar-section-data')).toBeNull()
    expect(screen.queryByTestId('sidebar-section-simulation')).toBeNull()
    expect(screen.queryByTestId('sidebar-mode-switcher')).toBeNull()
  })

  it('the Project flyout shows only the Guided rows', async () => {
    await renderSidebar()
    fireEvent.click(screen.getByTestId('sidebar-section-project'))
    await act(async () => { await new Promise(r => setTimeout(r, 10)) })
    for (const label of KEPT_PROJECT_ROWS) expect(screen.getByText(label)).toBeTruthy()
    for (const label of HIDDEN_PROJECT_ROWS) expect(screen.queryByText(label)).toBeNull()
  })

  it('Hub design opens the hubDesign panel', async () => {
    await renderSidebar()
    fireEvent.click(screen.getByTestId('sidebar-hub-design'))
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
  })
})

describe('Sidebar in Expert mode', () => {
  beforeEach(() => useUIStore.setState({ uiMode: 'expert' }))

  it('expanded: every section, the mode switcher and every PROJECT row; no Hub design', async () => {
    useUIStore.setState({ sidebarMode: 'expanded' })
    await renderSidebar()
    expect(screen.getByTestId('sidebar-section-project')).toBeTruthy()
    expect(screen.getByTestId('sidebar-section-data')).toBeTruthy()
    expect(screen.getByTestId('sidebar-section-simulation')).toBeTruthy()
    expect(screen.getByTestId('sidebar-mode-switcher')).toBeTruthy()
    expect(screen.queryByTestId('sidebar-hub-design')).toBeNull()
    for (const label of [...KEPT_PROJECT_ROWS, ...HIDDEN_PROJECT_ROWS]) expect(screen.getByText(label)).toBeTruthy()
  })

  it('icon strip: all three section buttons and the mode switcher; no Hub design', async () => {
    useUIStore.setState({ sidebarMode: 'icon' })
    await renderSidebar()
    expect(screen.getByTestId('sidebar-section-project')).toBeTruthy()
    expect(screen.getByTestId('sidebar-section-data')).toBeTruthy()
    expect(screen.getByTestId('sidebar-section-simulation')).toBeTruthy()
    expect(screen.getByTestId('sidebar-mode-switcher')).toBeTruthy()
    expect(screen.queryByTestId('sidebar-hub-design')).toBeNull()
  })

  it('switching modes live re-renders the sidebar', async () => {
    useUIStore.setState({ sidebarMode: 'expanded' })
    await renderSidebar()
    expect(screen.getByTestId('sidebar-section-data')).toBeTruthy()
    act(() => { useUIStore.setState({ uiMode: 'guided' }) })
    expect(screen.queryByTestId('sidebar-section-data')).toBeNull()
    act(() => { useUIStore.setState({ uiMode: 'expert' }) })
    expect(screen.getByTestId('sidebar-section-data')).toBeTruthy()
  })
})
