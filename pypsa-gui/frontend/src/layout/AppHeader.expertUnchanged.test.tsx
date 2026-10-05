import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AppHeader from './AppHeader'
import { useUIStore } from '../store/uiStore'
import { WRITABLE } from '../utils/lockState'

// Guided-mode spec §3.3 / §3.9: in Expert mode the header is the base
// commit's header plus exactly one new element, the `ui-mode-switch`
// segmented control (it renders in both modes so the user can always switch
// back). The snapshot was recorded before P23 touched AppHeader.tsx; the
// switch is removed from a clone before comparing, so any OTHER difference —
// a moved button, a changed class, a lost tooltip — fails here.

vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: [], running: [], paused: false } }),
  useEnqueueSolve: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutateAsync: vi.fn(), isPending: false }),
  activeJobForProject: () => undefined,
}))
vi.mock('../api/network', () => ({
  networkApi: {
    getBuses: vi.fn().mockResolvedValue([]),
    getLines: vi.fn().mockResolvedValue([]),
    getGenerators: vi.fn().mockResolvedValue([]),
    getStorageUnits: vi.fn().mockResolvedValue([]),
    getLoads: vi.fn().mockResolvedValue([]),
    getLinks: vi.fn().mockResolvedValue([]),
    getMeta: vi.fn().mockResolvedValue({ bus_count: 0 }),
    undoInfo: vi.fn().mockResolvedValue({ depth: 0 }),
  },
}))
vi.mock('../api/simulation', async (orig) => ({
  ...(await orig<typeof import('../api/simulation')>()),
  simulationApi: {
    getSolverConfig: vi.fn().mockResolvedValue({}),
    getStatus: vi.fn().mockResolvedValue({ running: false }),
  },
  createLogStream: vi.fn(() => () => {}),
}))
vi.mock('../api/projects', () => ({ projectsApi: { list: vi.fn().mockResolvedValue([]) } }))
vi.mock('../auth/AuthModeProvider', () => ({
  useAuthMode: () => ({ ready: true, authEnabled: false, enableAuth: () => {} }),
}))

async function renderHeader() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><AppHeader /></MemoryRouter>
    </QueryClientProvider>,
  )
  await act(async () => { await new Promise(r => setTimeout(r, 20)) })
  await act(async () => { await new Promise(r => setTimeout(r, 20)) })
  return utils
}

function withoutSwitch(container: HTMLElement): string {
  const clone = container.cloneNode(true) as HTMLElement
  clone.querySelector('[data-testid="ui-mode-switch"]')?.remove()
  return clone.innerHTML
}

beforeEach(() => {
  useUIStore.setState({ uiMode: 'expert', currentProject: 'demo', projectName: 'demo', rightPanelOpen: true })
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
})

describe('AppHeader — Expert unchanged', () => {
  it('renders the base header apart from the mode switch', async () => {
    const { container } = await renderHeader()
    expect(withoutSwitch(container)).toMatchSnapshot()
  })
})
