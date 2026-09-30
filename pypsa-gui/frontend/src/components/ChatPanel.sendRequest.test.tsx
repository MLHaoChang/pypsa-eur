import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore, type PendingConfirmationCard } from '../store/chatStore'
import { createChatStream, getChatHealth, postChatAbort, postChatConfirm } from '../api/chat'
import { listUploads } from '../api/uploads'
import ChatPanel, { guidedCardSummary } from './ChatPanel'
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
    // P26: the same plain words as the live label (actionLabel → plainWords);
    // before, a reload showed the raw engine title ("LOLE").
    expect(bubble.querySelector('[data-testid="chat-message-label"]')!.textContent)
      .toBe('Apply this recommendation: Not certified: expected shortfall 12.4 h/yr')
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

// P26 (carried from the P25 gate): Guided asks to confirm every write-tier
// call, and some write-tier tools do not edit the network at all — exports,
// a project snapshot, opening a project. "Confirm this change" was wrong for
// them. The card stays; only its words say what the call is for. Expert is
// unchanged.
describe('P26: Guided card wording per tool purpose', () => {
  const header = () => screen.getByTestId('chat-confirmation-header').textContent
  const note = () => screen.queryByTestId('chat-confirmation-note')?.textContent ?? null
  const show = async (tool_name: string, safety_tier = 'write') => {
    useChatStore.setState({ pending: { ...CARD, tool_name, safety_tier } })
    renderPanel()
    await screen.findByTestId('chat-confirmation-card')
  }

  it.each([
    'export_to_csv', 'export_to_excel', 'export_preview_png', 'export_chat_summary',
    'export_asset_results', 'gridspine_export_handoff_bundle',
  ])('Guided %s → "Confirm: export a file", network not changed', async (tool) => {
    await show(tool)
    expect(header()).toBe('Confirm: export a file')
    expect(note()).toBe('Writes a file you can download. Your network is not changed.')
  })

  it('Guided create_project_snapshot → "Confirm: save a copy"', async () => {
    await show('create_project_snapshot')
    expect(header()).toBe('Confirm: save a copy')
    expect(note()).toBe('Saves a backup copy of the project as it is now. Your network is not changed.')
  })

  it.each(['load_project', 'activate_project'])('Guided %s → "Confirm: open a project"', async (tool) => {
    await show(tool)
    expect(header()).toBe('Confirm: open a project')
    expect(note()).toBe('Switches the workbench to another project. Save first if you have unsaved edits.')
  })

  it.each(['update_component', 'bulk_update_components', 'update_solver_config', 'put_stress_scenarios'])(
    'Guided edit %s → "Confirm this change", no note', async (tool) => {
      await show(tool)
      expect(header()).toBe('Confirm this change')
      expect(note()).toBeNull()
    })

  it('the purpose words follow the tier: a non-write tool of an export-like name keeps "Confirm"', async () => {
    await show('export_to_csv', 'destructive')
    expect(header()).toBe('Confirm')
    expect(note()).toBeNull()
  })

  it.each(['export_to_csv', 'create_project_snapshot', 'load_project', 'update_component'])(
    'Expert %s → unchanged "Confirm · write", no note', async (tool) => {
      useUIStore.setState({ uiMode: 'expert' })
      await show(tool)
      expect(header()).toBe('Confirm · write')
      expect(note()).toBeNull()
    })
})

