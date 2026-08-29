// Task 13 — the chat panel's profile-switching UX.
//
// Covers, per the task-13 brief:
//   * the stream request carries `profile_id` only when the store's
//     `profileId` is non-null, and NEVER `model` (task 12 retired the closed
//     model union — sending it would re-assert a stale choice every turn and
//     undo an admin's `set_active_profile` or an A8 rate-limit fallback);
//   * the dropdown renders profile LABELS from `getChatProfiles()`, is
//     disabled while streaming, and — per ADR-0001 (unresolvable data ships
//     as a distinct state, never silently reinterpreted as "empty") — a
//     REFUSED profiles fetch must render visibly and textually differently
//     from a resolved-but-empty list;
//   * picking a profile on a different `wire` than the current selection
//     shows an inline confirm row and only commits on "Switch"; a same-wire
//     pick applies immediately;
//   * `model_fallback` and `thinking` SSE frames, previously dropped by a
//     `default`-less switch, now render.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatStream, getChatHistory, type ChatFrame } from '../api/chat'
import { getChatProfiles, type ChatProfilesPayload } from '../api/llmSettings'
import ChatPanel from './ChatPanel'

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
      configured: true, source: 'settings', hint: '…wxyz',
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

vi.mock('../api/llmSettings', () => ({
  getChatProfiles: vi.fn(),
}))

function profiles(over: Partial<ChatProfilesPayload> = {}): ChatProfilesPayload {
  return {
    profiles: [
      { id: 'anthropic-main', label: 'Claude Sonnet', wire: 'anthropic' },
      { id: 'anthropic-alt', label: 'Claude Opus (careful)', wire: 'anthropic' },
      { id: 'openai-main', label: 'GPT-5', wire: 'openai' },
    ],
    active_profile_id: 'anthropic-main',
    ...over,
  }
}

afterEach(() => cleanup())

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getChatProfiles).mockResolvedValue(profiles())
  // `vi.clearAllMocks()` only resets call history, not a mock's
  // resolved-value implementation — a test that calls `mockResolvedValue`
  // (persistent, not `...Once`) on a shared mock otherwise leaks its payload
  // into every later test that doesn't set its own. Restoring the factory's
  // own default here every time closes that off regardless of what any one
  // test does.
  vi.mocked(getChatHistory).mockResolvedValue({
    turns: [], last_session_id: null, bound_project: null,
    history_gap: 0, pending_turn: null,
  })
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null, assistantDockOpen: false })
  useChatStore.setState({
    sessionId: null, profileId: null, suppressHydrationOnce: false, newChatSeq: 0,
    pending: null, messages: [], streaming: false, streamCleanup: null,
    error: null,
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

async function send(text: string) {
  const user = userEvent.setup()
  await user.type(screen.getByTestId('chat-input'), text)
  await user.click(screen.getByTestId('chat-send'))
}

function lastRequestBody() {
  const calls = vi.mocked(createChatStream).mock.calls
  return calls[calls.length - 1][0]
}

async function scriptFrames(frames: ChatFrame[]) {
  vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
    for (const f of frames) onFrame(f)
    return () => {}
  })
  await send('hello')
}

// ── request body: profile_id only when set, never model ────────────────────

it('omits profile_id and model from the request when profileId is null (server-active)', async () => {
  renderPanel()
  await waitFor(() => expect(getChatProfiles).toHaveBeenCalled())
  await scriptFrames([{ event: 'session_init', data: { session_id: 's1' } }])
  await waitFor(() => expect(createChatStream).toHaveBeenCalled())
  const body = lastRequestBody()
  expect(body).not.toHaveProperty('profile_id')
  expect(body).not.toHaveProperty('model')
})

it('sends profile_id (and never model) once a profile has been picked', async () => {
  useChatStore.setState({ profileId: 'anthropic-alt' })
  renderPanel()
  await waitFor(() => expect(getChatProfiles).toHaveBeenCalled())
  await scriptFrames([{ event: 'session_init', data: { session_id: 's1' } }])
  await waitFor(() => expect(createChatStream).toHaveBeenCalled())
  const body = lastRequestBody()
  expect(body.profile_id).toBe('anthropic-alt')
  expect(body).not.toHaveProperty('model')
})

// ── dropdown: labels, disabled-while-streaming ──────────────────────────────

