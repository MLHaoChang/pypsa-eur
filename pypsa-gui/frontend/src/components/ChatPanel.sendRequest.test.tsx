import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore, type PendingConfirmationCard } from '../store/chatStore'
import { createChatStream, postChatConfirm } from '../api/chat'
import { listUploads } from '../api/uploads'
import ChatPanel from './ChatPanel'
import chatStoreSource from '../store/chatStore.ts?raw'

// Guided-mode spec §6.1: a card's "Let the assistant do this" goes through
// `chatStore.sendRequest`, and ChatPanel's queue effect sends it down the SAME
// path a typed message takes — one stream per request, never while a turn is
// streaming, never while a confirmation card waits, never with attachments
// (so the first-send attachment modal cannot appear), and without touching
// the user's draft. Write tools still arrive as confirmation cards: nothing on
// this path answers one.
//
// Harness copied from ChatPanel.deixis.test.tsx.

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(() => () => {}),
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
  const ui = (
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>
  )
  const r = render(ui)
  return { ...r, rerenderSame: () => r.rerender(ui) }
}

const calls = () => vi.mocked(createChatStream).mock.calls
const send = (text: string) => act(() => {
  useChatStore.getState().sendRequest(text, { source: 'hub-design' })
})
// Give the effect every chance to (wrongly) fire.
const settle = () => act(async () => { await new Promise(r => setTimeout(r, 20)) })

const CARD: PendingConfirmationCard = {
  tool_use_id: 'tu1', tool_name: 'update_component', args: {}, safety_tier: 'write',
  confirmation_token: 'tok', ttl_seconds: 60, expires_at_epoch_ms: Date.now() + 60_000,
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.removeItem('chat:firstSendAck')
  useUIStore.setState({
    currentProject: 'Demo', uiMode: 'guided', activeSlidePanel: 'hubDesign',
    canvasView: 'blank', selectedComponent: null, compareRailOpen: false,
    resultsSnapshotIdx: 0,
  })
  useChatStore.setState({
    sessionId: 'sess-1', pending: null, messages: [], error: null,
    streaming: false, streamCleanup: null, requestQueue: [], lastRequest: null,
    attachedFileIds: [], uploads: [],
    usage: {
      input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_create_tokens: 0, reported: true,
    },
  })
})

afterEach(() => cleanup())