// P26 (coordinator item 5): a Guided card leads with one plain sentence; the
// tool id and its JSON arguments sit in a collapsed "Details". The card and
// Approve / Deny are unchanged. Expert keeps the tool name and the JSON.
describe('P26: Guided card summary line, raw call under Details', () => {
  const summary = () => screen.queryByTestId('chat-confirmation-summary')?.textContent ?? null
  const show = async (tool_name: string, args: Record<string, unknown>, safety_tier = 'write') => {
    useChatStore.setState({ pending: { ...CARD, tool_name, args, safety_tier } })
    renderPanel()
    return screen.findByTestId('chat-confirmation-card')
  }

  it('run_eh_study with budget_solves → the study, with the step count', async () => {
    const card = await show('run_eh_study', { archetype: 'weak_flexible', budget_solves: 30,
      stages: ['apply_pack', 'mc_certify'] }, 'execution')
    expect(summary()).toBe('Run the reliability study for this site (about 30 calculation steps)')
    const details = card.querySelector('details[data-testid="chat-confirmation-details"]') as HTMLDetailsElement
    expect(details).not.toBeNull()
    expect(details.open).toBe(false)
    expect(details.textContent).toContain('run_eh_study')
    expect(details.textContent).toContain('"budget_solves": 30')
    // nothing technical outside the collapsed Details
    const outside = [...card.childNodes].filter(n => n !== details).map(n => n.textContent).join(' ')
    expect(outside).not.toContain('run_eh_study')
    expect(outside).not.toContain('mc_certify')
    expect(card.getAttribute('aria-labelledby')).toBe('chat-confirmation-title')
    expect(screen.getByTestId('chat-confirmation-summary').id).toBe('chat-confirmation-title')
  })

  it('run_eh_study without budget_solves → no count', async () => {
    await show('run_eh_study', { archetype: 'off_grid' }, 'execution')
    expect(summary()).toBe('Run the reliability study for this site')
  })

  it.each<[string, Record<string, unknown>, string]>([
    ['update_component', { component_class: 'Bus', name: 'it_bus', attrs: { eh_critical: true } },
      'Change the settings of it_bus'],
    ['bulk_update_components', { component_class: 'Bus', names: ['a', 'b', 'c'], updates: { eh_critical: true } },
      'Change the settings of 3 components'],
    ['update_solver_config', { partial: { voll: 5000 } },
      'Set the price of undelivered energy to €5,000 per MWh'],
    ['update_solver_config', { partial: { solver_name: 'highs' } }, 'Change the study settings'],
    ['put_stress_scenarios', { name: 'Demo', scenarios: [{}, {}, {}] },
      'Save the list of hard conditions to test (3 scenarios)'],
    ['export_to_csv', { filename: 'risks.csv', columns: [], rows: [] }, 'Export the file risks.csv'],
    ['create_project_snapshot', { name: 'Demo', label: 'before-fix' }, 'Save a backup copy named "before-fix"'],
    ['activate_project', { project_id: 'Island Microgrid' }, 'Open the project Island Microgrid'],
    ['set_snapshots', { snapshots: [] }, 'The assistant wants to use set snapshots'],
  ])('Guided %s → a plain summary', async (tool, args, want) => {
    await show(tool, args)
    expect(summary()).toBe(want)
  })

  it('run_fmea_sweep → a plain summary', async () => {
    await show('run_fmea_sweep', {}, 'execution')
    expect(summary()).toBe('Run the equipment-failure check (FMEA)')
  })

  it('Expert is unchanged: tool name as the title, JSON shown, no summary, no Details', async () => {
    useUIStore.setState({ uiMode: 'expert' })
    const card = await show('run_eh_study', { budget_solves: 30 }, 'execution')
    expect(summary()).toBeNull()
    expect(card.querySelector('details')).toBeNull()
    expect(document.getElementById('chat-confirmation-title')!.textContent).toBe('run_eh_study')
    expect(card.querySelector('pre')!.textContent).toContain('"budget_solves": 30')
  })

  it('Guided: Approve still answers the card', async () => {
    await show('run_eh_study', { budget_solves: 30 }, 'execution')
    await act(async () => { screen.getByTestId('chat-confirm-approve').click() })
    expect(postChatConfirm).toHaveBeenCalledWith('sess-1', { token: 'tok', decision: 'approve' })
  })
})

