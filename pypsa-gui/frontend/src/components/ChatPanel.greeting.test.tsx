import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import ChatPanel from './ChatPanel'

// Where the launch orientation is mounted, and when it stands down.
//
// Its content is covered by ChatLaunchGreeting.test.tsx. What is left — and
// what that suite cannot see, because it renders the greeting directly — is
// the condition ChatPanel puts it behind.
//
// The old `ChatEmptyState` was gated on `!currentProject && messages.length
// === 0`, so the greeting it replaces was invisible in exactly the case the
// spec cares most about: a project IS open and the assistant should already
// know its name, size and solve status. Widening that gate is the wiring.
//
// The second test is the one that stops the fix becoming a nuisance. A
// greeting that stays on screen under a live conversation is a permanent
// header repeating what the user has moved past.

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(),
    getChatHistory: vi.fn().mockResolvedValue({
      turns: [], last_session_id: null, bound_project: null,
      history_gap: 0, pending_turn: null,
    }),
    postChatAbort: vi.fn(),
    postChatConfirm: vi.fn(),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: true, source: 'env', hint: 'abcd',
      overridden_by_environment: false, storage_path: '/tmp/user.env',
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

vi.mock('../api/network', () => ({
  networkApi: {
    getMeta: vi.fn().mockResolvedValue({ name: 'Demo', bus_count: 9, snapshot_count: 24 }),
  },
}))
vi.mock('../api/simulation', () => ({
  simulationApi: {
    getStatus: vi.fn().mockResolvedValue({
      running: false, status: 'completed', condition: 'optimal',
      objective: 1, solve_time: 1, dispatch: 'fresh',
    }),
  },
}))

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null })
  useChatStore.setState({
    sessionId: 'sess-1', pending: null, messages: [], error: null,
    streaming: false, streamCleanup: null,
    usage: {
      input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_create_tokens: 0,
    },
  })
})

afterEach(() => cleanup())

it('greets you with a project open, which is the case the spec is about', async () => {
  renderPanel()

  const g = await screen.findByTestId('chat-launch-greeting')
  expect(g.textContent).toContain('Demo')
})

it('greets you with no project open too', async () => {
  useUIStore.setState({ currentProject: null })
  renderPanel()

  const g = await screen.findByTestId('chat-launch-greeting')
  expect(g.textContent).toMatch(/no project/i)
})

it('stands down once the conversation has started', async () => {
  renderPanel()
  await screen.findByTestId('chat-launch-greeting')

  act(() => {
    useChatStore.setState({
      messages: [{ id: 'm1', role: 'user', content: 'size the battery' }] as never,
    })
  })

  expect(screen.queryByTestId('chat-launch-greeting')).toBeNull()
})
