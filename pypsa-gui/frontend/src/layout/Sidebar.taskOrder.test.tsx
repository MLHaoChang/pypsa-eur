// M2 (plan 2026-10-05 §11, UX assessment §4.2): the Expert sidebar follows
// the order of the work — build the network, set its time, run it, read the
// results, take it further in a study, write it up — instead of grouping by
// kind (PROJECT / DATA / SIMULATION / STUDIES). The project itself is not a
// step: it moves to a card in the footer, with its actions behind a menu.
// Guided keeps its own sidebar (spec §3.5), pinned by Sidebar.uiMode.test.tsx.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
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

// UX overhaul (owner decision Q6a): the workflow order, named by category —
// MODEL (assets, bounds, time, economics), RUN, RESULTS, STUDIES, PROJECT.
// The PROJECT step keeps the id `reports`; panel ids are unchanged.
const ORDER = ['model', 'run', 'results', 'studies', 'reports']

// What each step holds. Planning → dynamics shows because the mocked project
// list is empty (unknown is not "not a study", Sidebar.studies.test.tsx).
const ROWS: Record<string, string[]> = {
  model: ['Assets', 'Capacity Bounds', 'Model Horizon', 'Time Series', 'Economics'],
  run: ['Solver Settings', 'Issues', 'Solve Queue'],
  results: ['Results'],
  studies: ['Decision study', 'Campus electrical', 'Planning → dynamics'],
  reports: ['Scenarios', 'Snapshots', 'Reports'],
}

// The project's actions, now behind the footer card's menu.
const PROJECT_MENU_ROWS = [
  'Project info', 'Save', 'Duplicate project', 'Export bundle', 'Other', 'Projects home', 'Workspace panel',
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

  it('lists the five steps in the order of the work', async () => {
    const { container } = await renderSidebar()
    const headers = Array.from(container.querySelectorAll('[data-testid^="sidebar-section-"]'))
      .map(el => el.getAttribute('data-testid')!.replace('sidebar-section-', ''))
    expect(headers).toEqual(ORDER)
  })

  it.each(ORDER)('%s holds its rows, in order', async (id) => {
    await renderSidebar()
    expect(rowLabels(screen.getByTestId(`sidebar-group-${id}`))).toEqual(ROWS[id])
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
    expect(within(card).getByTitle('Rename project (header field)').textContent).toContain('Demo')
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

describe('Q6a — studies are labelled by category', () => {
  beforeEach(() => useUIStore.setState({ sidebarMode: 'expanded' }))

  it('files the decision study under Investment and the electrical studies under Grid plan', async () => {
    await renderSidebar()
    const group = screen.getByTestId('sidebar-group-studies')
    const text = group.textContent ?? ''
    expect(text.indexOf('Investment')).toBeLessThan(text.indexOf('Decision study'))
    expect(text.indexOf('Grid plan')).toBeGreaterThan(text.indexOf('Decision study'))
    expect(text.indexOf('Grid plan')).toBeLessThan(text.indexOf('Campus electrical'))
  })

  it('names the last step PROJECT', async () => {
    await renderSidebar()
    expect(within(screen.getByTestId('sidebar-group-reports')).getByText('PROJECT')).toBeTruthy()
  })
})
