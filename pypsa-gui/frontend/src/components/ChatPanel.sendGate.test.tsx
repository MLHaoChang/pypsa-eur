// Obstacle 9 (guided-mode spec §2.8): with no API key for the active profile,
// Send stayed enabled and every message came back as a red "API key missing"
// error. ChatPanel now reads `GET /api/chat/health` under the shared
// `['chat','health']` key: `chat_ready === false` disables Send and
// Enter-to-send and shows the key form inline; an unknown readiness (probe
// failed, older backend) keeps Send enabled — a probe outage must not lock
// the assistant.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatStream, getChatHealth, getChatHistory, putApiKeySettings } from '../api/chat'
import { getChatProfiles } from '../api/llmSettings'
import ChatPanel from './ChatPanel'

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(() => () => {}),
    getChatHistory: vi.fn().mockResolvedValue({ turns: [], last_session_id: null, bound_project: null }),
    postChatAbort: vi.fn(),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: false, source: null, hint: null,
      overridden_by_environment: false, storage_path: '/tmp/user.env',
    }),
    putApiKeySettings: vi.fn().mockResolvedValue({
      configured: true, source: 'settings', hint: '…wxyz',
      overridden_by_environment: false, storage_path: '/tmp/user.env',
    }),
    deleteApiKeySettings: vi.fn(),
    getChatHealth: vi.fn(),
  }
})
vi.mock('../api/llmSettings', () => ({ getChatProfiles: vi.fn() }))
vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(),
  getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]),
  uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))

function health(chat_ready: boolean | undefined) {
  return {
    ok: true, anthropic_api_key_present: chat_ready === true,
    default_model: 'default', confirmation_ttl_seconds: 300,
    active_profile: { id: 'anthropic-sonnet', label: 'Default', wire: 'anthropic' },
    ...(chat_ready === undefined ? {} : { chat_ready }),
  } as never
}

let client: QueryClient
function renderPanel() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ChatPanel /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null, assistantDockOpen: true })
  useChatStore.setState({
    sessionId: null, pending: null, messages: [], streaming: false, streamCleanup: null,
    error: null,
  })
  vi.mocked(createChatStream).mockClear()
  vi.mocked(getChatHealth).mockReset()
  vi.mocked(getChatProfiles).mockReset().mockResolvedValue({
    active_profile_id: 'anthropic-sonnet',
    profiles: [
      { id: 'anthropic-sonnet', label: 'Default', wire: 'anthropic' },
      { id: 'local-llm', label: 'Local LLM (auth none)', wire: 'openai' },
    ],
  } as never)
})
afterEach(() => { cleanup(); useChatStore.setState({ profileId: null }) })

async function typeHello() {
  const user = userEvent.setup()
  await user.type(screen.getByTestId('chat-input'), 'hello')
  return user
}

describe('Send gate (obstacle 9)', () => {
  it('chat_ready:false → Send disabled with the hint, Enter does not send, key form inline', async () => {
    vi.mocked(getChatHealth).mockResolvedValue(health(false))
    renderPanel()
    await waitFor(() => expect(getChatHealth).toHaveBeenCalled())
    const user = await typeHello()
    const send = screen.getByTestId('chat-send') as HTMLButtonElement
    await waitFor(() => expect(send.disabled).toBe(true))
    expect(send.title).toBe('Add an API key first (Settings → Assistant)')
    await user.keyboard('{Enter}')
    await user.click(send)
    expect(createChatStream).not.toHaveBeenCalled()
    const gate = screen.getByTestId('chat-send-gate')
    expect(gate.querySelector('[data-testid="chat-api-key-setup"]')).toBeTruthy()
  })

  it('chat_ready:true → Send enabled and Enter sends', async () => {
    vi.mocked(getChatHealth).mockResolvedValue(health(true))
    renderPanel()
    await waitFor(() => expect(getChatHealth).toHaveBeenCalled())
    const user = await typeHello()
    expect((screen.getByTestId('chat-send') as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByTestId('chat-send-gate')).toBeNull()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(createChatStream).toHaveBeenCalledTimes(1))
  })

  it('chat_ready undefined (older backend) → enabled', async () => {
    vi.mocked(getChatHealth).mockResolvedValue(health(undefined))
    renderPanel()
    await waitFor(() => expect(getChatHealth).toHaveBeenCalled())
    await typeHello()
    expect((screen.getByTestId('chat-send') as HTMLButtonElement).disabled).toBe(false)
  })

  it('probe failure → enabled (fail-open)', async () => {
    vi.mocked(getChatHealth).mockRejectedValue(new Error('network down'))
    renderPanel()
    await waitFor(() => expect(getChatHealth).toHaveBeenCalled())
    await typeHello()
    expect((screen.getByTestId('chat-send') as HTMLButtonElement).disabled).toBe(false)
  })

  it("invalidating ['chat','health'] after the key is added re-enables Send", async () => {
    vi.mocked(getChatHealth).mockResolvedValue(health(false))
    renderPanel()
    await typeHello()
    const send = screen.getByTestId('chat-send') as HTMLButtonElement
    await waitFor(() => expect(send.disabled).toBe(true))
    vi.mocked(getChatHealth).mockResolvedValue(health(true))
    await act(async () => { await client.invalidateQueries({ queryKey: ['chat', 'health'] }) })
    await waitFor(() => expect(send.disabled).toBe(false))
  })

  it('saving a key in the inline form refreshes health and re-enables Send', async () => {
    vi.mocked(getChatHealth).mockResolvedValue(health(false))
    renderPanel()
    const user = await typeHello()
    const send = screen.getByTestId('chat-send') as HTMLButtonElement
    await waitFor(() => expect(send.disabled).toBe(true))
    vi.mocked(getChatHealth).mockResolvedValue(health(true))
    await user.type(await screen.findByTestId('chat-api-key-input'), 'sk-test-key')
    await user.click(screen.getByTestId('chat-api-key-save'))
    await waitFor(() => expect(putApiKeySettings).toHaveBeenCalled())
    await waitFor(() => expect(send.disabled).toBe(false))
  })
})

