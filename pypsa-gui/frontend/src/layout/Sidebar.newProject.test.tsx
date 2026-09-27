import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import Sidebar from './Sidebar'

// Guided-mode spec §3.4 (G4, literal): Sidebar.newProjectMut's success
// handler calls noteNewProjectCreated('blank') before anything else, so an
// implicit mode starts the new project in Guided and an explicit choice stays.

vi.mock('../api/network', () => ({
  networkApi: {
    getMeta: vi.fn().mockResolvedValue({ bus_count: 3 }),
    undoInfo: vi.fn().mockResolvedValue({ depth: 0 }),
  },
}))
vi.mock('../api/io', () => ({ ioApi: {} }))
vi.mock('../api/projects', () => ({
  projectsApi: {
    list: vi.fn().mockResolvedValue([]),
    save: vi.fn().mockResolvedValue({ saved: 'Fresh' }),
    importBundle: vi.fn().mockResolvedValue({ imported: 'Bundled', summary: {} }),
  },
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
// The wizard's Blank tab calls onConfirm(name) — that is the only thing the
// Sidebar hands it, so the stub exposes exactly that.
vi.mock('./NewProjectWizard', () => ({
  default: ({ onConfirm }: { onConfirm: (name: string) => void }) => (
    <button data-testid="wizard-confirm" onClick={() => onConfirm('Fresh')}>create</button>
  ),
}))
vi.mock('../components/ProjectPicker', () => ({ default: () => <div /> }))
vi.mock('../utils/projectActions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/projectActions')>()
  return {
    ...actual,
    invalidateNetworkQueries: vi.fn(),
    saveProjectQuietly: vi.fn().mockResolvedValue(undefined),
    resetBackendNetwork: vi.fn().mockResolvedValue(undefined),
    downloadProjectBundle: vi.fn(),
    abortRunningSim: vi.fn().mockResolvedValue(true),
    switchToProject: vi.fn(),
  }
})

async function createViaSidebar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  // The same bridge the assistant's primer uses to open the wizard.
  act(() => { window.dispatchEvent(new CustomEvent('chat:open-new-project-wizard')) })
  fireEvent.click(await screen.findByTestId('wizard-confirm'))
  await waitFor(() => expect(useUIStore.getState().currentProject).toBe('Fresh'))
}

beforeEach(() => {
  useUIStore.setState({
    sidebarMode: 'expanded',
    currentProject: 'Demo',
    projectName: 'Demo',
    activeSlidePanel: null,
  })
})

describe('Sidebar.newProjectMut — new-project rule', () => {
  it("calls noteNewProjectCreated('blank') before setting the current project", async () => {
    useUIStore.setState({ uiMode: 'expert', uiModeExplicit: false })
    const order: string[] = []
    const realNote = useUIStore.getState().noteNewProjectCreated
    const realSet = useUIStore.getState().setCurrentProject
    const note = vi.fn((k: Parameters<typeof realNote>[0]) => { order.push(`note:${k}`); realNote(k) })
    const setCurrent = vi.fn((n: string | null, id?: string | null) => { order.push(`set:${n}`); realSet(n, id) })
    useUIStore.setState({ noteNewProjectCreated: note, setCurrentProject: setCurrent })
    try {
      await createViaSidebar()
      expect(note).toHaveBeenCalledWith('blank')
      expect(order.indexOf('note:blank')).toBeLessThan(order.indexOf('set:Fresh'))
    } finally {
      useUIStore.setState({ noteNewProjectCreated: realNote, setCurrentProject: realSet })
    }
  })

  it('an implicit Expert user lands the new project in Guided', async () => {
    useUIStore.setState({ uiMode: 'expert', uiModeExplicit: false })
    await createViaSidebar()
    expect(useUIStore.getState().uiMode).toBe('guided')
  })

  it('an explicit Expert choice stays Expert', async () => {
    useUIStore.setState({ uiMode: 'expert', uiModeExplicit: true })
    await createViaSidebar()
    expect(useUIStore.getState().uiMode).toBe('expert')
  })
})

// §10 addendum (gate P23 B1): Sidebar "Open project → Browse for a project
// file" imports the bundle as a FRESH project (importBundle with no target),
// so it is a new project too — kind 'file'.
async function openFromFileViaSidebar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  act(() => { window.dispatchEvent(new CustomEvent('chat:open-project-picker')) })
  await screen.findByText('Browse for a project file (.pypsaproj.zip)')
  const input = document.querySelector('input[accept=".pypsaproj.zip,.zip"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [new File(['x'], 'b.pypsaproj.zip')] } })
  await waitFor(() => expect(useUIStore.getState().currentProject).toBe('Bundled'))
}

describe('Sidebar "Open from file" — new-project rule', () => {
  it("calls noteNewProjectCreated('file') before setting the current project", async () => {
    useUIStore.setState({ uiMode: 'expert', uiModeExplicit: true })
    const order: string[] = []
    const realNote = useUIStore.getState().noteNewProjectCreated
    const realSet = useUIStore.getState().setCurrentProject
    useUIStore.setState({
      noteNewProjectCreated: (k) => { order.push(`note:${k}`); realNote(k) },
      setCurrentProject: (n, id) => { order.push(`set:${n}`); realSet(n, id) },
    })
    try {
      await openFromFileViaSidebar()
      expect(order.indexOf('note:file')).toBeGreaterThanOrEqual(0)
      expect(order.indexOf('note:file')).toBeLessThan(order.indexOf('set:Bundled'))
      expect(useUIStore.getState().uiMode).toBe('expert')
    } finally {
      useUIStore.setState({ noteNewProjectCreated: realNote, setCurrentProject: realSet })
    }
  })

  it('an implicit Expert user lands the opened bundle in Guided', async () => {
    useUIStore.setState({ uiMode: 'expert', uiModeExplicit: false })
    await openFromFileViaSidebar()
    expect(useUIStore.getState().uiMode).toBe('guided')
  })
})
