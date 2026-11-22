import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import toast from 'react-hot-toast'
import { projectsApi } from '../api/projects'
import Sidebar from './Sidebar'

// project-io 04 Slice B: after a raw file import the network is in no project yet. The
// sidebar says so and offers "Save as project", not "No project loaded".

vi.mock('../api/network', () => ({
  networkApi: {
    getMeta: vi.fn().mockResolvedValue({ bus_count: 3 }),
    undoInfo: vi.fn().mockResolvedValue({ depth: 0 }),
  },
}))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))
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


function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><Sidebar /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({
    sidebarMode: 'expanded', currentProject: null, projectName: 'my_grid', unsavedNetwork: 'my_grid', activeSlidePanel: null,
  })
})

describe('a loaded network that belongs to no project', () => {
  it('reads "Network not saved yet" with Save as project, not "No project loaded"', async () => {
    mount()
    expect(await screen.findByTestId('sidebar-network-unsaved')).toBeTruthy()
    expect(screen.queryByText('No project loaded')).toBeNull()
  })

  it('Save as project saves under the file’s name and opens it', async () => {
    mount()
    fireEvent.click(await screen.findByTestId('sidebar-save-as-project'))
    fireEvent.click(await screen.findByRole('button', { name: 'Save' }))
    await waitFor(() => expect(useUIStore.getState().currentProject).toBe('my_grid'))
    expect(projectsApi.save).toHaveBeenCalledWith('my_grid', false, true, undefined, true)
    expect(useUIStore.getState().unsavedNetwork).toBeNull()
  })

  it('a plain empty sidebar still reads "No project loaded"', async () => {
    useUIStore.setState({ unsavedNetwork: null })
    mount()
    expect(await screen.findByText('No project loaded')).toBeTruthy()
  })
})

describe('Save as project refusals', () => {
  it('a taken name says so in plain words and keeps the network unsaved', async () => {
    vi.mocked(projectsApi.save).mockRejectedValueOnce({ response: { status: 409, data: { detail: "Project 'my_grid' already exists" } } })
    mount()
    fireEvent.click(await screen.findByTestId('sidebar-save-as-project'))
    fireEvent.click(await screen.findByRole('button', { name: 'Save' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('already exists, so nothing was saved')))
    expect(useUIStore.getState().currentProject).toBeNull()
    expect(useUIStore.getState().unsavedNetwork).toBe('my_grid')
  })
})