describe('ChatPanel dispatches queued card requests', () => {
  it('one request → exactly one stream, as a typed message would be sent', async () => {
    renderPanel()
    send('Apply this recommendation')
    await waitFor(() => expect(calls()).toHaveLength(1))
    const req = calls()[0][0]
    expect(req.message).toBe('Apply this recommendation')
    expect(req.attachment_file_ids).toBeUndefined()
    expect(req.ui_context?.ui_mode).toBe('guided')
    expect(req.input_mode).toBe('text')
    // Shown in the transcript as the user's own message.
    const last = useChatStore.getState().messages.at(-1)!
    expect(last).toMatchObject({ role: 'user', content: 'Apply this recommendation' })
    expect(useChatStore.getState().requestQueue).toEqual([])
    await settle()
    expect(calls()).toHaveLength(1)
  })

  it('waits while a turn is streaming, then sends', async () => {
    useChatStore.setState({ streaming: true })
    renderPanel()
    send('queued while busy')
    await settle()
    expect(calls()).toHaveLength(0)
    expect(useChatStore.getState().requestQueue).toHaveLength(1)
    act(() => useChatStore.getState().setStreaming(false))
    await waitFor(() => expect(calls()).toHaveLength(1))
    expect(calls()[0][0].message).toBe('queued while busy')
  })

  it('waits while a confirmation card is pending, and never answers it', async () => {
    useChatStore.setState({ pending: CARD })
    renderPanel()
    send('after the card')
    await settle()
    expect(calls()).toHaveLength(0)
    expect(useChatStore.getState().pending).toEqual(CARD)
    act(() => useChatStore.getState().setPending(null))
    await waitFor(() => expect(calls()).toHaveLength(1))
    expect(postChatConfirm).not.toHaveBeenCalled()
  })

  it('two requests → two streams, in order, the second after the first turn ends', async () => {
    renderPanel()
    send('first')
    send('second')
    await waitFor(() => expect(calls()).toHaveLength(1))
    expect(calls()[0][0].message).toBe('first')
    await settle()
    expect(calls()).toHaveLength(1)          // the first turn is streaming
    act(() => useChatStore.getState().setStreaming(false))
    await waitFor(() => expect(calls()).toHaveLength(2))
    expect(calls()[1][0].message).toBe('second')
  })

  it('attached files present and no first-send ack → no modal, files not sent, attachments kept', async () => {
    // The panel hydrates the chip strip from disk and attaches every file
    // (default-ON), exactly as for a user with two uploads.
    const up = (id: string) => ({ file_id: id, filename: `${id}.csv`, mime: 'text/csv',
      size: 1, kind: 'user_upload' as const, uploaded_at: 1 })
    vi.mocked(listUploads).mockResolvedValueOnce([up('f1'), up('f2')] as never)
    renderPanel()
    await waitFor(() => expect(useChatStore.getState().attachedFileIds).toEqual(['f1', 'f2']))
    send('do it')
    await waitFor(() => expect(calls()).toHaveLength(1))
    expect(calls()[0][0].attachment_file_ids).toBeUndefined()
    expect(screen.queryByTestId('chat-first-send-modal')).toBeNull()
    expect(useChatStore.getState().attachedFileIds).toEqual(['f1', 'f2'])
    expect(localStorage.getItem('chat:firstSendAck')).toBeNull()
  })

  it('waits while the user\'s own first-send modal is open', async () => {
    const up = (id: string) => ({ file_id: id, filename: `${id}.csv`, mime: 'text/csv',
      size: 1, kind: 'user_upload' as const, uploaded_at: 1 })
    vi.mocked(listUploads).mockResolvedValueOnce([up('f1')] as never)
    const user = (await import('@testing-library/user-event')).default.setup()
    renderPanel()
    await waitFor(() => expect(useChatStore.getState().attachedFileIds).toEqual(['f1']))
    await user.type(await screen.findByTestId('chat-input'), 'typed with a file')
    await user.click(screen.getByTestId('chat-send'))
    expect(await screen.findByTestId('chat-first-send-modal')).toBeTruthy()
    send('card request')
    await settle()
    expect(calls()).toHaveLength(0)
    await user.click(screen.getByTestId('chat-first-send-modal'))   // backdrop = cancel
    await waitFor(() => expect(calls()).toHaveLength(1))
    expect(calls()[0][0].message).toBe('card request')
  })

  it('a re-render storm still sends once', async () => {
    const { rerenderSame } = renderPanel()
    send('only once')
    for (let i = 0; i < 5; i++) rerenderSame()
    await waitFor(() => expect(calls()).toHaveLength(1))
    act(() => useChatStore.getState().setStreaming(false))
    for (let i = 0; i < 5; i++) rerenderSame()
    await settle()
    expect(calls()).toHaveLength(1)
  })

  it('leaves the composer draft untouched', async () => {
    const user = (await import('@testing-library/user-event')).default.setup()
    renderPanel()
    const box = await screen.findByTestId('chat-input') as HTMLTextAreaElement
    await user.type(box, 'my draft')
    send('card request')
    await waitFor(() => expect(calls()).toHaveLength(1))
    expect(box.value).toBe('my draft')
    expect(calls()[0][0].message).toBe('card request')
  })

  it('the store path cannot confirm anything: chatStore imports no chat API', () => {
    expect(chatStoreSource).not.toMatch(/from ['"]\.\.\/api\/chat['"]/)
    expect(chatStoreSource).not.toMatch(/postChatConfirm|\/api\/chat\/confirm/)
  })
})