// P26 (coordinator item 6): after a Deny, Guided shows one plain line; the
// technical lines (the backend's confirmation_denied error, "denied: <tool>")
// sit under Details or are hidden. Expert keeps both lines as they were.
describe('P26: a declined card in Guided', () => {
  const ERR = "✗ run_eh_study — confirmation_denied: deny on confirmation for 'run_eh_study'"
  const seed = () => useChatStore.setState({ messages: [
    { id: 't1', role: 'tool', content: ERR, tool_use_id: 'tu1', tool_name: 'run_eh_study', ts: 1 },
    { id: 't2', role: 'tool', content: 'denied: run_eh_study', tool_use_id: 'tu1', tool_name: 'run_eh_study', ts: 2 },
  ] })
  const toolRows = () => screen.queryAllByTestId('chat-message').filter(m => m.getAttribute('data-role') === 'tool')

  it('Guided: one line "You declined — nothing was changed.", the raw line under Details', async () => {
    seed()
    renderPanel()
    await waitFor(() => expect(toolRows()).toHaveLength(1))
    const row = toolRows()[0]
    expect(row.querySelector('[data-testid="chat-tool-label"]')!.textContent)
      .toBe('You declined — nothing was changed.')
    const details = row.querySelector('details') as HTMLDetailsElement
    expect(details.open).toBe(false)
    expect(details.textContent).toContain('denied: run_eh_study')
    expect(screen.queryByText(ERR)).toBeNull()
  })

  it('Guided: clicking Deny produces that line', async () => {
    useChatStore.setState({ pending: CARD })
    renderPanel()
    await act(async () => { (await screen.findByTestId('chat-confirm-deny')).click() })
    await waitFor(() => expect(screen.getByTestId('chat-tool-label').textContent)
      .toBe('You declined — nothing was changed.'))
  })

  it('Expert is unchanged: both raw lines, no label', async () => {
    useUIStore.setState({ uiMode: 'expert' })
    seed()
    renderPanel()
    await waitFor(() => expect(toolRows()).toHaveLength(2))
    expect(toolRows()[0].textContent).toContain(ERR)
    expect(toolRows()[1].textContent).toContain('denied: run_eh_study')
    expect(screen.queryByTestId('chat-tool-label')).toBeNull()
  })

  it('other failed tools still show in Guided', async () => {
    useChatStore.setState({ messages: [
      { id: 't1', role: 'tool', content: '✗ update_component — validation_error: bus Field required', ts: 1 },
    ] })
    renderPanel()
    await waitFor(() => expect(toolRows()).toHaveLength(1))
    expect(toolRows()[0].textContent).toContain('validation_error')
  })
})

