import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import Sidebar from './Sidebar'

// Guided-mode spec §3.5 / §3.9: in Expert mode the Sidebar's DOM is the base
// commit's DOM. The snapshot in __snapshots__/ was recorded from the Sidebar
// BEFORE P23 touched it (same mocks, same store state). The only Expert-visible
// difference P23 is allowed to make is the new `data-testid` attributes listed
// in §3.5, so those — and only those — are stripped before comparing.
//
// The snapshot IS re-recorded when the Expert sidebar legitimately gains an
// entry (the Reports view, PR #64, added one under Planning → dynamics): it
// pins that Guided mode changes nothing in Expert, not that Expert never
// changes. Re-record with `npx vitest run src/layout/Sidebar.expertUnchanged.test.tsx -u`
// and check the diff is exactly the entry that was added.
// The Studies group (2026-10-05) moved Planning → dynamics, Campus electrical
// and Reports out of Simulation under their own header: owner's request.
const NEW_TEST_IDS = /\s?data-testid="(sidebar-section-(project|data|simulation)|sidebar-mode-switcher)"/g
function normalise(html: string): string {
  return html.replace(NEW_TEST_IDS, '')
}

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
  // Let the mocked queries settle so the snapshot is of the steady state.
  await act(async () => { await new Promise(r => setTimeout(r, 20)) })
  await act(async () => { await new Promise(r => setTimeout(r, 20)) })
  return utils
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
    canvasMode: 'select',
    theme: 'dark',
    density: 'comfortable',
    readOnly: false,
    readOnlyReason: 'writable',
    autosaveEnabled: false,
  })
})

describe('Sidebar — Expert unchanged', () => {
  it('expanded sidebar renders the base DOM', async () => {
    useUIStore.setState({ sidebarMode: 'expanded' })
    const { container } = await renderSidebar()
    expect(normalise(container.innerHTML)).toMatchSnapshot()
  })

  it('icon strip renders the base DOM', async () => {
    useUIStore.setState({ sidebarMode: 'icon' })
    const { container } = await renderSidebar()
    expect(normalise(container.innerHTML)).toMatchSnapshot()
  })
})