it('renders profile labels from getChatProfiles, selecting active_profile_id by default', async () => {
  renderPanel()
  const select = await screen.findByTestId('chat-model-select') as HTMLSelectElement
  await waitFor(() => expect(screen.getByText('Claude Sonnet')).toBeTruthy())
  expect(screen.getByText('Claude Opus (careful)')).toBeTruthy()
  expect(screen.getByText('GPT-5')).toBeTruthy()
  expect(select.value).toBe('anthropic-main')
  expect(select.disabled).toBe(false)
})

it('disables the dropdown while streaming', async () => {
  renderPanel()
  await screen.findByText('Claude Sonnet')
  vi.mocked(createChatStream).mockImplementation(() => () => {})
  await send('hello')
  await waitFor(() => expect(
    (screen.getByTestId('chat-model-select') as HTMLSelectElement).disabled,
  ).toBe(true))
})

// ── ADR-0001: a refused fetch must not look like an empty list ─────────────

it('shows a loading placeholder before the profiles query resolves', async () => {
  let resolvePromise: (v: ChatProfilesPayload) => void = () => {}
  vi.mocked(getChatProfiles).mockReturnValue(
    new Promise((resolve) => { resolvePromise = resolve }),
  )
  renderPanel()
  const select = await screen.findByTestId('chat-model-select') as HTMLSelectElement
  expect(select.disabled).toBe(true)
  expect(select.textContent).not.toMatch(/no models configured/i)
  resolvePromise(profiles())
  await waitFor(() => expect(
    (screen.getByTestId('chat-model-select') as HTMLSelectElement).disabled,
  ).toBe(false))
})

it('shows a distinct "could not load" state on a REFUSED profiles fetch — not "no models configured"', async () => {
  vi.mocked(getChatProfiles).mockRejectedValue(new Error('network down'))
  renderPanel()
  // Wait for the settled (error) state specifically — the transient loading
  // placeholder is ALSO disabled, so asserting on `disabled` alone would
  // resolve during loading and race the query's actual settlement.
  await waitFor(() => expect(
    screen.getByTestId('chat-model-select').textContent,
  ).toMatch(/could not load/i))
  const select = screen.getByTestId('chat-model-select') as HTMLSelectElement
  expect(select.disabled).toBe(true)
  expect(select.textContent).not.toMatch(/no models configured/i)
})

it('shows "no models configured" — distinct text — when the fetch succeeds with an empty list', async () => {
  vi.mocked(getChatProfiles).mockResolvedValue({ profiles: [], active_profile_id: '' })
  renderPanel()
  await waitFor(() => expect(
    screen.getByTestId('chat-model-select').textContent,
  ).toMatch(/no models configured/i))
  const select = screen.getByTestId('chat-model-select') as HTMLSelectElement
  expect(select.disabled).toBe(true)
  expect(select.textContent).not.toMatch(/could not load/i)
})

// ── cross-wire vs same-wire switching ───────────────────────────────────────

it('picking a same-wire profile applies immediately, no confirm row', async () => {
  renderPanel()
  await screen.findByText('Claude Sonnet')
  const user = userEvent.setup()
  await user.selectOptions(screen.getByTestId('chat-model-select'), 'anthropic-alt')
  expect(useChatStore.getState().profileId).toBe('anthropic-alt')
  expect(screen.queryByTestId('chat-profile-switch-confirm')).toBeNull()
})

it('picking a cross-wire profile shows an inline confirm row and does not commit until Switch', async () => {
  renderPanel()
  await screen.findByText('Claude Sonnet')
  const user = userEvent.setup()
  await user.selectOptions(screen.getByTestId('chat-model-select'), 'openai-main')
  const confirm = await screen.findByTestId('chat-profile-switch-confirm')
  expect(confirm.textContent).toMatch(/GPT-5/)
  expect(confirm.textContent).toMatch(/starts a new chat/i)
  // Not committed yet.
  expect(useChatStore.getState().profileId).toBeNull()

  await user.click(screen.getByTestId('chat-profile-switch-cancel-btn'))
  expect(screen.queryByTestId('chat-profile-switch-confirm')).toBeNull()
  expect(useChatStore.getState().profileId).toBeNull()
})