// P26 gate B2: the collapsed Details hid what a destructive card deletes or
// replaces. Every destructive and execution tool the assistant can call
// (BE/services/chat_tools_schema.py, "Safety: destructive" / "execution" /
// "execution_long_running") has a summary that names its target; the fallback
// names the first identifying argument; destructive cards open their Details.
describe('P26 gate B2: Guided cards name what they touch', () => {
  const DESTRUCTIVE_AND_EXECUTION = [
    'delete_component', 'cascade_delete_bus', 'batch_delete_components', 'cluster_network',
    'delete_vintage_bounds', 'delete_timeseries', 'run_simulation', 'run_ac_pf_stage',
    'abort_simulation', 'force_reset_simulation', 'run_fmea_sweep', 'run_frontier_study',
    'run_mc_study', 'run_coupling_loop', 'run_margin_loop', 'run_eh_study', 'abort_adequacy_study',
    'solve_queue_enqueue', 'solve_queue_abort', 'save_project', 'save_project_as',
    'save_project_a_copy', 'rename_project', 'delete_project', 'create_scenario',
    'import_project_bundle', 'create_project_from_template', 'restore_project_snapshot',
    'delete_project_snapshot', 'import_network_nc', 'import_csv_bundle', 'import_excel',
    'import_matpower', 'clear_audit_log', 'undo_last', 'clear_chat_history',
    'apply_demand_from_excel', 'delete_upload', 'reconstruct_network_from_image', 'clear_uploads',
    'set_active_profile', 'gridspine_run_pipeline',
  ]
  it.each(DESTRUCTIVE_AND_EXECUTION)('%s has its own summary (not the fallback)', (tool) => {
    expect(guidedCardSummary(tool, {})).not.toMatch(/^The assistant wants to use/)
  })

  it.each<[string, Record<string, unknown>, string]>([
    ['delete_component', { component_class: 'Generator', name: 'genset_1' }, 'Delete genset_1 from the network'],
    ['cascade_delete_bus', { name: 'it_bus' }, 'Delete the bus it_bus and everything connected to it'],
    ['batch_delete_components', { component_class: 'Load', names: ['a', 'b', 'c', 'd', 'e', 'f'] },
      'Delete 6 components from the network: a, b, c, d, e and 1 more'],
    ['delete_timeseries', { component: 'Load', name: 'it_load', attribute: 'p_set' },
      'Delete the p_set time series of it_load'],
    ['delete_vintage_bounds', { component_class: 'Generator', name: 'pv' }, 'Remove the build-year limits of pv'],
    ['delete_project', { name: 'Demo' }, 'Delete the project Demo'],
    ['delete_project', { name: 'Demo', cascade: true }, 'Delete the project Demo and every scenario made from it'],
    ['delete_project_snapshot', { name: 'Demo', snapshot_id: 's1' }, 'Delete the backup s1 of the project Demo'],
    ['restore_project_snapshot', { name: 'Demo', snapshot_id: 's1' },
      'Restore the project Demo to the backup s1 (replaces the current network)'],
    ['import_network_nc', { bytes_b64: 'AAAA', filename: 'grid.nc' },
      'Replace the whole network with the imported file grid.nc'],
    ['import_excel', { bytes_b64: 'AAAA' }, 'Replace the whole network with an imported file'],
    ['rename_project', { name: 'A', new_name: 'B' }, 'Rename the project A to B'],
    ['save_project_as', { name: 'B' }, 'Save the project under the new name B'],
    ['create_project_from_template', { template_id: 'eh_microgrid', new_name: 'Isle' },
      'Create the project Isle from the eh_microgrid template'],
    ['apply_demand_from_excel', { file_id: 'f1', load_name: 'it_load', time_col: 't', value_col: 'v' },
      'Replace the demand of it_load with values from the uploaded file f1'],
    ['delete_upload', { file_id: 'f9' }, 'Delete the uploaded file f9'],
    ['abort_adequacy_study', { study: 'eh_study' }, 'Stop the running eh_study study'],
    ['run_mc_study', { draws: 1000 }, 'Run the reliability simulation (1000 runs)'],
    ['run_coupling_loop', { target_lole_h: 3 }, 'Size the design to meet 3 h/yr of shortfall (repeated calculations)'],
    ['solve_queue_enqueue', { project_id: 'Demo' }, 'Queue a calculation of the project Demo'],
    ['set_active_profile', { profile_id: 'local' }, 'Switch the assistant to the model profile local'],
  ])('%s names its target', (tool, args, want) => {
    expect(guidedCardSummary(tool, args)).toBe(want)
  })

  it.each<[Record<string, unknown>, string]>([
    [{ name: 'genset_1' }, 'The assistant wants to use frob widget on genset_1'],
    [{ names: ['a', 'b'] }, 'The assistant wants to use frob widget on a, b'],
    [{ component: 'Load' }, 'The assistant wants to use frob widget on Load'],
    [{ project_id: 'Demo' }, 'The assistant wants to use frob widget on Demo'],
    [{ filename: 'x.csv' }, 'The assistant wants to use frob widget on x.csv'],
    [{ other: 1 }, 'The assistant wants to use frob widget'],
  ])('fallback names the first identifying argument %j', (args, want) => {
    expect(guidedCardSummary('frob_widget', args)).toBe(want)
  })

  const details = () => screen.getByTestId('chat-confirmation-details') as HTMLDetailsElement
  it('destructive tier: Details open by default, the target in the summary', async () => {
    useChatStore.setState({ pending: { ...CARD, tool_name: 'delete_component', safety_tier: 'destructive',
      args: { component_class: 'Generator', name: 'genset_1' } } })
    renderPanel()
    await screen.findByTestId('chat-confirmation-card')
    expect(screen.getByTestId('chat-confirmation-summary').textContent).toBe('Delete genset_1 from the network')
    expect(details().open).toBe(true)
  })

  it.each(['execution', 'write'])('%s tier: Details stay collapsed', async (tier) => {
    useChatStore.setState({ pending: { ...CARD, tool_name: 'run_mc_study', safety_tier: tier, args: { draws: 10 } } })
    renderPanel()
    await screen.findByTestId('chat-confirmation-card')
    expect(details().open).toBe(false)
  })
})

