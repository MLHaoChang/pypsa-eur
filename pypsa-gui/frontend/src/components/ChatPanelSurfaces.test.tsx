import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { getChatHistory } from '../api/chat'
import ChatPanel from './ChatPanel'

// The surfaces a user reads when something has gone wrong, plus the meter
// they read to decide what a conversation is costing.
//
//   * #21 — the confirmation card and the error banner were plain <div>s. A
//     screen reader user got no announcement that a destructive action was
//     waiting on them, and no announcement when a turn failed.
//   * #22 — the countdown reached zero and stopped. Approve stayed live and
//     404'd, so the card taught the user that the timer meant nothing.
//   * #20 — the backend recovers an interrupted turn and reports damaged
//     history records; nothing rendered either, so both were detected,
//     reported, and thrown away by the client.
//   * #12 — cached tokens are billed but were invisible in the meter.

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
    postChatConfirm: vi.fn().mockResolvedValue({ ok: true }),
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

afterEach(() => cleanup())

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
  vi.mocked(getChatHistory).mockResolvedValue({
    turns: [], last_session_id: null, bound_project: null,
    history_gap: 0, pending_turn: null,
  })
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
}

function pendingCard(over: Record<string, unknown> = {}) {
  return {
    tool_use_id: 'tu-1',
    tool_name: 'delete_project',
    args: { name: 'Demo' },
    safety_tier: 'destructive',
    confirmation_token: 'tok-1',
    expires_at_epoch_ms: Date.now() + 300_000,
    ...over,
  }
}

// ── #21 — ARIA roles ────────────────────────────────────────────────────────

it('announces a pending destructive confirmation as an alert dialog', async () => {
  renderPanel()
  act(() => { useChatStore.setState({ pending: pendingCard() as never }) })

  const card = await screen.findByTestId('chat-confirmation-card')

  // A destructive action waiting on the user is the strongest possible
  // reason to interrupt a screen reader — alertdialog is the role that does
  // it AND says the thing is interactive, which `alert` alone does not.
  expect(card.getAttribute('role')).toBe('alertdialog')
  expect(card.getAttribute('aria-modal')).toBe('false')
  // The role is meaningless without a name; the tool being confirmed is it.
  const labelledBy = card.getAttribute('aria-labelledby')
  expect(labelledBy).toBeTruthy()
  expect(document.getElementById(labelledBy!)?.textContent).toContain('delete_project')
})

it('announces an error banner as an alert', async () => {
  renderPanel()
  act(() => {
    useChatStore.setState({
      error: { error_kind: 'rate_limited', message: 'slow down' },
    })
  })

  const banner = await screen.findByTestId('chat-error-banner')
  expect(banner.getAttribute('role')).toBe('alert')
})

// ── #22 — expired confirmation ──────────────────────────────────────────────

it('withdraws the confirmation card once its token has expired', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    renderPanel()
    act(() => {
      useChatStore.setState({
        pending: pendingCard({ expires_at_epoch_ms: Date.now() + 1_000 }) as never,
      })
    })
    expect(await screen.findByTestId('chat-confirmation-card')).toBeTruthy()

    await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })

    // Approve on an expired token 409s. Leaving the button live taught the
    // user the countdown was decorative; the card has to withdraw itself.
    await waitFor(() => {
      expect(screen.queryByTestId('chat-confirmation-card')).toBeNull()
    })
    expect(useChatStore.getState().pending).toBeNull()
    expect(screen.getByTestId('chat-error-banner').textContent)
      .toContain('Confirmation expired')
  } finally {
    vi.useRealTimers()
  }
})

it('leaves a live confirmation card alone while time remains', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    renderPanel()
    act(() => {
      useChatStore.setState({
        pending: pendingCard({ expires_at_epoch_ms: Date.now() + 300_000 }) as never,
      })
    })
    expect(await screen.findByTestId('chat-confirmation-card')).toBeTruthy()

    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })

    expect(screen.queryByTestId('chat-confirmation-card')).not.toBeNull()
    expect(useChatStore.getState().pending).not.toBeNull()
  } finally {
    vi.useRealTimers()
  }
})

// ── #20 — surfacing what the backend recovered ──────────────────────────────

it('tells the user their previous message was never answered', async () => {
  vi.mocked(getChatHistory).mockResolvedValue({
    turns: [], last_session_id: null, bound_project: 'Demo',
    history_gap: 0,
    pending_turn: {
      ts: 1_754_000_000, session_id: 'sess-old',
      model: 'claude-sonnet-4-6', user: 'size the battery',
    },
  } as never)

  renderPanel()

  const notice = await screen.findByTestId('chat-interrupted-turn')
  // The message itself must be shown — it is the thing the user lost, and
  // seeing it is what lets them decide whether to send it again.
  expect(notice.textContent).toContain('size the battery')
})

it('says so when part of the transcript could not be read', async () => {
  vi.mocked(getChatHistory).mockResolvedValue({
    turns: [{
      ts: 1_754_000_000, session_id: 's1', model: 'claude-sonnet-4-6',
      user: 'hello', assistant: [{ type: 'text', text: 'hi' }],
      usage: {
        input_tokens: 1, output_tokens: 1,
        cache_read_tokens: 0, cache_create_tokens: 0,
      },
    }],
    last_session_id: 's1', bound_project: 'Demo',
    history_gap: 3, pending_turn: null,
  } as never)

  renderPanel()

  const banner = await screen.findByTestId('chat-history-gap')
  expect(banner.textContent).toMatch(/3/)
})

it('shows no damage notices on a clean reload', async () => {
  renderPanel()
  await waitFor(() => expect(vi.mocked(getChatHistory)).toHaveBeenCalled())

  expect(screen.queryByTestId('chat-history-gap')).toBeNull()
  expect(screen.queryByTestId('chat-interrupted-turn')).toBeNull()
})

// ── #12 — cache tokens in the meter ─────────────────────────────────────────

it('shows what the cache is doing once it has done something', async () => {
  renderPanel()
  act(() => {
    useChatStore.setState({
      usage: {
        input_tokens: 1_200, output_tokens: 340,
        cache_read_tokens: 48_000, cache_create_tokens: 2_000,
      },
    })
  })

  const meter = await screen.findByTestId('chat-cost-meter')
  expect(meter.textContent).toMatch(/cache/i)
  expect(meter.textContent).toContain('48,000')
})

it('keeps the meter uncluttered before anything is cached', async () => {
  renderPanel()
  act(() => {
    useChatStore.setState({
      usage: {
        input_tokens: 10, output_tokens: 5,
        cache_read_tokens: 0, cache_create_tokens: 0,
      },
    })
  })

  const meter = await screen.findByTestId('chat-cost-meter')
  expect(meter.textContent).not.toMatch(/cache/i)
})
