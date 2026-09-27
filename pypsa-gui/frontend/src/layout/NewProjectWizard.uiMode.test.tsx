// Guided-mode spec §3.4 (G4, literal): the wizard's Templates, From-file and
// Clone success handlers each call noteNewProjectCreated(kind) before any
// navigation. An implicit mode becomes Guided; an explicit choice is kept.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import NewProjectWizard, { type NewProjectTab } from './NewProjectWizard'
import { projectsApi } from '../api/projects'
import { ioApi } from '../api/io'
import { useUIStore, type NewProjectKind } from '../store/uiStore'

vi.mock('../api/projects')
vi.mock('../api/network', () => ({ networkApi: { undoInfo: vi.fn() } }))
vi.mock('../api/gridspine', () => ({ gridspineApi: { createStudy: vi.fn() } }))
vi.mock('../api/io', () => ({ ioApi: { importNetcdf: vi.fn() } }))

const SRC = { id: 'id-src', name: 'src', created_at: '2026-01-01T00:00:00',
  has_solver_config: true, bus_count: 5, snapshot_count: 24, objective: null }

// Records the order of the mode call vs. the navigation it must precede.
let order: string[] = []
function Where() {
  const loc = useLocation()
  if (loc.pathname === '/app') order.push('navigate')
  return null
}

function renderAt(tab: NewProjectTab) {
  window.history.pushState({}, '', '/projects')
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/projects']}>
        <Routes>
          <Route path="*" element={<>
            <Where />
            <NewProjectWizard existingProjects={[SRC] as never} onConfirm={() => {}}
              onClose={() => {}} isPending={false} initialTab={tab} />
          </>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

async function drive(tab: 'template' | 'file' | 'clone') {
  renderAt(tab)
  if (tab === 'template') {
    await userEvent.click(screen.getByRole('button', { name: /IEEE 39-Bus \(New England\)/ }))
  } else if (tab === 'file') {
    const input = document.querySelector('input[type=file]') as HTMLInputElement
    await userEvent.upload(input, new File(['x'], 'grid.nc'))
  } else {
    await userEvent.click(await screen.findByRole('button', { name: /^src/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Clone project' }))
  }
  await waitFor(() => expect(order).toContain('navigate'))
}

const realNote = useUIStore.getState().noteNewProjectCreated
const note = vi.fn((k: NewProjectKind) => { order.push(`note:${k}`); realNote(k) })

beforeEach(() => {
  vi.clearAllMocks()
  order = []
  useUIStore.setState({ openTabs: [], noteNewProjectCreated: note })
  vi.mocked(projectsApi.createFromTemplate).mockResolvedValue(
    { imported: 'IEEE 39-Bus (New England)', summary: { buses: 39 } } as never)
  vi.mocked(projectsApi.list).mockResolvedValue([SRC] as never)
  vi.mocked(ioApi.importNetcdf).mockResolvedValue({ buses: 3 } as never)
  vi.mocked(projectsApi.load).mockResolvedValue({} as never)
  vi.mocked(projectsApi.save).mockImplementation(async (name: string) => ({ saved: name }) as never)
})
afterEach(() => {
  window.history.pushState({}, '', '/')
  useUIStore.setState({ noteNewProjectCreated: realNote })
})

const CASES: Array<['template' | 'file' | 'clone', NewProjectKind]> = [
  ['template', 'template'],
  ['file', 'file'],
  ['clone', 'clone'],
]

describe('NewProjectWizard — new-project rule', () => {
  for (const [tab, kind] of CASES) {
    it(`${tab} tab calls noteNewProjectCreated('${kind}') before navigating`, async () => {
      useUIStore.setState({ uiMode: 'expert', uiModeExplicit: true })
      await drive(tab)
      expect(note).toHaveBeenCalledTimes(1)
      expect(note).toHaveBeenCalledWith(kind)
      expect(order.indexOf(`note:${kind}`)).toBeLessThan(order.indexOf('navigate'))
    })

    it(`${tab} tab: an implicit Expert user lands in Guided`, async () => {
      useUIStore.setState({ uiMode: 'expert', uiModeExplicit: false })
      await drive(tab)
      expect(useUIStore.getState().uiMode).toBe('guided')
    })
  }

  it('an explicit Expert choice survives a template project', async () => {
    useUIStore.setState({ uiMode: 'expert', uiModeExplicit: true })
    await drive('template')
    expect(useUIStore.getState().uiMode).toBe('expert')
  })
})
