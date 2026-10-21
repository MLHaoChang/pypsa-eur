// M2 (plan 2026-10-05 §11, UX assessment §4.2): the Expert sidebar follows
// the order of the work — build the network, set its time, run it, read the
// results, take it further in a study, write it up — instead of grouping by
// kind (PROJECT / DATA / SIMULATION / STUDIES). The project itself is not a
// step: it moves to a card in the footer, with its actions behind a menu.
// Guided keeps its own sidebar (spec §3.5), pinned by Sidebar.uiMode.test.tsx.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useDecisionStore } from '../pages/decision/decisionStore'
import Sidebar from './Sidebar'

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

// Workspace reconciliation §6.1: the ten flow sections under four bands, then
// the project utilities. Bands are the collapsible headers; each section's
// rows are the rows the old steps held, under their new section.
const ORDER = ['setup', 'model', 'run', 'results', 'reports']

// What each band holds, per section. Planning → dynamics shows because the
// mocked project list is empty (unknown is not "not a study").
const ROWS: Record<string, string[]> = {
  setup: ['Model Horizon', 'Carriers'],
  model: ['Campus electrical', 'Loads', 'Add demand', 'Assets', 'Capacity Bounds', 'Time Series', 'Profile Studio', 'Economics'],
  run: ['Solver Settings', 'Scenarios', 'Issues', 'Solve Queue'],
  results: ['Results', 'Planning → dynamics', 'Reports'],
  reports: ['Snapshots'],
}

// The project's actions, now behind the footer card's menu.
const PROJECT_MENU_ROWS = [
  'Project info', 'Save', 'Duplicate project', 'Export bundle', 'Other', 'Workspace panel',
]

function rowLabels(group: HTMLElement): string[] {
  return Array.from(group.querySelectorAll('button'))
    .filter(b => !b.hasAttribute('data-testid'))
    .map(b => b.querySelector('span.flex-1')?.textContent ?? '')
    .filter(Boolean)
}

beforeEach(() => {
  useUIStore.setState({
    uiMode: 'expert',
    currentProject: 'Demo',
    projectName: 'Demo',
    recents: ['Demo', 'Other'],
    lastSavedByProject: {},
    activeSlidePanel: null,
    assistantDockOpen: false,
  })
})