// P29 (B1, deferred spec §4.1): Guided renders the tool progress lines in
// words — `… preparing X` hidden, `→ X` "Working: …", `✓ X` "Done: …",
// `✗ X` "Could not: …" with the error's own message as the next line. The
// raw line sits under Details; the transcript and Expert are unchanged.
describe('P29: Guided tool lines', () => {
  type Frame = { event: string; data: Record<string, unknown> }
  const toolRows = () => screen.queryAllByTestId('chat-message').filter(m => m.getAttribute('data-role') === 'tool')
  const labels = () => screen.queryAllByTestId('chat-tool-label').map(l => l.textContent)
  // The visible text of a row: everything outside a <details>.
  const outsideDetails = (el: HTMLElement) => {
    const c = el.cloneNode(true) as HTMLElement
    c.querySelectorAll('details').forEach(d => d.remove())
    return c.textContent ?? ''
  }
  async function startTurn() {
    renderPanel()
    send('Run the study')
    await waitFor(() => expect(calls()).toHaveLength(1))
    const onFrame = calls()[0][1] as (f: Frame) => void
    return (f: Frame) => act(() => { onFrame(f as never) })
  }
  const PREP = { event: 'tool_preparing', data: { tool_name: 'run_eh_study', tool_use_id: 'tu9' } }
  const REQ = { event: 'tool_request', data: { tool_name: 'run_eh_study', tool_use_id: 'tu9', safety_tier: 'execution' } }
  const RES = { event: 'tool_result', data: { tool_name: 'run_eh_study', tool_use_id: 'tu9' } }

  it('Guided: three frames read "Working: …" then "Done: …", no raw arrow or "preparing"', async () => {
    const emit = await startTurn()
    await emit(PREP)
    expect(toolRows()).toHaveLength(0)
    await emit(REQ)
    await waitFor(() => expect(labels()).toEqual(['Working: run the reliability study…']))
    await emit(RES)
    await waitFor(() => expect(labels()).toEqual([
      'Working: run the reliability study…', 'Done: run the reliability study']))
    for (const m of screen.queryAllByTestId('chat-message')) {
      expect(outsideDetails(m)).not.toMatch(/→ |preparing/)
    }
    // The raw line is kept under Details, collapsed.
    const details = toolRows()[1].querySelector('details') as HTMLDetailsElement
    expect(details.open).toBe(false)
    expect(details.textContent).toContain('✓ run_eh_study')
    // The transcript keeps the raw lines.
    expect(useChatStore.getState().messages.filter(m => m.role === 'tool').map(m => m.content))
      .toEqual(['… preparing run_eh_study', '→ run_eh_study', '✓ run_eh_study'])
  })

  it('Guided: a failed tool reads "Could not: …" with its message as the next line', async () => {
    const emit = await startTurn()
    await emit({ event: 'tool_request', data: { tool_name: 'update_component', tool_use_id: 'tu8', safety_tier: 'write' } })
    await emit({ event: 'tool_error', data: {
      tool_name: 'update_component', tool_use_id: 'tu8', error_kind: 'validation_error',
      message: 'bus Field required' } })
    await waitFor(() => expect(labels()).toEqual(['Working: change a setting…', 'Could not: change a setting']))
    const row = toolRows()[1]
    expect(row.querySelector('[data-testid="chat-tool-message"]')!.textContent).toBe('bus Field required')
    expect(outsideDetails(row)).not.toMatch(/validation_error|✗/)
    expect(row.querySelector('details')!.textContent).toContain('✗ update_component — validation_error: bus Field required')
  })

  it.each<[string, string]>([
    ['suggest_eh_setup', 'look at how the site is set up'],
    ['get_adequacy_results', 'read the study results'],
    ['list_components', 'list what is in the network'],
    ['run_fmea_sweep', 'check what happens when equipment fails'],
    ['update_solver_config', 'change a study setting'],
    ['put_stress_scenarios', 'save the stress scenarios'],
    ['frob_widget', 'use frob widget'],
  ])('Guided: %s reads "Done: %s"', async (tool, phrase) => {
    useChatStore.setState({ messages: [
      { id: 't1', role: 'tool', content: `✓ ${tool}`, tool_use_id: 'tu1', tool_name: tool, ts: 1 },
    ] })
    renderPanel()
    await waitFor(() => expect(labels()).toEqual([`Done: ${phrase}`]))
  })

  it.each<[string, string]>([
    ['🔀 active project: Demo → Site2', 'The assistant is now working in the project Site2.'],
    ['🔀 active project: Demo → (unbound)', 'The assistant replaced the network; it is not saved to a project yet.'],
  ])('Guided: the rebound line %s reads in words (no raw arrow)', async (raw, want) => {
    useChatStore.setState({ messages: [{ id: 't1', role: 'tool', content: raw, ts: 1 }] })
    renderPanel()
    await waitFor(() => expect(labels()).toEqual([want]))
    expect(outsideDetails(toolRows()[0])).not.toMatch(/→ /)
  })

  it('Expert renders the raw three lines', async () => {
    useUIStore.setState({ uiMode: 'expert' })
    const emit = await startTurn()
    await emit(PREP)
    await emit(REQ)
    await emit(RES)
    await waitFor(() => expect(toolRows()).toHaveLength(3))
    expect(screen.queryByTestId('chat-tool-label')).toBeNull()
    expect(toolRows().map(r => r.outerHTML)).toMatchSnapshot()
  })
})
