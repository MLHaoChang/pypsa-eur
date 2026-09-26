import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import ChatPanel from './ChatPanel'

// P22: another panel seeds the composer (e.g. the EH panel's 'Ask the
// assistant'); the text is shown for the user to send, never auto-sent.
//
// Harness copied from ChatPanel.greeting.test.tsx.
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
      cache_read_tokens: 0, cache_create_tokens: 0, reported: true,
    },
  })
})

afterEach(() => cleanup())


it('places a seeded request in the composer without sending it', async () => {
  const { createChatStream } = await import('../api/chat')
  renderPanel()
  act(() => { useChatStore.getState().seedComposer('Review my latest Energy Hub study') })
  const box = await screen.findByRole('textbox')
  await vi.waitFor(() => expect((box as HTMLTextAreaElement).value)
    .toBe('Review my latest Energy Hub study'))
  expect(useChatStore.getState().composerSeed).toBeNull()      // consumed once
  expect(createChatStream).not.toHaveBeenCalled()              // not auto-sent
})
