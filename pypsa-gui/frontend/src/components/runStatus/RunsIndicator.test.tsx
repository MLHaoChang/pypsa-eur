import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RunsIndicator } from './RunsIndicator'
import { useUIStore } from '../../store/uiStore'
import { runEstimateApi } from '../../api/runEstimate'
import type { RunView } from '../../utils/runStatus'

let jobs: Array<Record<string, unknown>> = []
vi.mock('../../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs, running: [], paused: false } }),
  activeJobForProject: () => undefined,
}))
let projectRun: RunView | null = null
vi.mock('../../hooks/useProjectRun', () => ({ useProjectRun: () => projectRun }))

const job = (id: string, project: string | null, status = 'running') => ({
  id, project_id: project, project_key: null, kind: 'solve', status, position: status === 'queued' ? 1 : null,
  objective: null, solve_time: null, condition: null, error: null, enqueued_at: 1, started_at: status === 'running' ? Date.now() / 1000 - 30 : null, finished_at: null,
})

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><RunsIndicator /></QueryClientProvider>)
}

beforeEach(() => {
  jobs = []
  projectRun = null
  useUIStore.setState({ currentProject: 'Demo', uiMode: 'expert', activeSlidePanel: null })
  vi.spyOn(runEstimateApi, 'get').mockResolvedValue({ kind: 'solve', seconds: null, samples: 0, run_ids: [] })
})
afterEach(() => vi.restoreAllMocks())

describe('RunsIndicator', () => {
  it('renders nothing while no run is active', () => {
    jobs = [job('a', 'Demo', 'completed')]
    renderIt()
    expect(screen.queryByTestId('runs-indicator')).toBeNull()
  })

  it('says the count in words, not colour alone, and skips runs it cannot see', () => {
    jobs = [job('a', 'Demo'), job('b', 'Other', 'queued'), job('c', null)]
    renderIt()
    expect(screen.getByTestId('runs-indicator').textContent).toBe('2 runs active')
    // Theme tokens, so it follows the app's theme switch, not the OS (UX 24).
    expect(screen.getByTestId('runs-indicator').className).toContain('text-warn')
    expect(screen.getByTestId('runs-indicator').className).not.toMatch(/dark:|amber-/)
  })

  it('counts the Guided study of the project on screen', () => {
    projectRun = { name: 'Reliability study', stage: 'Studying', steps: '', startedAt: 1, jobId: null, queued: false }
    renderIt()
    expect(screen.getByTestId('runs-indicator').textContent).toBe('1 run active')
  })

  it('Expert opens the Solve queue panel', () => {
    jobs = [job('a', 'Demo')]
    renderIt()
    fireEvent.click(screen.getByTestId('runs-indicator'))
    expect(useUIStore.getState().activeSlidePanel).toBe('solveQueue')
    expect(useUIStore.getState().uiMode).toBe('expert')
  })

  it('Guided opens the list in place, never switching mode, and Escape closes it', () => {
    useUIStore.setState({ uiMode: 'guided' })
    jobs = [job('a', 'Demo'), job('b', 'Other', 'queued')]
    renderIt()
    fireEvent.click(screen.getByTestId('runs-indicator'))
    const pop = screen.getByTestId('runs-popover')
    expect(pop.textContent).toContain('Other')
    expect(pop.textContent).toContain('Waiting in queue #1')
    expect(useUIStore.getState().uiMode).toBe('guided')
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('runs-popover')).toBeNull()
  })
})
