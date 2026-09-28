import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore, type PendingConfirmationCard } from '../store/chatStore'
import { createChatStream, getChatHealth, postChatAbort, postChatConfirm } from '../api/chat'
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
    postChatConfirm: vi.fn().mockResolvedValue({ ok: true }),
    getChatHealth: vi.fn(),
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

// A Guided `write` card: since the P25 gate (B1) the backend emits one for
// update_component in Guided mode, which is what this suite renders.
const CARD: PendingConfirmationCard = {
  tool_use_id: 'tu1', tool_name: 'update_component', args: {}, safety_tier: 'write',
  confirmation_token: 'tok', ttl_seconds: 60, expires_at_epoch_ms: Date.now() + 60_000,
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getChatHealth).mockResolvedValue({ ok: true } as never)   // readiness unknown → open
  localStorage.removeItem('chat:firstSendAck')
  useUIStore.setState({
    currentProject: 'Demo', uiMode: 'guided', activeSlidePanel: 'hubDesign',
    canvasView: 'blank', selectedComponent: null, compareRailOpen: false,
    resultsSnapshotIdx: 0,
  })
  useChatStore.setState({
    sessionId: 'sess-1', pending: null, messages: [], error: null,
    streaming: false, streamCleanup: null, requestQueue: [], lastRequest: null,
    activeRequest: null,
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

const RAW = 'Apply this recommendation from the study review: "Not certified: LOLE 12.4 h/yr". '
  + 'Run the tool run_eh_study with exactly these arguments: '
  + '{"archetype":"weak_flexible","stages":["apply_pack","ens_solve","mc_certify","dtc_stress","dtc_planning"]}. '
  + 'Say in one sentence what will change, then proceed to the confirmation.'

describe('P25 gate: how a card request looks, and when it is not sent', () => {
  it('B2: shows the label; the sent text is unchanged and sits in a collapsed Details', async () => {
    renderPanel()
    act(() => { useChatStore.getState().sendRequest(RAW, { source: 'hub-design',
      label: 'Apply this recommendation: Not certified' }) })
    await waitFor(() => expect(calls()).toHaveLength(1))
    expect(calls()[0][0].message).toBe(RAW)                       // the wire is unchanged
    const msg = useChatStore.getState().messages.at(-1)!
    expect(msg.content).toBe(RAW)                                 // so is the history
    const bubble = screen.getAllByTestId('chat-message').at(-1)!
    expect(bubble.querySelector('[data-testid="chat-message-label"]')!.textContent)
      .toBe('Apply this recommendation: Not certified')
    const details = bubble.querySelector('details[data-testid="chat-message-details"]') as HTMLDetailsElement
    expect(details).not.toBeNull()
    expect(details.open).toBe(false)
    expect(details.textContent).toContain('run_eh_study')
    expect(details.textContent).toContain(RAW)
  })

  it('B2: a user bubble wraps long tokens instead of scrolling sideways', async () => {
    renderPanel()
    send('x'.repeat(300))
    await waitFor(() => expect(calls()).toHaveLength(1))
    const text = screen.getAllByTestId('chat-message').at(-1)!
      .querySelector('[data-testid="chat-message-text"]')!
    expect(text.className).toContain('break-words')
    expect(text.className).toContain('[overflow-wrap:anywhere]')
  })

  it('B2: a reloaded Improve request (no label stored) renders the same way', async () => {
    useChatStore.setState({ messages: [{ id: 'm1', role: 'user', content: RAW, ts: 1 }] })
    renderPanel()
    const bubble = (await screen.findAllByTestId('chat-message'))[0]
    expect(bubble.querySelector('[data-testid="chat-message-label"]')!.textContent)
      .toBe('Apply this recommendation: Not certified: LOLE 12.4 h/yr')
    expect((bubble.querySelector('details') as HTMLDetailsElement).open).toBe(false)
  })

  it('B2: a typed message and a plain card sentence render as before', async () => {
    renderPanel()
    send('On the Site card, no critical load is tagged.')
    await waitFor(() => expect(calls()).toHaveLength(1))
    const bubble = screen.getAllByTestId('chat-message').at(-1)!
    expect(bubble.querySelector('details')).toBeNull()
    expect(bubble.querySelector('[data-testid="chat-message-text"]')!.textContent)
      .toBe('On the Site card, no critical load is tagged.')
  })

  it('no API key (chat_ready false): nothing is posted, the queue is dropped, the key form shows', async () => {
    vi.mocked(getChatHealth).mockResolvedValue({ ok: true, chat_ready: false,
      active_profile: { id: 'p', label: 'P', wire: 'anthropic' } } as never)
    renderPanel()
    await screen.findByTestId('chat-send-gate')
    send('do it')
    await settle()
    expect(calls()).toHaveLength(0)
    expect(useChatStore.getState().requestQueue).toEqual([])
    expect(useChatStore.getState().messages).toEqual([])
    expect(screen.getByTestId('chat-send-gate')).toBeTruthy()
  })

  it('denying one action drops the rest of that card\'s actions, not other requests', async () => {
    const user = (await import('@testing-library/user-event')).default.setup()
    renderPanel()
    act(() => {
      const st = useChatStore.getState()
      st.sendRequest('action one', { group: 'g1' })
      st.sendRequest('action two', { group: 'g1' })
      st.sendRequest('another card', { group: 'g2' })
    })
    await waitFor(() => expect(calls()).toHaveLength(1))
    act(() => useChatStore.getState().setPending({ ...CARD, tool_name: 'run_eh_study' }))
    await user.click(await screen.findByTestId('chat-confirm-deny'))
    expect(postChatConfirm).toHaveBeenCalledWith('sess-1',
      expect.objectContaining({ decision: 'deny' }))
    expect(useChatStore.getState().requestQueue.map(r => r.text)).toEqual(['another card'])
    act(() => useChatStore.getState().setStreaming(false))
    await waitFor(() => expect(calls()).toHaveLength(2))
    expect(calls()[1][0].message).toBe('another card')
  })

  it('approving keeps the rest of the group queued', async () => {
    const user = (await import('@testing-library/user-event')).default.setup()
    renderPanel()
    act(() => {
      useChatStore.getState().sendRequest('action one', { group: 'g1' })
      useChatStore.getState().sendRequest('action two', { group: 'g1' })
    })
    await waitFor(() => expect(calls()).toHaveLength(1))
    act(() => useChatStore.getState().setPending({ ...CARD, tool_name: 'run_eh_study' }))
    await user.click(await screen.findByTestId('chat-confirm-approve'))
    act(() => useChatStore.getState().setStreaming(false))
    await waitFor(() => expect(calls()).toHaveLength(2))
    expect(calls()[1][0].message).toBe('action two')
  })

  it('a typed message is not the active card request any more', async () => {
    renderPanel()
    act(() => { useChatStore.getState().sendRequest('card', { group: 'g1' }) })
    await waitFor(() => expect(calls()).toHaveLength(1))
    expect(useChatStore.getState().activeRequest?.group).toBe('g1')
    act(() => useChatStore.getState().setStreaming(false))
    const input = await screen.findByTestId('chat-input')
    const { fireEvent } = await import('@testing-library/react')
    fireEvent.change(input, { target: { value: 'typed' } })
    fireEvent.click(screen.getByTestId('chat-send'))
    await waitFor(() => expect(calls()).toHaveLength(2))
    expect(useChatStore.getState().activeRequest).toBeNull()
  })
})

// P25 re-gate R1 (adopted from qa25r/probes/QA25R.probe.test.tsx): with
// several Guided writes in one response, the NEXT card's SSE frame often
// lands while the previous card's /confirm POST is still in flight. The
// handler must clear only the card it answered.
const CARD_B: PendingConfirmationCard = { ...CARD, tool_use_id: 'tu2', confirmation_token: 'tokB',
  args: { name: 'B2' } }

describe('R1: two cards in one response', () => {
  for (const decision of ['approve', 'deny'] as const) {
    it(`${decision}: card B arriving before /confirm returns survives`, async () => {
      useChatStore.setState({ pending: CARD, streaming: true })
      vi.mocked(postChatConfirm).mockImplementationOnce(async () => {
        act(() => useChatStore.getState().setPending(CARD_B))
        return { ok: true } as never
      })
      renderPanel()
      const btn = await screen.findByTestId(decision === 'approve' ? 'chat-confirm-approve' : 'chat-confirm-deny')
      await act(async () => { btn.click() })
      await settle()
      expect(useChatStore.getState().pending?.confirmation_token).toBe('tokB')
      expect(screen.getByTestId('chat-confirmation-card')).toBeTruthy()
    })

    it(`${decision}: with no next card, the answered card is cleared`, async () => {
      useChatStore.setState({ pending: CARD, streaming: true })
      renderPanel()
      const btn = await screen.findByTestId(decision === 'approve' ? 'chat-confirm-approve' : 'chat-confirm-deny')
      await act(async () => { btn.click() })
      await settle()
      expect(useChatStore.getState().pending).toBeNull()
    })
  }
})

describe('P25 re-gate notes: expiry and Stop end the card run', () => {
  it('a card that expires drops the rest of its group (like a Deny)', async () => {
    renderPanel()
    act(() => {
      useChatStore.getState().sendRequest('action one', { group: 'g1' })
      useChatStore.getState().sendRequest('action two', { group: 'g1' })
      useChatStore.getState().sendRequest('other', { group: 'g2' })
    })
    await waitFor(() => expect(calls()).toHaveLength(1))
    act(() => useChatStore.getState().setPending({ ...CARD, expires_at_epoch_ms: Date.now() - 1 }))
    await waitFor(() => expect(useChatStore.getState().pending).toBeNull())
    expect(useChatStore.getState().error?.error_kind).toBe('confirmation_expired')
    expect(useChatStore.getState().requestQueue.map(r => r.text)).toEqual(['other'])
  })

  it('Stop clears every queued card request', async () => {
    renderPanel()
    act(() => {
      useChatStore.getState().sendRequest('first', { group: 'g1' })
      useChatStore.getState().sendRequest('second', { group: 'g1' })
      useChatStore.getState().sendRequest('third')
    })
    await waitFor(() => expect(calls()).toHaveLength(1))
    await act(async () => { screen.getByTestId('chat-abort').click() })
    await waitFor(() => expect(postChatAbort).toHaveBeenCalled())
    expect(useChatStore.getState().requestQueue).toEqual([])
    await settle()
    expect(calls()).toHaveLength(1)
  })
})

describe('P25 re-gate note 2: the card header in plain words (Guided only)', () => {
  const header = () => screen.getByTestId('chat-confirmation-header').textContent
  it('Guided write → "Confirm this change"', async () => {
    useChatStore.setState({ pending: CARD })
    renderPanel()
    await screen.findByTestId('chat-confirmation-card')
    expect(header()).toBe('Confirm this change')
  })

  it.each(['execution', 'execution_long_running', 'destructive'])('Guided %s → "Confirm"', async (tier) => {
    useChatStore.setState({ pending: { ...CARD, safety_tier: tier } })
    renderPanel()
    await screen.findByTestId('chat-confirmation-card')
    expect(header()).toBe('Confirm')
  })

  it.each(['write', 'execution', 'destructive'])('Expert %s → unchanged "Confirm · <tier>"', async (tier) => {
    useUIStore.setState({ uiMode: 'expert' })
    useChatStore.setState({ pending: { ...CARD, safety_tier: tier } })
    renderPanel()
    await screen.findByTestId('chat-confirmation-card')
    expect(header()).toBe(`Confirm · ${tier}`)
  })
})
