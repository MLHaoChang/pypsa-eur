// Guided-mode spec §10 addendum (gate P23 B1): ImportZone with NO current
// project imports a bundle as a new project and opens it — a new project, so
// noteNewProjectCreated('file'). Importing INTO the current project is not.
import { render, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ImportZone } from './ImportExport'
import { useUIStore, type NewProjectKind } from '../store/uiStore'
import { projectsApi } from '../api/projects'
import { networkApi } from '../api/network'

vi.mock('../api/projects')
vi.mock('../api/io')
vi.mock('../api/network')
vi.mock('../utils/projectActions', () => ({ saveProjectQuietly: vi.fn().mockResolvedValue(true) }))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

function renderZone() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ImportZone onSuccess={() => {}} /></QueryClientProvider>)
}
async function pickBundle() {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  await userEvent.upload(input, new File(['x'], 'b.pypsaproj.zip'))
}

let order: string[] = []
const realNote = useUIStore.getState().noteNewProjectCreated
const realSet = useUIStore.getState().setCurrentProject
beforeEach(() => {
  vi.clearAllMocks()
  order = []
  vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0, unsaved: false })
  vi.mocked(projectsApi.importBundle).mockResolvedValue({ imported: 'Bundled', summary: {} } as never)
  useUIStore.setState({
    noteNewProjectCreated: (k: NewProjectKind) => { order.push(`note:${k}`); realNote(k) },
    setCurrentProject: (n: string | null, id?: string | null) => { order.push(`set:${n}`); realSet(n, id) },
  })
})
afterEach(() => useUIStore.setState({ noteNewProjectCreated: realNote, setCurrentProject: realSet }))

describe('ImportZone — new-project rule', () => {
  it("no current project: noteNewProjectCreated('file') before setCurrentProject; implicit → Guided", async () => {
    useUIStore.setState({ currentProject: null, uiMode: 'expert', uiModeExplicit: false })
    renderZone()
    await pickBundle()
    await waitFor(() => expect(order).toContain('set:Bundled'))
    expect(order.indexOf('note:file')).toBeGreaterThanOrEqual(0)
    expect(order.indexOf('note:file')).toBeLessThan(order.indexOf('set:Bundled'))
    expect(useUIStore.getState().uiMode).toBe('guided')
  })

  it('explicit Expert stays Expert', async () => {
    useUIStore.setState({ currentProject: null, uiMode: 'expert', uiModeExplicit: true })
    renderZone()
    await pickBundle()
    await waitFor(() => expect(order).toContain('set:Bundled'))
    expect(useUIStore.getState().uiMode).toBe('expert')
  })
})
