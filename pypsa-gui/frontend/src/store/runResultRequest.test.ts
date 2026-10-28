import { beforeEach, describe, expect, it } from 'vitest'
import { useUIStore } from './uiStore'
import { applyUiNavigate } from '../components/uiNavigate'
import { navigationEpoch } from '../utils/navigationEpoch'

describe('requestRunResult', () => {
  beforeEach(() => {
    useUIStore.setState({
      discussedRun: null, resultsTabRequest: null, activeSlidePanel: null,
    })
  })

  it('stores the discussed run and opens Results', () => {
    useUIStore.getState().requestRunResult({
      runId: 'fg-abc', status: 'completed', resultsTab: 'dispatch',
      section: 'summary', mode: 'lopf',
    })
    const s = useUIStore.getState()
    expect(s.discussedRun).toEqual({
      runId: 'fg-abc', status: 'completed', resultsTab: 'dispatch',
      section: 'summary', mode: 'lopf',
    })
    expect(s.activeSlidePanel).toBe('results')
    expect(s.resultsTabRequest).toBe('dispatch')
  })

  it('clears cleanly', () => {
    useUIStore.getState().requestRunResult({ runId: 'r1' })
    useUIStore.getState().clearDiscussedRun()
    expect(useUIStore.getState().discussedRun).toBeNull()
  })

  it('drops the discussed run on project switch', () => {
    useUIStore.getState().requestRunResult({ runId: 'r1', status: 'completed' })
    useUIStore.getState().setCurrentProject('another-project')
    expect(useUIStore.getState().discussedRun).toBeNull()
  })
})

describe('a past run under navigation (LC-T10)', () => {
  beforeEach(() => {
    useUIStore.setState({ discussedRun: null, resultsTabRequest: null, activeSlidePanel: null })
    useUIStore.getState().requestRunResult({ runId: 'fg-old', resultsTab: 'capex', section: 'results_capacity' })
  })

  it("a model turn's Results navigation shows the current results, not the past run", () => {
    applyUiNavigate({ kind: 'navigate', panel_id: 'results', results_tab: 'capex' }, navigationEpoch(), { fromModelTurn: true })
    expect(useUIStore.getState().discussedRun).toBeNull()
    expect(useUIStore.getState().resultsTabRequest).toBe('capex')
  })

  it("a model turn's open_run_result still opens the past run", () => {
    applyUiNavigate({ kind: 'open_run_result', run_id: 'fg-other', results_tab: 'dispatch' }, navigationEpoch(), { fromModelTurn: true })
    expect(useUIStore.getState().discussedRun?.runId).toBe('fg-other')
  })

  it("the live walk's own step keeps the past run it walks", () => {
    applyUiNavigate({ kind: 'navigate', panel_id: 'results', results_tab: 'dispatch' }, navigationEpoch())
    expect(useUIStore.getState().discussedRun?.runId).toBe('fg-old')
  })

  it('a model turn elsewhere leaves the past run alone', () => {
    applyUiNavigate({ kind: 'navigate', panel_id: 'timeseries' }, navigationEpoch(), { fromModelTurn: true })
    expect(useUIStore.getState().discussedRun?.runId).toBe('fg-old')
  })
})
