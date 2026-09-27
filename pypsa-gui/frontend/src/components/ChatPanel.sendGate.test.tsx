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
import { createChatStream, getChatHealth, putApiKeySettings } from '../api/chat'
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
})
afterEach(() => cleanup())

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