// QA gate B1 (P22.9-FE): `chat_ready` describes the instance's ACTIVE profile,
// but a turn runs on the session's picked `profile_id` when one is named
// (routers/chat.py binds `body.profile_id`; only an unnamed pick follows the
// active profile). A user without an Anthropic key who picked a working local
// profile must not be locked out.
describe('Send gate follows the session profile (gate B1)', () => {
  it("picked a ready non-active profile → Send stays enabled (the reviewer's repro)", async () => {
    useChatStore.setState({ profileId: 'local-llm' })
    vi.mocked(getChatHealth).mockResolvedValue(health(false))
    renderPanel()
    await waitFor(() => expect(getChatHealth).toHaveBeenCalled())
    await waitFor(() => expect(
      (screen.getByTestId('chat-model-select') as HTMLSelectElement).value).toBe('local-llm'))
    const user = await typeHello()
    await new Promise(r => setTimeout(r, 100))
    const send = screen.getByTestId('chat-send') as HTMLButtonElement
    expect(send.disabled).toBe(false)
    expect(screen.queryByTestId('chat-send-gate')).toBeNull()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(createChatStream).toHaveBeenCalledTimes(1))
    expect(vi.mocked(createChatStream).mock.calls[0][0]).toMatchObject({ profile_id: 'local-llm' })
  })

  it('explicitly picked the active (not ready) profile → still gated', async () => {
    useChatStore.setState({ profileId: 'anthropic-sonnet' })
    vi.mocked(getChatHealth).mockResolvedValue(health(false))
    renderPanel()
    await typeHello()
    await waitFor(() => expect((screen.getByTestId('chat-send') as HTMLButtonElement).disabled).toBe(true))
    expect(screen.getByTestId('chat-send-gate')).toBeTruthy()
  })
})

