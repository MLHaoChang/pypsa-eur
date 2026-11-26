// Walkthrough J4/J5 (UX-NEXT-1): while the Guided goal study ran, the header
// pill still read "Idle". The study is not a queue job, so the pill only saw
// the queue. It now reads the project's run (`useProjectRun`), queue job or
// study, and says "Running…"; the run strip under the header has the details.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AppHeader from './AppHeader'
import { useUIStore } from '../store/uiStore'
import { useSimulationStore } from '../store/simulationStore'
import { WRITABLE } from '../utils/lockState'
import type { RunView } from '../utils/runStatus'
import type { EhStudyPayload } from '../api/simulation'

vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: [], running: [], paused: false } }),
  useEnqueueSolve: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutateAsync: vi.fn(), isPending: false }),
  activeJobForProject: () => undefined,
}))

let projectRun: RunView | null = null
let hubStudy: EhStudyPayload | null = null
vi.mock('../pages/hubDesign/useHubData', () => ({ useHubStudy: () => ({ study: hubStudy }) }))
vi.mock('../hooks/useProjectRun', () => ({ useProjectRun: () => projectRun }))

vi.mock('../auth/AuthModeProvider', () => ({
  useAuthMode: () => ({ ready: true, authEnabled: false, enableAuth: () => {} }),
}))

function renderHeader() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><AppHeader /></MemoryRouter>
    </QueryClientProvider>,
  )
}

const study: RunView = {
  name: 'Reliability study', stage: 'Studying', steps: 'step 3 of up to 30',
  startedAt: 100, jobId: null, queued: false,
}

beforeEach(() => {
  projectRun = null
  hubStudy = null
  useUIStore.setState({ currentProject: 'Demo', projectName: 'Demo', uiMode: 'guided', activeSlidePanel: null })
  useUIStore.getState().setLockState(WRITABLE)
  useSimulationStore.setState({ status: 'idle' })
})

describe('AppHeader status pill with a Guided study', () => {
  it('reads "Running…" while the study runs, not "Idle"', () => {
    projectRun = study
    renderHeader()
    expect(screen.getByText('Running…')).toBeTruthy()
    expect(screen.queryByText('Idle')).toBeNull()
  })

  it('reads "Idle" when nothing runs', () => {
    renderHeader()
    expect(screen.getByText('Idle')).toBeTruthy()
  })

  it('a queue job the poll still shows after the solve ended does not override the solve status', () => {
    projectRun = { ...study, name: 'Optimisation', jobId: 'job-1' }
    useSimulationStore.setState({ status: 'completed' })
    renderHeader()
    expect(screen.getByText('Optimal')).toBeTruthy()
  })
})

describe('AppHeader names the finished Guided study (template-e2e 46)', () => {
  it('shows "Run of <when>" beside the pill once the study has ended', () => {
    hubStudy = { status: 'done', finished_at: Date.UTC(2026, 10, 26, 13, 2) / 1000 }
    renderHeader()
    expect(screen.getByTestId('header-run').textContent).toMatch(/Run of 26 Nov, \d\d:\d\d/)
  })

  it('names no run while it runs or when none has run', () => {
    projectRun = study
    hubStudy = { status: 'running', started_at: 100 }
    renderHeader()
    expect(screen.queryByTestId('header-run')).toBeNull()
  })

  it('names no study run once the user\'s own LOPF solve is the run on screen (study then LOPF)', () => {
    hubStudy = { status: 'done', finished_at: Date.UTC(2026, 10, 26, 13, 2) / 1000 }
    useSimulationStore.setState({ status: 'completed' })
    renderHeader()
    expect(screen.queryByTestId('header-run')).toBeNull()
    expect(screen.getByText('Optimal')).toBeTruthy()
  })

  it('names no run for a failed study', () => {
    hubStudy = { status: 'failed', finished_at: Date.UTC(2026, 10, 26, 13, 2) / 1000 }
    renderHeader()
    expect(screen.queryByTestId('header-run')).toBeNull()
  })
})
