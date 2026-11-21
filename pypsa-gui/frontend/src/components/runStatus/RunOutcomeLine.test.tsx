import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RunStrip from './RunStrip'
import { useUIStore } from '../../store/uiStore'
import { WRITABLE } from '../../utils/lockState'
import * as queueHook from '../../hooks/useSolveQueue'
import * as hubData from '../../pages/hubDesign/useHubData'
import * as projectRun from '../../hooks/useProjectRun'
import * as flowNav from '../../layout/flowNavigation'
import { runEstimateApi } from '../../api/runEstimate'
import { outcomeFromJob, outcomeFromStudy, outcomeText, outcomeReason, runFromJob } from '../../utils/runStatus'
import type { SolveJob } from '../../api/solveQueue'
import type { EhStudyPayload } from '../../api/simulation'

const T = 1_700_000_000
function job(over: Partial<SolveJob>): SolveJob {
  return {
    id: 'job-1', project_id: 'P', status: 'running', kind: 'solve', position: null, error: null,
    enqueued_at: T, started_at: T, finished_at: null, ...over,
  } as SolveJob
}

let jobs: SolveJob[] = []
let study: EhStudyPayload | null = null

function setQueue(next: SolveJob[]) {
  jobs = next
  vi.spyOn(queueHook, 'useSolveQueue').mockReturnValue({ data: { jobs, running: [], paused: false } } as never)
  // The strip's run line follows the same queue, as in the app.
  const active = jobs.find(j => j.project_id === 'P' && (j.status === 'running' || j.status === 'queued'))
  vi.spyOn(projectRun, 'useProjectRun').mockReturnValue(active ? runFromJob(active) : null)
}

function renderStrip() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const ui = () => <QueryClientProvider client={qc}><RunStrip /></QueryClientProvider>
  const r = render(ui())
  return { rerender: () => r.rerender(ui()) }
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'P', uiMode: 'guided' })
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
  vi.spyOn(runEstimateApi, 'get').mockResolvedValue({ kind: 'solve', seconds: null, samples: 0, run_ids: [] })
  study = null
  vi.spyOn(hubData, 'useHubStudy').mockImplementation(() => ({ study }) as never)
})
afterEach(() => vi.restoreAllMocks())

describe('outcome words', () => {
  it('names the run and its time, with colons and no raw kind', () => {
    const o = outcomeFromJob(job({ kind: 'campus', status: 'completed', started_at: T, finished_at: T + 370 }))!
    expect(outcomeText(o)).toBe('Campus study finished · 6 min')
    expect(outcomeText(outcomeFromJob(job({ status: 'failed' }))!)).toBe('Optimisation failed')
    expect(outcomeText(outcomeFromJob(job({ status: 'aborted' }))!)).toBe('Optimisation stopped')
    expect(outcomeFromJob(job({ status: 'running' }))).toBeNull()
  })
  it('a failure gives its own reason, or says none was recorded', () => {
    expect(outcomeReason(outcomeFromJob(job({ status: 'failed', error: 'Infeasible model' }))!)).toBe('Infeasible model')
    expect(outcomeReason(outcomeFromJob(job({ status: 'failed', error: '  ' }))!)).toMatch(/^No reason was recorded/)
    expect(outcomeReason(outcomeFromJob(job({ status: 'interrupted' }))!)).toMatch(/app restarted/)
  })
  it('the Guided study says "done" where a job says "completed"', () => {
    const o = outcomeFromStudy({ status: 'done', started_at: T, finished_at: T + 30 } as EhStudyPayload)!
    expect(o.end).toBe('completed')
    expect(outcomeText(o)).toBe('Reliability study finished · under a minute')
    expect(outcomeFromStudy({ status: 'running', started_at: T } as EhStudyPayload)).toBeNull()
    expect(outcomeFromStudy({ status: 'idle' } as EhStudyPayload)).toBeNull()
  })
})