// A2 (deferred spec 2026-09-28 §2.1, "Writes that bypass axios" (a)): the
// chat stream is a raw fetch and the assistant's tools write into the
// BACKEND's project — so while this tab shows another one, Send is gated and
// the existing gate element shows the banner's sentence; card requests are
// dropped (they must not fire into whichever project a later Switch lands on).
describe('Send gate while the tab and the backend disagree (A2)', () => {
  const SENTENCE = 'This tab shows Demo, but the app is now on Other. Changes from this tab are paused.'
  beforeEach(() => {
    vi.mocked(getChatHealth).mockResolvedValue(health(true))
    useUIStore.setState({ projectMismatch: { tab: 'Demo', backend: 'Other' } })
  })
  afterEach(() => { useUIStore.setState({ projectMismatch: null }) })

  it('mismatch → Send disabled, chat-send-gate shows the banner sentence, Enter does not send', async () => {
    renderPanel()
    await waitFor(() => expect(getChatHealth).toHaveBeenCalled())
    const user = await typeHello()
    const send = screen.getByTestId('chat-send') as HTMLButtonElement
    expect(send.disabled).toBe(true)
    expect(send.title).toBe(SENTENCE)
    expect(screen.getByTestId('chat-send-gate').textContent).toBe(SENTENCE)
    await user.keyboard('{Enter}')
    expect(createChatStream).not.toHaveBeenCalled()
  })

  it('a card request is dropped while mismatched, not sent', async () => {
    renderPanel()
    await waitFor(() => expect(getChatHealth).toHaveBeenCalled())
    act(() => { useChatStore.setState({ lastRequest: null }); useChatStore.getState().sendRequest('Do it') })
    await new Promise(r => setTimeout(r, 50))
    expect(createChatStream).not.toHaveBeenCalled()
    expect(useChatStore.getState().requestQueue).toHaveLength(0)
  })

  it('cleared → Send enabled again', async () => {
    renderPanel()
    await waitFor(() => expect(getChatHealth).toHaveBeenCalled())
    await typeHello()
    const send = screen.getByTestId('chat-send') as HTMLButtonElement
    expect(send.disabled).toBe(true)
    act(() => { useUIStore.setState({ projectMismatch: null }) })
    await waitFor(() => expect(send.disabled).toBe(false))
    expect(screen.queryByTestId('chat-send-gate')).toBeNull()
  })
})

