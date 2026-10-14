// The `goal_state` frame through the real panel (chat harness issue 17): the
// rounds of a goal arrive on the same stream as the turn that set it, and a
// round starting is marked in the transcript and gets its own bubble.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(),
    getChatHistory: vi.fn().mockResolvedValue({ turns: [], last_session_id: null, bound_project: null }),
    postChatAbort: vi.fn(),
    postChatConfirm: vi.fn().mockResolvedValue({ ok: true }),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: true,
      source: 'env',
      hint: null,
      overridden_by_environment: false,
      storage_path: '/tmp/user.env',
    }),
    putApiKeySettings: vi.fn(),
    deleteApiKeySettings: vi.fn(),
  }
})

vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(),
  getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]),
  uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))

import { createChatStream } from '../api/chat'
import ChatPanel from './ChatPanel'

type Frame = { event: string; data: Record<string, unknown> }

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null, assistantDockOpen: false })
  useChatStore.setState({
    sessionId: null, pending: null, messages: [], goal: null,
    streaming: false, streamCleanup: null,
  })
  vi.mocked(createChatStream).mockClear()
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
}

async function sendFrames(frames: Frame[]) {
  vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
    for (const f of frames) onFrame(f as never)
    return () => {}
  })
  const user = userEvent.setup()
  await user.type(screen.getByTestId('chat-input'), 'hello')
  await user.click(screen.getByTestId('chat-send'))
}

const GOAL = { objective: 'make it solve', max_rounds: 5, phase: 'active', reason: null }

describe('the goal_state frame', () => {
  it('sets the strip, and each round gets a marker and its own bubble', async () => {
    renderPanel()
    await sendFrames([
      { event: 'session_init', data: { session_id: 's1' } },
      { event: 'goal_state', data: { goal: { ...GOAL, rounds_used: 0 } } },
      { event: 'token', data: { delta: 'starting' } },
      { event: 'turn_done', data: {} },
      { event: 'goal_state', data: { goal: { ...GOAL, rounds_used: 1 } } },
      { event: 'session_init', data: { session_id: 's1' } },
      { event: 'token', data: { delta: 'round one' } },
      { event: 'turn_done', data: {} },
    ])
    const tail = useChatStore.getState().messages.slice(-3).map(m => [m.role, m.content])
    expect(tail).toEqual([
      ['assistant', 'starting'],
      ['system', 'Goal: round 1 of 5'],
      ['assistant', 'round one'],
    ])
    expect(screen.getByTestId('chat-goal-rounds').textContent).toBe('round 1 of 5')
  })

  it('a paused or blocked goal adds no marker', async () => {
    renderPanel()
    useChatStore.setState({ goal: { ...GOAL, rounds_used: 2, phase: 'active' } as never })
    await sendFrames([
      { event: 'session_init', data: { session_id: 's1' } },
      { event: 'goal_state', data: { goal: { ...GOAL, rounds_used: 2, phase: 'blocked', reason: 'error' } } },
      { event: 'error', data: { error_kind: 'internal_error', message: 'x' } },
    ])
    expect(useChatStore.getState().messages.some(m => m.role === 'system' && m.content.startsWith('Goal:'))).toBe(false)
    expect(screen.getByTestId('chat-goal-strip').getAttribute('data-phase')).toBe('blocked')
  })
})
