import { beforeEach, describe, expect, it } from 'vitest'
import { useUIStore } from './uiStore'

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