// P28 A3 (deferred spec 2026-09-28 §3.1, D-3): the gate reads the readiness
// of the profile the next turn runs on — `profileId ?? boundProfileId ??
// active` — from `GET /chat/profiles` (per-profile `chat_ready`), falling
// back to `/health`'s `chat_ready` for the active profile when the list does
// not say (not loaded, refused, older backend). `boundProfileId` comes from
// `/history.bound_profile_id` on hydrate: after a reload the store's pick is
// null, but the resumed session stays bound to the profile it ran on.
describe('Send gate follows the bound profile and per-profile readiness (P28 A3)', () => {
  const PROFILES = {
    active_profile_id: 'anthropic-sonnet',
    profiles: [
      { id: 'anthropic-sonnet', label: 'Default', wire: 'anthropic', chat_ready: false },
      { id: 'local-llm', label: 'Local LLM (auth none)', wire: 'openai', chat_ready: true },
      { id: 'keyless-openai', label: 'Keyless', wire: 'openai', chat_ready: false },
    ],
  }
  const TURN = {
    ts: 1, session_id: 's-bound', model: 'm', user: 'earlier', usage: {},
    assistant: [{ type: 'text', text: 'earlier answer' }],
  }
  function historyBoundTo(id: string | null) {
    vi.mocked(getChatHistory).mockResolvedValueOnce({
      turns: [TURN], last_session_id: 's-bound', bound_project: 'Demo',
      history_gap: 0, pending_turn: null, bound_profile_id: id,
    } as never)
  }
  afterEach(() => { useChatStore.setState({ boundProfileId: null } as never) })

  it('after a reload with profileId null, /history bound to a ready non-active profile while the active profile is not ready → Send enabled', async () => {
    vi.mocked(getChatProfiles).mockResolvedValue(PROFILES as never)
    vi.mocked(getChatHealth).mockResolvedValue(health(false))
    historyBoundTo('local-llm')
    renderPanel()
    await waitFor(() => expect(useChatStore.getState().boundProfileId).toBe('local-llm'))
    await waitFor(() => expect(
      (screen.getByTestId('chat-model-select') as HTMLSelectElement).value).toBe('local-llm'))
    const user = await typeHello()
    await new Promise(r => setTimeout(r, 50))
    const send = screen.getByTestId('chat-send') as HTMLButtonElement
    expect(send.disabled).toBe(false)
    expect(screen.queryByTestId('chat-send-gate')).toBeNull()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(createChatStream).toHaveBeenCalledTimes(1))
    // The bound profile is never asserted over the session's own binding.
    expect(vi.mocked(createChatStream).mock.calls[0][0]).not.toHaveProperty('profile_id')
  })

  it('bound to a keyless profile, active ready → gated', async () => {
    vi.mocked(getChatProfiles).mockResolvedValue({
      ...PROFILES,
      profiles: PROFILES.profiles.map(p => p.id === 'anthropic-sonnet' ? { ...p, chat_ready: true } : p),
    } as never)
    vi.mocked(getChatHealth).mockResolvedValue(health(true))
    historyBoundTo('keyless-openai')
    renderPanel()
    await waitFor(() => expect(useChatStore.getState().boundProfileId).toBe('keyless-openai'))
    await typeHello()
    await waitFor(() => expect((screen.getByTestId('chat-send') as HTMLButtonElement).disabled).toBe(true))
    expect(screen.getByTestId('chat-send-gate')).toBeTruthy()
  })

  it("a picked non-active profile is gated on ITS readiness from the list", async () => {
    vi.mocked(getChatProfiles).mockResolvedValue({
      ...PROFILES,
      profiles: PROFILES.profiles.map(p => p.id === 'anthropic-sonnet' ? { ...p, chat_ready: true } : p),
    } as never)
    vi.mocked(getChatHealth).mockResolvedValue(health(true))
    useChatStore.setState({ profileId: 'keyless-openai' })
    renderPanel()
    await typeHello()
    await waitFor(() => expect((screen.getByTestId('chat-send') as HTMLButtonElement).disabled).toBe(true))
  })

  it('the pick wins over the bound profile', async () => {
    vi.mocked(getChatProfiles).mockResolvedValue(PROFILES as never)
    vi.mocked(getChatHealth).mockResolvedValue(health(false))
    historyBoundTo('keyless-openai')
    useChatStore.setState({ profileId: 'local-llm' })
    renderPanel()
    await waitFor(() => expect(useChatStore.getState().boundProfileId).toBe('keyless-openai'))
    await typeHello()
    await new Promise(r => setTimeout(r, 50))
    expect((screen.getByTestId('chat-send') as HTMLButtonElement).disabled).toBe(false)
  })

  it('/chat/profiles refused (401): the active profile falls back to /health, a bound non-active one fails open', async () => {
    vi.mocked(getChatProfiles).mockRejectedValue(Object.assign(new Error('401'), { response: { status: 401 } }))
    vi.mocked(getChatHealth).mockResolvedValue(health(false))
    renderPanel()
    await typeHello()
    const send = screen.getByTestId('chat-send') as HTMLButtonElement
    await waitFor(() => expect(send.disabled).toBe(true))
    cleanup()
    historyBoundTo('local-llm')
    renderPanel()
    await waitFor(() => expect(useChatStore.getState().boundProfileId).toBe('local-llm'))
    await typeHello()
    await new Promise(r => setTimeout(r, 50))
    expect((screen.getByTestId('chat-send') as HTMLButtonElement).disabled).toBe(false)
  })

  it("a turn's session_init names the bound profile → the gate follows it", async () => {
    vi.mocked(getChatProfiles).mockResolvedValue({
      ...PROFILES,
      profiles: PROFILES.profiles.map(p => p.id === 'anthropic-sonnet' ? { ...p, chat_ready: true } : p),
    } as never)
    vi.mocked(getChatHealth).mockResolvedValue(health(true))
    vi.mocked(createChatStream).mockImplementationOnce((_req, onFrame) => {
      onFrame({ event: 'session_init', data: { session_id: 's9', profile_id: 'keyless-openai' } } as never)
      onFrame({ event: 'turn_done', data: {} } as never)
      onFrame({ event: 'session_done', data: { reason: 'complete' } } as never)
      return () => {}
    })
    renderPanel()
    const user = await typeHello()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(useChatStore.getState().boundProfileId).toBe('keyless-openai'))
    await user.type(screen.getByTestId('chat-input'), 'again')
    await waitFor(() => expect((screen.getByTestId('chat-send') as HTMLButtonElement).disabled).toBe(true))
  })

  it('a key saved in the inline form re-reads the profile list too', async () => {
    vi.mocked(getChatProfiles).mockResolvedValue(PROFILES as never)
    vi.mocked(getChatHealth).mockResolvedValue(health(false))
    renderPanel()
    const user = await typeHello()
    const send = screen.getByTestId('chat-send') as HTMLButtonElement
    await waitFor(() => expect(send.disabled).toBe(true))
    vi.mocked(getChatHealth).mockResolvedValue(health(true))
    vi.mocked(getChatProfiles).mockResolvedValue({
      ...PROFILES,
      profiles: PROFILES.profiles.map(p => p.id === 'anthropic-sonnet' ? { ...p, chat_ready: true } : p),
    } as never)
    await user.type(await screen.findByTestId('chat-api-key-input'), 'sk-test-key')
    await user.click(screen.getByTestId('chat-api-key-save'))
    await waitFor(() => expect(putApiKeySettings).toHaveBeenCalled())
    await waitFor(() => expect(send.disabled).toBe(false))
  })
})
