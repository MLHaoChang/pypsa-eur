// The `choice_request` frame through the real panel (chat harness issue 15):
// the handler is where the new fields are normalised, so a malformed frame
// degrades to a plain single-pick card instead of a broken one.
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
    sessionId: null, pending: null, messages: [],
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

describe('the choice_request frame', () => {
  it('carries multi_select, detail and intent into the card', async () => {
    renderPanel()
    await sendFrames([
      { event: 'session_init', data: { session_id: 's1' } },
      { event: 'choice_request', data: {
        tool_use_id: 'tu1', title: 'Review the plan', question: 'Go ahead?',
        options: [{ label: 'Approve', recommended: true }, { label: 'Revise' }],
        allow_free_text: true, multi_select: false,
        detail: '# Plan\n1. Add a battery', intent: 'plan_review',
      } },
      { event: 'turn_done', data: {} },
    ])
    const c = useChatStore.getState().choice
    expect(c?.intent).toBe('plan_review')
    expect(c?.detail).toBe('# Plan\n1. Add a battery')
    expect(c?.multi_select).toBe(false)
    expect(screen.getByTestId('chat-choice-card').getAttribute('data-intent')).toBe('plan_review')
  })

  it('normalises a frame from an older backend or a bad value', async () => {
    renderPanel()
    await sendFrames([
      { event: 'session_init', data: { session_id: 's1' } },
      { event: 'choice_request', data: {
        tool_use_id: 'tu2', title: 'Q', question: 'Which?',
        options: [{ label: 'A' }, { label: 'B' }], allow_free_text: true,
        multi_select: 'yes', detail: '   ', intent: 'poll',
      } },
      { event: 'turn_done', data: {} },
    ])
    const c = useChatStore.getState().choice
    expect(c?.multi_select).toBe(false)
    expect(c?.detail).toBeNull()
    expect(c?.intent).toBe('choice')
    expect(screen.queryByTestId('chat-choice-send')).toBeNull()
  })
})
