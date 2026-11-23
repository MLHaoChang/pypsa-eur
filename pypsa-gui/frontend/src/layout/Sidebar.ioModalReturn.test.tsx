// LC-03 past-run parity (UX ask 2): the Export chooser opened from Results
// (the discussed-run row, Summary, the Expert Project menu or its flyout)
// sits over the Results slide panel. Cancelling or exporting must return to
// Results with the same past run: App.tsx closes the slide panel on a
// mousedown outside it, the sidebar and [data-no-panel-close], and the
// chooser and the flyout render outside the <aside>.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { slidePanelClickShouldClose } from '../utils/slidePanelDismiss'
import Sidebar from './Sidebar'

vi.mock('../api/network', () => ({
  networkApi: {
    getMeta: vi.fn().mockResolvedValue({ bus_count: 3 }),
    undoInfo: vi.fn().mockResolvedValue({ depth: 0 }),
  },
}))
vi.mock('../api/io', () => ({
  ioApi: { resultsWorkbookViews: vi.fn().mockResolvedValue({ views: [], run: null }) },
}))
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

afterEach(() => {
  useUIStore.setState({ activeSlidePanel: null, discussedRun: null, ioModalRequest: null, ioModalFormat: null })
})

const PAST = { runId: 'run-past', section: 'results_capacity' }

function seed(mode: 'guided' | 'expert') {
  useUIStore.setState({
    uiMode: mode, currentProject: 'oj-grid', projectName: 'oj-grid',
    activeSlidePanel: 'results', discussedRun: PAST,
  })
}

describe('Export requests open Results › Hand-off › Exports (R6)', () => {
  for (const mode of ['guided', 'expert'] as const) {
    it(`an export request shows Hand-off › Exports over Results and keeps the past run (${mode})`, async () => {
      seed(mode)
      await renderSidebar()
      act(() => { useUIStore.getState().requestIoModal('export', 'workbook') })
      await act(async () => { await new Promise(r => setTimeout(r, 10)) })
      expect(screen.queryByTestId('io-modal')).toBeNull()
      const s = useUIStore.getState()
      expect(s.activeSlidePanel).toBe('results')
      expect(s.resultsTabRequest).toBe('exports')
      expect(s.ioModalFormat).toBe('workbook')   // carried for the Exports tab to read
      expect(s.ioModalRequest).toBeNull()
      expect(s.discussedRun).toEqual(PAST)
    })
  }

  it('an import request still opens the Import dialog', async () => {
    seed('expert')
    await renderSidebar()
    act(() => { useUIStore.getState().requestIoModal('import') })
    expect(await screen.findByTestId('io-modal')).toBeTruthy()
  })

  it('the Export… row in the Project menu opens Hand-off › Exports, not a dialog', async () => {
    seed('expert')
    await renderSidebar()
    if (!screen.queryByRole('button', { name: 'Export…' })) fireEvent.click(screen.getByTestId('sidebar-project-menu'))
    fireEvent.click(screen.getAllByRole('button', { name: 'Export…' })[0])
    expect(screen.queryByTestId('io-modal')).toBeNull()
    expect(useUIStore.getState().resultsTabRequest).toBe('exports')
  })

  it('keeps Results for a click in the icon strip\'s flyout', async () => {
    seed('expert')
    useUIStore.setState({ sidebarMode: 'icon' })
    await renderSidebar()
    fireEvent.click(screen.getByTestId('sidebar-section-project'))
    const flyout = screen.queryByTestId('sidebar-flyout')
    expect(flyout).not.toBeNull()
    expect(slidePanelClickShouldClose(flyout!, null)).toBe(false)
  })
})
