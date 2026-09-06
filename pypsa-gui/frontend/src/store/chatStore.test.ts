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
        reported: true,
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

// Task 13 — chatStore half of the profile-switching UX. `model: ChatModel`
// (a closed union baked into every request) is replaced by `profileId:
// string | null`, where `null` means "the server's active profile" rather
// than any particular string — sending a selector every turn would re-assert
// a stale choice over an admin's `set_active_profile` or an A8 fallback.
describe('chatStore profile switching (Task 13)', () => {
  beforeEach(() => {
    useChatStore.setState({
      sessionId: null,
      profileId: null,
      suppressHydrationOnce: false,
      messages: [],
      pending: null,
      toolProgress: {},
      usage: {
        input_tokens: 0, output_tokens: 0,
        cache_read_tokens: 0, cache_create_tokens: 0, reported: true,
      },
      streaming: false,
      error: null,
      streamCleanup: null,
    })
  })

  it('defaults profileId to null (server-active)', () => {
    expect(useChatStore.getState().profileId).toBeNull()
  })

  it('setProfileId sets the selector', () => {
    useChatStore.getState().setProfileId('openai-gpt5')
    expect(useChatStore.getState().profileId).toBe('openai-gpt5')
    useChatStore.getState().setProfileId(null)
    expect(useChatStore.getState().profileId).toBeNull()
  })

  it('startNewChat nulls sessionId and clears the visible conversation', () => {
    useChatStore.setState({
      sessionId: 'sess-old',
      messages: [{ id: 'm1', role: 'user', content: 'hi', ts: 1 }],
      pending: {
        tool_use_id: 'tu-1', tool_name: 'x', args: {}, safety_tier: 'write',
        confirmation_token: 'tok', ttl_seconds: 30, expires_at_epoch_ms: 0,
      },
      toolProgress: { 'tu-1': [{ kind: 'PHASE', line: 'x' }] },
      error: { error_kind: 'rate_limited', message: 'slow down' },
      usage: {
        input_tokens: 10, output_tokens: 20,
        cache_read_tokens: 1, cache_create_tokens: 2, reported: true,
      },
    })
    useChatStore.getState().startNewChat()
    const s = useChatStore.getState()
    expect(s.sessionId).toBeNull()
    expect(s.messages).toEqual([])
    expect(s.pending).toBeNull()
    expect(s.toolProgress).toEqual({})
    expect(s.error).toBeNull()
    expect(s.usage).toEqual({
      input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_create_tokens: 0,
      // W-3 — a new chat has measured nothing YET, which is not the same as
      // an endpoint that measured zero. The reset must clear the flag too,
      // or the fresh session would inherit the old one's credibility.
      reported: false,
    })
  })

  // Fix round 1 — a review reproduced this: `startNewChat()` can fire while
  // `sessionId` is ALREADY null (fresh project, no chat.jsonl yet; or a
  // cross-wire pick before the user's first message). The hydration effect
  // used to key its rerun on `sessionId` changing, so a null→null "change"
  // never triggered it, `suppressHydrationOnce` stayed armed, and the NEXT
  // real hydration (a genuine project switch) silently ate the stale flag
  // instead of the new project's real history. `newChatSeq` exists so the
  // effect has something to watch that ALWAYS changes on every call.
  it('startNewChat bumps newChatSeq on every call, even when sessionId is already null', () => {
    useChatStore.setState({ sessionId: null, newChatSeq: 0 })
    useChatStore.getState().startNewChat()
    expect(useChatStore.getState().sessionId).toBeNull()
    expect(useChatStore.getState().newChatSeq).toBe(1)
    // A second call with sessionId STILL null must bump it again — this is
    // exactly the case a sessionId-keyed effect dependency cannot see.
    useChatStore.getState().startNewChat()
    expect(useChatStore.getState().sessionId).toBeNull()
    expect(useChatStore.getState().newChatSeq).toBe(2)
  })

  it('startNewChat arms a ONE-SHOT hydration-suppression flag', () => {
    useChatStore.getState().startNewChat()
    expect(useChatStore.getState().suppressHydrationOnce).toBe(true)
  })

  it('consumeSuppressHydrationOnce reads and clears the flag exactly once', () => {
    useChatStore.getState().startNewChat()
    expect(useChatStore.getState().consumeSuppressHydrationOnce()).toBe(true)
    // Consumed — the flag must not still read true, and a second consume
    // must not report true again (that would suppress the NEXT hydration
    // too, which is a real project switch's history replay).
    expect(useChatStore.getState().suppressHydrationOnce).toBe(false)
    expect(useChatStore.getState().consumeSuppressHydrationOnce()).toBe(false)
  })

  it('consumeSuppressHydrationOnce is false when nothing armed it', () => {
    expect(useChatStore.getState().consumeSuppressHydrationOnce()).toBe(false)
  })

  it('appendThinkingDelta accumulates into the trailing assistant bubble, separate from content', () => {
    const { appendThinkingDelta, appendTokenDelta } = useChatStore.getState()
    appendThinkingDelta('Let me ')
    appendThinkingDelta('think about this.')
    appendTokenDelta('The answer is 42.')
    const msgs = useChatStore.getState().messages
    expect(msgs).toHaveLength(1)
    expect(msgs[0].role).toBe('assistant')
    expect(msgs[0].thinking).toBe('Let me think about this.')
    expect(msgs[0].content).toBe('The answer is 42.')
  })
})

// W-3's STORE half had no coverage. A mutation audit replaced
// `reported: s.usage.reported || (delta.reported ?? false)` with a constant
// `false` — so the flag could never become true and the shipped app would
// permanently read "tokens n/a" — and 100/100 tests across ten usage-touching
// files still passed. The two tests that exist set `usage.reported` via
// `setState` and assert the render branch, which cannot see the accumulator.
describe('usage.reported (W-3)', () => {
  it('a reported delta makes the totals meaningful', () => {
    useChatStore.setState({
      usage: {
        input_tokens: 0, output_tokens: 0,
        cache_read_tokens: 0, cache_create_tokens: 0, reported: false,
      },
    })
    useChatStore.getState().accrueUsage({
      input_tokens: 5, output_tokens: 2,
      cache_read_tokens: 0, cache_create_tokens: 0, reported: true,
    })
    expect(useChatStore.getState().usage.reported).toBe(true)
    expect(useChatStore.getState().usage.input_tokens).toBe(5)
  })

  it('is sticky — a later silent turn does not erase what was measured', () => {
    // An endpoint may report on one turn and not the next. Once anything real
    // has been measured the totals stay meaningful, so the flag must OR, not
    // overwrite.
    useChatStore.setState({
      usage: {
        input_tokens: 5, output_tokens: 2,
        cache_read_tokens: 0, cache_create_tokens: 0, reported: true,
      },
    })
    useChatStore.getState().accrueUsage({
      input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_create_tokens: 0, reported: false,
    })
    expect(useChatStore.getState().usage.reported).toBe(true)
  })

  it('stays false while nothing has ever been reported', () => {
    useChatStore.setState({
      usage: {
        input_tokens: 0, output_tokens: 0,
        cache_read_tokens: 0, cache_create_tokens: 0, reported: false,
      },
    })
    useChatStore.getState().accrueUsage({
      input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_create_tokens: 0, reported: false,
    })
    expect(useChatStore.getState().usage.reported).toBe(false)
  })
})
