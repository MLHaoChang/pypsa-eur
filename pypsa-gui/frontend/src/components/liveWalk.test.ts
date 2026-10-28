import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useUIStore } from '../store/uiStore'
import { resultsEvidenceApi } from '../api/resultsEvidence'
import { manualNavigation } from '../utils/navigationEpoch'
import { keyResultsWalkRequested, openProjectList, openProjectRequested, startKeyResultsWalk } from './liveWalk'
import type { ResultsSourceBinding } from '../utils/resultsSource'
vi.mock('../api/resultsEvidence', async importOriginal => ({
  ...await importOriginal<typeof import('../api/resultsEvidence')>(),
  resultsEvidenceApi: { loadflow: vi.fn(), run: vi.fn() },
}))
const binding: ResultsSourceBinding = { version: 1, project_id: 'project-1', project_name: 'Demo', scope: 'current', source: 'lopf', window_from: 2, window_to: 3, input_sha256: 'a'.repeat(64), data_sha256: 'b'.repeat(64) }
function display(tab: string, source: ResultsSourceBinding) {
  const element = document.createElement('section')
  element.dataset.resultTab = tab
  element.dataset.resultBinding = JSON.stringify(source)
  document.body.replaceChildren(element)
}
const tick = () => new Promise(resolve => setTimeout(resolve, 0))
describe('source-bound results walk', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useUIStore.setState({ currentProject: 'Demo', uiMode: 'expert', discussedRun: null, resultSource: 'lopf', resultsTabRequest: null, assistantDockOpen: false, activeSlidePanel: null })
    vi.mocked(resultsEvidenceApi.loadflow).mockResolvedValue({ status: 'ok', binding })
  })
  afterEach(() => { manualNavigation(); document.body.replaceChildren(); vi.restoreAllMocks() })
  it('recognises a tour and project-open request', () => {
    expect(keyResultsWalkRequested('Walk me through the key results in each tab')).toBe(true)
    expect(keyResultsWalkRequested('hello')).toBe(false)
    expect(openProjectRequested('Open the project')).toBe(true)
  })
  it('waits for matching loaded data and revalidates its exact source before describing it', async () => {
    const onStep = vi.fn()
    const cancel = startKeyResultsWalk({ onStep })
    expect(useUIStore.getState().resultsTabRequest).toBe('loadflow')
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(onStep).not.toHaveBeenCalled()
    display('dispatch', binding)
    await tick()
    expect(resultsEvidenceApi.loadflow).not.toHaveBeenCalled()
    display('loadflow', binding)
    await vi.waitFor(() => expect(onStep).toHaveBeenCalledOnce())
    expect(resultsEvidenceApi.loadflow).toHaveBeenCalledWith('lopf', 2, 3)
    cancel()
  })
  it('preserves Guided mode and does not claim unsupported current tabs', () => {
    useUIStore.setState({ uiMode: 'guided' })
    const onStop = vi.fn()
    startKeyResultsWalk({ onStop })
    expect(useUIStore.getState().uiMode).toBe('guided')
    expect(useUIStore.getState().resultsTabRequest).toBeNull()
    expect(onStop).toHaveBeenCalledWith(expect.stringContaining('Guided'))
  })
  it('manual navigation wins over an in-flight verification', async () => {
    let resolve: (x: unknown) => void = () => {}
    vi.mocked(resultsEvidenceApi.loadflow).mockImplementation(() => new Promise(r => { resolve = r as typeof resolve }))
    const onStep = vi.fn()
    startKeyResultsWalk({ onStep })
    display('loadflow', binding)
    await tick()
    useUIStore.getState().setSlidePanel('issues')
    resolve({ status: 'ok', binding })
    await tick()
    expect(onStep).not.toHaveBeenCalled()
    expect(useUIStore.getState().activeSlidePanel).toBe('issues')
  })
  it('changed data cannot satisfy a stale source check', async () => {
    vi.mocked(resultsEvidenceApi.loadflow).mockResolvedValue({ status: 'ok', binding: { ...binding, data_sha256: 'c'.repeat(64) } })
    const onStep = vi.fn()
    const cancel = startKeyResultsWalk({ onStep })
    display('loadflow', binding)
    await tick()
    expect(onStep).not.toHaveBeenCalled()
    cancel()
  })
  it('retains the run and baseline, filters missing sections and requires explicit Next', async () => {
    useUIStore.setState({ discussedRun: { runId: 'run-old', baselineRunId: 'baseline-old' } })
    const historical = { ...binding, scope: 'historical' as const, run_id: 'run-old', baseline_run_id: 'baseline-old', section: null }
    vi.mocked(resultsEvidenceApi.run).mockImplementation(async (_run, section) => ({ status: 'ok', binding: { ...historical, section: section ?? null }, data: { run_id: 'run-old', status: 'completed', metrics: {}, sections: [{ name: 'loadflow', available: true }, { name: 'dispatch', available: false }] } }))
    const onStep = vi.fn()
    const cancel = startKeyResultsWalk({ onStep })
    await tick()
    expect(useUIStore.getState().resultsTabRequest).toBe('summary')
    display('summary', historical)
    await vi.waitFor(() => expect(onStep).toHaveBeenCalledOnce())
    await tick()
    expect(useUIStore.getState().resultsTabRequest).toBe('summary')
    cancel.next()
    expect(useUIStore.getState().resultsTabRequest).toBe('loadflow')
    display('loadflow', { ...historical, section: 'loadflow' })
    await vi.waitFor(() => expect(onStep).toHaveBeenCalledTimes(2))
    expect(useUIStore.getState().discussedRun).toEqual({ runId: 'run-old', baselineRunId: 'baseline-old' })
    expect(resultsEvidenceApi.run).toHaveBeenLastCalledWith('run-old', 'loadflow', 'baseline-old')
  })
  it("a past run's walk says plainly when access to the project was lost (LC-T08)", async () => {
    useUIStore.setState({ discussedRun: { runId: 'run-old' } })
    const message = 'This project is not available to you: your access was removed or the project was deleted.'
    vi.mocked(resultsEvidenceApi.run).mockRejectedValue(Object.assign(new Error('404'),
      { response: { status: 404, data: { detail: { error_kind: 'project_unavailable', message } } } }))
    const onStop = vi.fn()
    startKeyResultsWalk({ onStop })
    await vi.waitFor(() => expect(onStop).toHaveBeenCalledWith(message))
  })
  it('keeps the unavailable line for any other refusal of a past run', async () => {
    useUIStore.setState({ discussedRun: { runId: 'run-old' } })
    vi.mocked(resultsEvidenceApi.run).mockRejectedValue(new Error('500'))
    const onStop = vi.fn()
    startKeyResultsWalk({ onStop })
    await vi.waitFor(() => expect(onStop).toHaveBeenCalledWith(
      'Retained run evidence unavailable. Current results were not substituted.'))
  })
  it('opens the project list without the console', () => {
    const listener = vi.fn()
    window.addEventListener('chat:open-project-picker', listener, { once: true })
    openProjectList()
    expect(listener).toHaveBeenCalledOnce()
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
  })
})