describe('M2 — Expert sidebar, expanded', () => {
  beforeEach(() => useUIStore.setState({ sidebarMode: 'expanded' }))

  it('lists the bands in the order of the flow', async () => {
    const { container } = await renderSidebar()
    const headers = Array.from(container.querySelectorAll('[data-testid^="sidebar-section-"]'))
      .map(el => el.getAttribute('data-testid')!.replace('sidebar-section-', ''))
    expect(headers).toEqual(ORDER)
  })

  it.each(ORDER)('%s holds its rows, in order', async (id) => {
    await renderSidebar()
    expect(rowLabels(screen.getByTestId(`sidebar-group-${id}`))).toEqual(ROWS[id])
  })

  // UX-flow #21: the Decision study row shows only in a study's own project.
  it('shows the Decision study row only in the project of the open decision study', async () => {
    useDecisionStore.setState({ active: { project: 'Demo', studyId: 's1' } })
    await renderSidebar()
    expect(rowLabels(screen.getByTestId('sidebar-group-results')))
      .toEqual(['Results', 'Decision study', 'Planning → dynamics', 'Reports'])
    cleanup()
    useDecisionStore.setState({ active: { project: 'Elsewhere', studyId: 's1' } })
    await renderSidebar()
    expect(rowLabels(screen.getByTestId('sidebar-group-results'))).not.toContain('Decision study')
    useDecisionStore.setState({ active: null })
  })

  it('drops the old kind-based headers', async () => {
    await renderSidebar()
    for (const old of ['BUILD', 'TIME', 'DATA', 'SIMULATION']) expect(screen.queryByText(old)).toBeNull()
  })

  it('a step collapses and opens again', async () => {
    await renderSidebar()
    fireEvent.click(screen.getByTestId('sidebar-section-run'))
    expect(screen.queryByText('Solver Settings')).toBeNull()
    fireEvent.click(screen.getByTestId('sidebar-section-run'))
    expect(screen.getByText('Solver Settings')).toBeTruthy()
  })

  it('the project is a card in the footer, below the steps', async () => {
    const { container } = await renderSidebar()
    const card = screen.getByTestId('sidebar-project-card')
    const reports = screen.getByTestId('sidebar-group-reports')
    // DOCUMENT_POSITION_FOLLOWING: the card comes after the last step.
    expect(reports.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // Outside the scrolling body, so it stays on screen.
    expect(container.querySelector('.overflow-y-auto')?.contains(card)).toBe(false)
    expect(within(card).getByText('Demo').textContent).toContain('Demo')
    expect(within(card).getByText(/Autosave/)).toBeTruthy()
  })

  it('the project actions sit behind the card menu', async () => {
    await renderSidebar()
    for (const label of PROJECT_MENU_ROWS) expect(screen.queryByText(label)).toBeNull()
    const toggle = screen.getByTestId('sidebar-project-menu')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    const menu = screen.getByTestId('sidebar-project-menu-list')
    for (const label of PROJECT_MENU_ROWS) expect(within(menu).getByText(label)).toBeTruthy()
  })

  it('a menu row acts and closes the menu', async () => {
    await renderSidebar()
    fireEvent.click(screen.getByTestId('sidebar-project-menu'))
    fireEvent.click(screen.getByText('Project info'))
    expect(useUIStore.getState().activeSlidePanel).toBe('overview')
    expect(screen.queryByTestId('sidebar-project-menu-list')).toBeNull()
  })

  it('Escape and a click outside close the menu', async () => {
    await renderSidebar()
    fireEvent.click(screen.getByTestId('sidebar-project-menu'))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('sidebar-project-menu-list')).toBeNull()
    fireEvent.click(screen.getByTestId('sidebar-project-menu'))
    fireEvent.mouseDown(screen.getByTestId('sidebar-group-model'))
    expect(screen.queryByTestId('sidebar-project-menu-list')).toBeNull()
  })
})

describe('M2 — Expert sidebar, icon strip', () => {
  beforeEach(() => useUIStore.setState({ sidebarMode: 'icon' }))

  it('one button per step, in order, and the project button below them', async () => {
    const { container } = await renderSidebar()
    const ids = Array.from(container.querySelectorAll('[data-testid^="sidebar-section-"]'))
      .map(el => el.getAttribute('data-testid')!.replace('sidebar-section-', ''))
    expect(ids).toEqual([...ORDER, 'project'])
  })

  it.each(ORDER)('the %s flyout holds that step', async (id) => {
    await renderSidebar()
    fireEvent.click(screen.getByTestId(`sidebar-section-${id}`))
    await act(async () => { await new Promise(r => setTimeout(r, 10)) })
    const flyout = screen.getByTestId('sidebar-flyout')
    for (const label of ROWS[id]) expect(within(flyout).getByText(label)).toBeTruthy()
  })

  it('the project flyout holds the project actions', async () => {
    await renderSidebar()
    fireEvent.click(screen.getByTestId('sidebar-section-project'))
    await act(async () => { await new Promise(r => setTimeout(r, 10)) })
    const flyout = screen.getByTestId('sidebar-flyout')
    for (const label of PROJECT_MENU_ROWS) expect(within(flyout).getByText(label)).toBeTruthy()
  })
})

describe('flow sections in the sidebar', () => {
  beforeEach(() => useUIStore.setState({ sidebarMode: 'expanded' }))

  it('lists the ten sections, numbered, in flow order', async () => {
    const { container } = await renderSidebar()
    const ids = Array.from(container.querySelectorAll('[data-testid^="flow-section-"]'))
      .map(el => el.getAttribute('data-testid')!.replace('flow-section-', ''))
    expect(ids).toEqual(['project', 'carriers', 'connection', 'loads', 'assets', 'profiles',
      'commercial', 'scenarios', 'readiness', 'results'])
  })

  it('names the last band PROJECT', async () => {
    await renderSidebar()
    expect(within(screen.getByTestId('sidebar-group-reports')).getByText('PROJECT')).toBeTruthy()
  })
})