describe('the strip after a run ends', () => {
  it('keeps "Open results" until dismissed, and it never jumps by itself', () => {
    const open = vi.spyOn(flowNav, 'openSectionInGuided')
    setQueue([job({ status: 'running' })])
    const s = renderStrip()
    expect(screen.queryByTestId('run-outcome')).toBeNull()
    setQueue([job({ status: 'completed', finished_at: T + 130 })])
    s.rerender()
    expect(screen.getByTestId('run-outcome-text').textContent).toBe('Optimisation finished · 2 min')
    expect(screen.getByRole('status').textContent).toContain('Optimisation finished')
    expect(open).not.toHaveBeenCalled()
    // Polls later: still there (audit #25's 6 s gap).
    s.rerender(); s.rerender()
    fireEvent.click(screen.getByTestId('run-outcome-open'))
    expect(open).toHaveBeenCalledWith('results')
    expect(useUIStore.getState().uiMode).toBe('guided')
    expect(screen.getByTestId('run-outcome')).toBeTruthy()
    fireEvent.click(screen.getByTestId('run-outcome-dismiss'))
    expect(screen.queryByTestId('run-outcome')).toBeNull()
  })

  it('Expert "Open results" opens the Results section through the return path', () => {
    const open = vi.spyOn(flowNav, 'openFlowTarget')
    useUIStore.setState({ uiMode: 'expert' })
    setQueue([job({ status: 'running' })])
    const s = renderStrip()
    setQueue([job({ status: 'completed', finished_at: T + 30 })])
    s.rerender()
    const link = screen.getByTestId('run-outcome-open')
    expect(link.className.split(' ')).toContain('underline')
    fireEvent.click(link)
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ section: 'results' }))
  })

  it('a run that ended before the tab saw it going on says nothing', () => {
    setQueue([job({ status: 'completed', finished_at: T + 30 })])
    renderStrip()
    expect(screen.queryByTestId('run-outcome')).toBeNull()
  })

  it('another project\'s run never shows here', () => {
    setQueue([job({ project_id: 'Q', status: 'running' })])
    const s = renderStrip()
    setQueue([job({ project_id: 'Q', status: 'completed', finished_at: T + 30 })])
    s.rerender()
    expect(screen.queryByTestId('run-outcome')).toBeNull()
  })

  it('Guided "See why" shows the reason in place; Expert opens the Solve queue', () => {
    setQueue([job({ status: 'running' })])
    const s = renderStrip()
    setQueue([job({ status: 'failed', error: 'Infeasible model' })])
    s.rerender()
    expect(screen.getByTestId('run-outcome').dataset.end).toBe('failed')
    fireEvent.click(screen.getByTestId('run-outcome-why'))
    expect(screen.getByTestId('run-outcome-reason').textContent).toBe('Infeasible model')
    expect(useUIStore.getState().uiMode).toBe('guided')
    act(() => useUIStore.setState({ uiMode: 'expert', activeSlidePanel: null } as never))
    fireEvent.click(screen.getByTestId('run-outcome-why'))
    expect(useUIStore.getState().activeSlidePanel).toBe('solveQueue')
    expect(screen.queryByTestId('run-outcome-reason')).toBeNull()
  })

  it('a stopped run has no link; a new run replaces the line', () => {
    setQueue([job({ status: 'running' })])
    const s = renderStrip()
    setQueue([job({ status: 'aborted' })])
    s.rerender()
    expect(screen.getByTestId('run-outcome-text').textContent).toBe('Optimisation stopped')
    expect(screen.queryByTestId('run-outcome-open')).toBeNull()
    expect(screen.queryByTestId('run-outcome-why')).toBeNull()
    setQueue([job({ status: 'aborted' }), job({ id: 'job-2', status: 'running' })])
    s.rerender()
    expect(screen.queryByTestId('run-outcome')).toBeNull()
  })

  it('the Guided goal study ends the same way', () => {
    setQueue([])
    study = { status: 'running', started_at: T } as EhStudyPayload
    const s = renderStrip()
    study = { status: 'done', started_at: T, finished_at: T + 200 } as EhStudyPayload
    s.rerender()
    expect(screen.getByTestId('run-outcome-text').textContent).toBe('Reliability study finished · 3 min')
  })

  it('switching project clears it', () => {
    setQueue([job({ status: 'running' })])
    const s = renderStrip()
    setQueue([job({ status: 'completed', finished_at: T + 30 })])
    s.rerender()
    expect(screen.getByTestId('run-outcome')).toBeTruthy()
    act(() => useUIStore.setState({ currentProject: 'Other' }))
    s.rerender()
    expect(screen.queryByTestId('run-outcome')).toBeNull()
  })
})
