import { describe, expect, it, vi, afterEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { useUIStore } from '../../store/uiStore'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RunStatusLine } from './RunStatusLine'
import { runEstimateApi } from '../../api/runEstimate'
import type { RunView } from '../../utils/runStatus'

function renderLine(run: RunView) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <RunStatusLine run={run} />
    </QueryClientProvider>,
  )
}

const running: RunView = {
  name: 'Optimisation', stage: 'Solving', steps: '', startedAt: Date.now() / 1000 - 134,
  jobId: 'job-1', queued: false,
}

afterEach(() => vi.restoreAllMocks())

describe('RunStatusLine', () => {
  it('shows name, stage, elapsed and the estimate with its basis', async () => {
    vi.spyOn(runEstimateApi, 'get').mockResolvedValue({ kind: 'solve', seconds: 400, samples: 3, run_ids: [] })
    renderLine(running)
    expect((await screen.findByTestId('run-status-eta')).textContent).toContain('about 4 min left')
    expect(screen.getByTestId('run-status-elapsed').textContent).toContain('2:1')
    expect(screen.getByTestId('run-status').getAttribute('title'))
      .toContain('Based on your last 3 runs of this project.')
  })

  it('an unknown estimate says so, muted', async () => {
    vi.spyOn(runEstimateApi, 'get').mockResolvedValue({ kind: 'solve', seconds: null, samples: 1, run_ids: [] })
    renderLine(running)
    const eta = await screen.findByTestId('run-status-eta')
    expect(eta.textContent).toContain('first runs: no estimate yet')
    expect(eta.className).toContain('opacity-70')
  })

  it('a failed estimate read leaves elapsed time only', async () => {
    const get = vi.spyOn(runEstimateApi, 'get').mockRejectedValue(new Error('down'))
    renderLine(running)
    await vi.waitFor(() => expect(get).toHaveBeenCalled())
    expect(screen.queryByTestId('run-status-eta')).toBeNull()
    expect(screen.getByTestId('run-status-elapsed')).toBeTruthy()
  })

  it('a queued run asks for no estimate and shows its place', () => {
    const get = vi.spyOn(runEstimateApi, 'get')
    renderLine({ ...running, queued: true, startedAt: null, stage: 'Waiting in queue #2' })
    expect(screen.getByText('· Waiting in queue #2')).toBeTruthy()
    expect(screen.queryByTestId('run-status-elapsed')).toBeNull()
    expect(get).not.toHaveBeenCalled()
  })

  it('announces nothing itself: the clock is not read out (the strip announces the stage)', () => {
    vi.spyOn(runEstimateApi, 'get').mockResolvedValue({ kind: 'solve', seconds: null, samples: 0, run_ids: [] })
    renderLine(running)
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('Expert gets a Log link that opens the Log tab; Guided does not', () => {
    vi.spyOn(runEstimateApi, 'get').mockResolvedValue({ kind: 'solve', seconds: null, samples: 0, run_ids: [] })
    useUIStore.setState({ uiMode: 'expert', bottomTabRequest: null })
    const { unmount } = renderLine(running)
    // UI-COHESION-1: a text link is always underlined and never turns red.
    const log = screen.getByTestId('run-status-log')
    expect(log.className.split(' ')).toContain('underline')
    expect(log.className).not.toMatch(/hover:underline|text-(red|danger|error)/)
    fireEvent.click(log)
    expect(useUIStore.getState().bottomTabRequest).toBe('Log')
    unmount()
    useUIStore.setState({ uiMode: 'guided' })
    renderLine(running)
    expect(screen.queryByTestId('run-status-log')).toBeNull()
  })

  it('a Guided study (no job) has no Log link in Expert either', () => {
    useUIStore.setState({ uiMode: 'expert' })
    renderLine({ ...running, jobId: null })
    expect(screen.queryByTestId('run-status-log')).toBeNull()
  })

  it('drops parts in the agreed order on narrow headers', async () => {
    vi.spyOn(runEstimateApi, 'get').mockResolvedValue({ kind: 'solve', seconds: 400, samples: 3, run_ids: [] })
    renderLine({ ...running, steps: 'step 4 of up to 30' })
    expect((await screen.findByTestId('run-status-eta')).className).toContain('hidden md:inline')
    expect(screen.getByTestId('run-status-steps').className).toContain('hidden sm:inline')
    expect(screen.getByTestId('run-status-elapsed').className).not.toContain('hidden')
  })
})