it('confirming a cross-wire switch calls setProfileId + startNewChat (nulls sessionId, clears messages)', async () => {
  renderPanel()
  await screen.findByText('Claude Sonnet')
  useChatStore.setState({
    sessionId: 'sess-live',
    messages: [{ id: 'm1', role: 'user', content: 'hi', ts: 1 }],
  })
  const user = userEvent.setup()
  await user.selectOptions(screen.getByTestId('chat-model-select'), 'openai-main')
  await user.click(await screen.findByTestId('chat-profile-switch-confirm-btn'))

  expect(useChatStore.getState().profileId).toBe('openai-main')
  expect(useChatStore.getState().sessionId).toBeNull()
  expect(useChatStore.getState().messages).toEqual([])
  expect(screen.queryByTestId('chat-profile-switch-confirm')).toBeNull()
})

it('a startNewChat from the cross-wire switch suppresses the next chat.jsonl hydration', async () => {
  // `mockResolvedValueOnce`, not `mockResolvedValue`: this test asserts
  // `getChatHistory` is called exactly once total, so a PERSISTENT override
  // here would otherwise leak into every later test in this file that
  // doesn't set its own (`vi.clearAllMocks()` in `beforeEach` clears call
  // history, not a mock's resolved-value implementation) — silently handing
  // them a stale `sess-old` session.
  vi.mocked(getChatHistory).mockResolvedValueOnce({
    turns: [{
      ts: 1, session_id: 'sess-old', model: 'claude-sonnet-5',
      user: 'old question', assistant: [{ type: 'text', text: 'old answer' }],
      usage: { input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_create_tokens: 0 },
    }],
    last_session_id: 'sess-old',
    bound_project: 'Demo',
    history_gap: 0,
    pending_turn: null,
  })
  renderPanel()
  // Initial hydration replays the old turn.
  await waitFor(() => expect(screen.getByText('old question')).toBeTruthy())
  expect(useChatStore.getState().sessionId).toBe('sess-old')
  expect(vi.mocked(getChatHistory)).toHaveBeenCalledTimes(1)

  await screen.findByText('Claude Sonnet')
  const user = userEvent.setup()
  await user.selectOptions(screen.getByTestId('chat-model-select'), 'openai-main')
  await user.click(await screen.findByTestId('chat-profile-switch-confirm-btn'))

  // The old turn must not reappear, and history must not have been re-fetched
  // a second time to repopulate it.
  expect(screen.queryByText('old question')).toBeNull()
  expect(useChatStore.getState().sessionId).toBeNull()
  await waitFor(() => {
    // Give any errant re-hydration a chance to land before asserting it didn't.
  })
  expect(vi.mocked(getChatHistory)).toHaveBeenCalledTimes(1)
})

// ── new handleFrame cases ───────────────────────────────────────────────────

it('renders a model_fallback frame as a system-styled from → to line', async () => {
  renderPanel()
  await screen.findByText('Claude Sonnet')
  await scriptFrames([
    { event: 'session_init', data: { session_id: 's1' } },
    {
      event: 'model_fallback',
      data: {
        from_model: 'claude-opus-5', to_model: 'claude-sonnet-5',
        reason: 'rate_limited', profile_id: 'anthropic-main',
      },
    },
  ])
  const line = await screen.findByText(/claude-opus-5.*claude-sonnet-5/)
  expect(line.textContent).toMatch(/rate limited/i)
})

it('accumulates thinking frames into a collapsible details block', async () => {
  renderPanel()
  await screen.findByText('Claude Sonnet')
  await scriptFrames([
    { event: 'session_init', data: { session_id: 's1' } },
    { event: 'thinking', data: { delta: 'Let me work through the numbers.' } },
    { event: 'token', data: { delta: 'The answer is 42.' } },
  ])
  const details = await screen.findByTestId('chat-thinking-block')
  expect(details.tagName.toLowerCase()).toBe('details')
  expect(details.textContent).toMatch(/Let me work through the numbers\./)
  expect(await screen.findByText('The answer is 42.')).toBeTruthy()
})

// ── Fix round 1 — review-reproduced regressions ─────────────────────────────
//
// An independent review live-reproduced two defects and flagged a product
// gap. All three are covered here.

