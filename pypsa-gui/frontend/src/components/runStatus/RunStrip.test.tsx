import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RunStrip from './RunStrip'
import { useUIStore } from '../../store/uiStore'
import { WRITABLE } from '../../utils/lockState'
import * as projectRun from '../../hooks/useProjectRun'
import { runEstimateApi } from '../../api/runEstimate'
import type { RunView } from '../../utils/runStatus'

function renderStrip() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><RunStrip /></QueryClientProvider>)
}

const run: RunView = {
  name: 'Optimisation', stage: 'Solving', steps: '', startedAt: Date.now() / 1000 - 30,
  jobId: 'job-1', queued: false,
}

beforeEach(() => {
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
  vi.spyOn(runEstimateApi, 'get').mockResolvedValue({ kind: 'solve', seconds: null, samples: 0, run_ids: [] })
})
afterEach(() => vi.restoreAllMocks())

describe('RunStrip', () => {
  it('takes no space when nothing runs and the project is writable', () => {
    vi.spyOn(projectRun, 'useProjectRun').mockReturnValue(null)
    renderStrip()
    expect(screen.queryByTestId('run-strip')).toBeNull()
  })

  it('shows the run and the read-only line together while the project is solved', () => {
    vi.spyOn(projectRun, 'useProjectRun').mockReturnValue(run)
    useUIStore.getState().setSolvingReadOnly(true)
    renderStrip()
    expect(screen.getByTestId('run-status').getAttribute('title')).toContain('Optimisation · Solving')
    expect(screen.getByTestId('run-strip-readonly').textContent)
      .toBe('Being solved: edits open again when the run ends.')
  })

  it('a queued run or a Guided study shows without the read-only line', () => {
    vi.spyOn(projectRun, 'useProjectRun').mockReturnValue({ ...run, queued: true, startedAt: null, stage: 'Waiting in queue #2' })
    renderStrip()
    expect(screen.getByTestId('run-strip')).toBeTruthy()
    expect(screen.queryByTestId('run-strip-readonly')).toBeNull()
  })

  it('keeps the read-only line when the queue has not caught up with the run', () => {
    vi.spyOn(projectRun, 'useProjectRun').mockReturnValue(null)
    useUIStore.getState().setSolvingReadOnly(true)
    renderStrip()
    expect(screen.getByTestId('run-strip-readonly')).toBeTruthy()
    expect(screen.queryByTestId('run-status')).toBeNull()
  })
})
