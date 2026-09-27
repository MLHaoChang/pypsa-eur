// P24 (guided-mode spec §5.3, §5.9): the wizard's "create from template"
// mutation, lifted so the hub-design Start card creates projects the same
// way. Behaviour is the wizard's, unchanged: G4 `noteNewProjectCreated
// ('template')` first, the project becomes current, the caller's `onCreated`
// (the wizard closes itself there) runs, then the new project's tab is added
// and — off /app — the workbench opens (Obstacle 2, spec §2.6).
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { projectsApi } from '../api/projects'
import { useUIStore } from '../store/uiStore'
import { useCreateFromTemplate } from './useCreateFromTemplate'

vi.mock('../api/projects')
vi.mock('react-hot-toast', () => {
  const t = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() })
  return { default: t }
})

const NAME = 'Data Center Energy Hub'
const calls: string[] = []
let where = ''

function Where() {
  const loc = useLocation()
  where = loc.pathname + loc.search
  return null
}

function wrap(qc: QueryClient, path: string | null) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      {path === null ? children : (
        <MemoryRouter initialEntries={[path]}>
          <Routes><Route path="*" element={<><Where />{children}</>} /></Routes>
        </MemoryRouter>
      )}
    </QueryClientProvider>
  )
}

const real = {
  note: useUIStore.getState().noteNewProjectCreated,
  setCurrentProject: useUIStore.getState().setCurrentProject,
  addTab: useUIStore.getState().addTab,
}

beforeEach(() => {
  vi.clearAllMocks()
  calls.length = 0
  where = ''
  useUIStore.setState({
    openTabs: [],
    noteNewProjectCreated: vi.fn((kind) => { calls.push(`note:${kind}`) }),
    setCurrentProject: vi.fn((p: string | null) => {
      calls.push(`current:${p}`); real.setCurrentProject(p)
    }),
    addTab: vi.fn((n: string) => { calls.push(`tab:${n}`); real.addTab(n) }),
  })
  vi.mocked(projectsApi.createFromTemplate).mockResolvedValue(
    { imported: NAME, summary: { buses: 3 } } as never)
})
afterEach(() => {
  window.history.pushState({}, '', '/')
  useUIStore.setState({ noteNewProjectCreated: real.note,
    setCurrentProject: real.setCurrentProject, addTab: real.addTab })
})

function render(path: string | null, onCreated?: (name: string) => void) {
  if (path) window.history.pushState({}, '', path)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const inv = vi.spyOn(qc, 'invalidateQueries')
  const hook = renderHook(() => useCreateFromTemplate({ onCreated }),
    { wrapper: wrap(qc, path) })
  return { hook, inv }
}

describe('useCreateFromTemplate', () => {
  it('from /projects: note G4 first, set current, onCreated, add tab, navigate', async () => {
    const onCreated = vi.fn((n: string) => { calls.push(`created:${n}`) })
    const { hook, inv } = render('/projects', onCreated)
    act(() => hook.result.current.mutate('eh_datacenter'))
    await waitFor(() => expect(where).toBe('/app?project=Data%20Center%20Energy%20Hub'))
    expect(projectsApi.createFromTemplate).toHaveBeenCalledWith('eh_datacenter')
    expect(calls).toEqual([
      'note:template', `current:${NAME}`, `created:${NAME}`, `tab:${NAME}`])
    expect(useUIStore.getState().projectName).toBe(NAME)
    expect(inv).toHaveBeenCalledWith({ queryKey: ['projects'] })
  })

  it('from /app: adds the tab and does not navigate', async () => {
    const { hook } = render('/app')
    act(() => hook.result.current.mutate('eh_datacenter'))
    await waitFor(() => expect(calls).toContain(`tab:${NAME}`))
    expect(where).toBe('/app')
  })

  it('outside a router: creates and adds the tab, nowhere to navigate', async () => {
    const { hook } = render(null)
    act(() => hook.result.current.mutate('eh_datacenter'))
    await waitFor(() => expect(calls).toContain(`tab:${NAME}`))
    expect(calls[0]).toBe('note:template')
  })

  it('exposes isPending and the template id being created', async () => {
    let resolve: (v: unknown) => void = () => {}
    vi.mocked(projectsApi.createFromTemplate).mockReturnValue(
      new Promise(r => { resolve = r }) as never)
    const { hook } = render('/app')
    act(() => hook.result.current.mutate('eh_microgrid'))
    await waitFor(() => expect(hook.result.current.isPending).toBe(true))
    expect(hook.result.current.variables).toBe('eh_microgrid')
    await act(async () => { resolve({ imported: NAME, summary: { buses: 3 } }) })
    await waitFor(() => expect(hook.result.current.isPending).toBe(false))
  })

  it('a failed create toasts and changes nothing (no G4 note, no tab)', async () => {
    const toast = (await import('react-hot-toast')).default
    vi.mocked(projectsApi.createFromTemplate).mockRejectedValue(new Error('boom'))
    const onCreated = vi.fn()
    const { hook } = render('/projects', onCreated)
    act(() => hook.result.current.mutate('eh_datacenter'))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'Template import failed: boom'))
    expect(calls).toEqual([])
    expect(onCreated).not.toHaveBeenCalled()
    expect(where).toBe('/projects')
  })
})