// (1) CRITICAL — the one-shot suppression flag relied on `sessionId`
// CHANGING to retrigger the hydration effect. `startNewChat()` can fire
// while `sessionId` is ALREADY null (a fresh project with no chat.jsonl, or
// a cross-wire pick before the user's first message) — a null→null
// "change" the effect's dependency array never sees, so the flag stays
// armed and is consumed by the WRONG hydration: the next genuine project
// switch, silently eating that project's real history.
it('a startNewChat with an already-null sessionId does not swallow the NEXT project\'s real history (regression)', async () => {
  // ProjectA: no history at all — sessionId never leaves null.
  vi.mocked(getChatHistory).mockResolvedValueOnce({
    turns: [], last_session_id: null, bound_project: 'ProjectA',
    history_gap: 0, pending_turn: null,
  })
  useUIStore.setState({ currentProject: 'ProjectA', activeSlidePanel: null, assistantDockOpen: false })
  renderPanel()
  await screen.findByText('Claude Sonnet')
  await waitFor(() => expect(vi.mocked(getChatHistory)).toHaveBeenCalledTimes(1))
  expect(useChatStore.getState().sessionId).toBeNull()

  // Cross-wire pick + confirm while sessionId is ALREADY null — this is the
  // exact condition a sessionId-keyed effect dependency cannot observe.
  const user = userEvent.setup()
  await user.selectOptions(screen.getByTestId('chat-model-select'), 'openai-main')
  await user.click(await screen.findByTestId('chat-profile-switch-confirm-btn'))
  expect(useChatStore.getState().sessionId).toBeNull()
  expect(useChatStore.getState().profileId).toBe('openai-main')

  // Genuine project switch to ProjectB, which HAS real history.
  vi.mocked(getChatHistory).mockResolvedValueOnce({
    turns: [{
      ts: 1, session_id: 'sess-b', model: 'claude-sonnet-5',
      user: 'projectB question', assistant: [{ type: 'text', text: 'projectB answer' }],
      usage: { input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_create_tokens: 0 },
    }],
    last_session_id: 'sess-b',
    bound_project: 'ProjectB',
    history_gap: 0,
    pending_turn: null,
  })
  act(() => {
    useUIStore.setState({ currentProject: 'ProjectB' })
  })

  // ProjectB's history must load — the earlier startNewChat's suppression
  // flag must NOT still be armed by the time this runs.
  await waitFor(() => expect(screen.getByText('projectB question')).toBeTruthy())
  expect(useChatStore.getState().sessionId).toBe('sess-b')
})

// (2) IMPORTANT — a stale/deleted profileId (e.g. an admin removed the
// profile the user had explicitly picked) resolves to an UNKNOWN current
// wire. The old guard `selectedProfileMeta && meta.wire !== target.wire`
// short-circuits to `false` on an unknown baseline, applying the pick
// directly — no confirm, no startNewChat — silently changing the wire
// under a session that continues believing it's on the old provider.
it('treats a pick as cross-wire (fail-safe) when the current profileId no longer exists in the fetched list', async () => {
  useChatStore.setState({ profileId: 'deleted-anthropic-profile' })
  renderPanel()
  await screen.findByText('Claude Sonnet')
  const user = userEvent.setup()
  await user.selectOptions(screen.getByTestId('chat-model-select'), 'openai-main')

  const confirm = await screen.findByTestId('chat-profile-switch-confirm')
  expect(confirm.textContent).toMatch(/GPT-5/)
  // Not committed — same discipline as any other cross-wire pick.
  expect(useChatStore.getState().profileId).toBe('deleted-anthropic-profile')
  expect(useChatStore.getState().sessionId).toBeNull()
})

// (3) Product gap — `startNewChat()` was reachable ONLY from the cross-wire
// confirm. A deployment where every profile shares one wire had no way at
// all to start a fresh session short of switching projects.
it('a "New chat" control starts a fresh conversation (nulls sessionId, clears messages)', async () => {
  renderPanel()
  await screen.findByText('Claude Sonnet')
  useChatStore.setState({
    sessionId: 'sess-live',
    messages: [{ id: 'm1', role: 'user', content: 'hi', ts: 1 }],
  })
  const user = userEvent.setup()
  await user.click(screen.getByTestId('chat-new-chat'))
  expect(useChatStore.getState().sessionId).toBeNull()
  expect(useChatStore.getState().messages).toEqual([])
})
