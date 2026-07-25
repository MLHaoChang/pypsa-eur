import { beforeEach, describe, expect, it } from 'vitest'
import { useChatStore } from './chatStore'

describe('chatStore Track A (A1 tool_progress)', () => {
  beforeEach(() => {
    useChatStore.setState({
      sessionId: null,
      messages: [],
      pending: null,
      toolProgress: {},
      usage: {
        input_tokens: 0,
        output_tokens: 0,
        cache_read_tokens: 0,
        cache_create_tokens: 0,
      },
      streaming: false,
      error: null,
      streamCleanup: null,
      uploads: [],
      attachedFileIds: [],
      unseenExportCount: 0,
      uploadBatches: {},
    })
  })

  it('A1: appends progress lines keyed by tool_use_id', () => {
    const { appendToolProgress } = useChatStore.getState()
    appendToolProgress('tu-1', { kind: 'PHASE', line: 'building' })
    appendToolProgress('tu-1', { kind: 'PHASE', line: 'solving' })
    const lines = useChatStore.getState().toolProgress['tu-1']
    expect(lines).toHaveLength(2)
    expect(lines[1]).toEqual({ kind: 'PHASE', line: 'solving' })
  })

  it('A1: caps retained progress at 500 newest lines', () => {
    const { appendToolProgress } = useChatStore.getState()
    for (let i = 0; i < 520; i++) {
      appendToolProgress('tu-cap', { kind: 'TRACE', line: `L${i}` })
    }
    const lines = useChatStore.getState().toolProgress['tu-cap']
    expect(lines).toHaveLength(500)
    expect(lines[0].line).toBe('L20')
    expect(lines[499].line).toBe('L519')
  })

  it('A1: resetForProjectSwitch clears toolProgress', () => {
    useChatStore.getState().appendToolProgress('tu-x', { kind: 'PHASE', line: 'x' })
    useChatStore.getState().resetForProjectSwitch()
    expect(useChatStore.getState().toolProgress).toEqual({})
  })
})
