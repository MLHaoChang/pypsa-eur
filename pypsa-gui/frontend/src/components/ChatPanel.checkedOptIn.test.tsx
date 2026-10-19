/** Pilot is an explicit request policy; it must never silently become ordinary chat. */
import { createHash, webcrypto } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { client } from '../api/client'
import { createChatStream, getChatHistory, postChatRewind } from '../api/chat'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import * as speechOut from '../utils/speechOut'
import ChatPanel from './ChatPanel'
import AssistantDock from './AssistantDock'
import type { ChatAccessSnapshot } from '../api/chatAccess'

const unsafe = 'Invented cost 999 EUR and guaranteed profit.'
let capabilities: Record<string, unknown>
let queryClient: QueryClient
const speech: { onFinal?: (text: string) => void } = {}
vi.mock('../hooks/useSpeechToText', () => ({ useSpeechToText: (options: typeof speech) => {
  speech.onFinal = options.onFinal
  return { available: true, supported: true, listening: false, interim: '', permissionDenied: false,
    toggle: vi.fn(), stop: vi.fn() }
} }))
vi.mock('../api/dictation', () => ({ getDictationConfig: vi.fn().mockResolvedValue({ available: false,
  model: '', languages: ['en'], max_bytes: 10485760, max_recording_seconds: 120 }) }))
vi.mock('../api/chat', async original => ({ ...await original<typeof import('../api/chat')>(),
  createChatStream: vi.fn(() => () => {}), getChatHistory: vi.fn(), postChatAbort: vi.fn(), postChatConfirm: vi.fn(),
  postChatRewind: vi.fn().mockResolvedValue({ ok: true, dropped: 2 }),
  getApiKeySettings: vi.fn().mockResolvedValue({ configured: true, source: 'env', hint: 'test' }),
}))
vi.mock('../api/uploads', () => ({ deleteUpload: vi.fn(), getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]), uploadFile: vi.fn(), UploadError: class extends Error {} }))
vi.mock('../api/network', () => ({ networkApi: { getMeta: vi.fn().mockResolvedValue({ name: 'Demo', bus_count: 1, snapshot_count: 2 }) } }))
vi.mock('../api/simulation', () => ({ simulationApi: { getStatus: vi.fn().mockResolvedValue({ running: false, status: 'idle' }) } }))

beforeEach(() => {
  vi.clearAllMocks()
  capabilities = { version: 1, mode: 'pilot', profiles: { 'storage-unit-cost/1': { enabled: true } } }
  vi.spyOn(client, 'get').mockImplementation(async url => {
    if (url === '/chat/reliability/capabilities') return { data: capabilities } as never
    if (url === '/chat/profiles') return { data: { profiles: [], active_profile_id: null } } as never
    return { data: { workflows: [], chat_ready: true } } as never
  })
  vi.mocked(getChatHistory).mockResolvedValue({ turns: [], last_session_id: null, bound_project: null,
    history_gap: 0, pending_turn: null })
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('speechSynthesis', { speak: vi.fn(), cancel: vi.fn(), speaking: false })
  vi.stubGlobal('SpeechSynthesisUtterance', class { constructor(public text: string) {} })
  vi.spyOn(speechOut, 'speak').mockImplementation(() => {})
  vi.spyOn(speechOut, 'cancelSpeech').mockImplementation(() => {})
  useUIStore.setState({ currentProject: 'Demo', assistantDockOpen: true, assistantSpeakEnabled: true,
    selectedComponent: { type: 'StorageUnit', name: 'Battery' }, projectMismatch: null })
  useChatStore.setState({ sessionId: 'pilot-session', messages: [], error: null, pending: null, choice: null,
    streaming: false, streamCleanup: null, profileId: null, boundProfileId: null, requestQueue: [],
    sourceGapDraft: null,
    activeRequest: null, lastRequest: null, newChatSeq: 0, suppressHydrationOnce: false, attachedFileIds: [], uploads: [] })
})
afterEach(() => { cleanup(); queryClient?.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

function mount() {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={queryClient}><ChatPanel /></QueryClientProvider>)
}
async function optIn() {
  const toggle = await screen.findByRole('checkbox', { name: 'Check answers' })
  await waitFor(() => expect((toggle as HTMLInputElement).disabled).toBe(false))
  fireEvent.click(toggle)
  return toggle as HTMLInputElement
}
function send(text = 'Give me the key results.') {
  fireEvent.change(screen.getByTestId('chat-input'), { target: { value: text } })
  fireEvent.click(screen.getByTestId('chat-send'))
}
function emit(event: string, data: Record<string, unknown>) {
  const calls = vi.mocked(createChatStream).mock.calls
  act(() => calls.at(-1)![1]({ event, data }))
}
function init() {
  emit('session_init', { session_id: 'pilot-session', turn_id: 'turn-1', model: 'test',
    assurance: { version: 1, mode: 'strict', state: 'checking' } })
}

const preparedGap = () => ({ projectName: 'Demo', chatSequence: useChatStore.getState().newChatSeq,
  message: 'Recheck the exact request shown in the checked follow-up card.',
  checked_scope: { version: 1, profile: 'storage-unit-cost/1', project_id: 'owned-project',
    entity_id: 'Battery', metrics: ['total_cost'], cost_basis: { currency: 'EUR', price_year: 2026, annual_fixed_cost_fraction: 1 } },
  source_gap_ref: { source_id: 'a'.repeat(64), revision: 1, digest: 'b'.repeat(64) } })

it('prepares a separate request and sends it only on explicit per-message checked consent', async () => {
  mount()
  const toggle = await screen.findByRole('checkbox', { name: 'Check answers' }) as HTMLInputElement
  await waitFor(() => expect(toggle.disabled).toBe(false))
  fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Keep my unsent message.' } })
  act(() => {
    useChatStore.setState({ attachedFileIds: ['keep-attachment'] })
    useChatStore.getState().setSourceGapDraft(preparedGap())
  })
  expect(await screen.findByRole('region', { name: 'Prepared checked follow-up' })).toBeTruthy()
  expect(createChatStream).not.toHaveBeenCalled()
  const liveSession = useChatStore.getState().sessionId
  const draft = useChatStore.getState().sourceGapDraft!
  fireEvent.click(screen.getByRole('button', { name: 'Send checked follow-up' }))
  expect(createChatStream).toHaveBeenCalledOnce()
  const request = vi.mocked(createChatStream).mock.calls[0][0]
  expect(request.assurance).toEqual({ version: 1, mode: 'strict' })
  expect(request.checked_scope).toEqual(draft.checked_scope)
  expect(request.source_gap_ref).toEqual(draft.source_gap_ref)
  expect(request.session_id).toBe(liveSession ?? undefined)
  expect(request.attachment_file_ids).toBeUndefined()
  expect(request.message).toBe(draft.message)
  expect((screen.getByTestId('chat-input') as HTMLTextAreaElement).value).toBe('Keep my unsent message.')
  expect(useChatStore.getState().attachedFileIds).toEqual(['keep-attachment'])
  expect(useChatStore.getState().sourceGapDraft).toBeNull()
  expect(useChatStore.getState().requestQueue).toEqual([])
  expect(toggle.checked).toBe(false)
})

it.each(['pilot', 'strict'])('shows the same %s checked selection in in-page chat and its actual request', async mode => {
  capabilities.mode = mode
  const access = (checked: boolean): ChatAccessSnapshot => ({ version: 1, read_only: true, observed_at: 100,
    scope: { project_id: 'project-a', project_name: 'Demo' }, selection: { ui_mode: 'expert', checked_requested: checked },
    restrictions: { mode: 'read_only', revision: 'rule-a' }, confirmation_rules: [], blockers: [],
    project_lock: 'unlocked', session: { id: 'pilot-session', output_tokens_used: 0, turn_in_flight: false },
    checking: { server_mode: mode, effective_for_selection: checked, scope: 'bounded_profiles' },
    limits: { output_tokens_per_turn: 8192, tool_calls_per_turn: 25, output_tokens_per_session: 200000,
      turns_without_reported_usage: 100, daily_project_tokens: null, confirmation_ttl_seconds: 60 } })
  vi.mocked(client.get).mockImplementation(async (url, config) => {
    if (url === '/chat/access') return { data: access(config?.params.checked === true) } as never
    if (url === '/chat/reliability/capabilities') return { data: capabilities } as never
    if (url === '/chat/profiles') return { data: { profiles: [{ id: 'test-model', label: 'Test model',
      wire: 'openai', chat_ready: true }], active_profile_id: 'test-model' } } as never
    return { data: { workflows: [], chat_ready: true } } as never
  })
  vi.spyOn(client, 'post').mockResolvedValue({ data: { thread_id: 'thread-a', generation: 1 } })
  useUIStore.setState({ uiMode: 'expert', assistantSpeakEnabled: false })
  useChatStore.setState({ durableThreadId: null, utteranceGeneration: 1 })
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={queryClient}><AssistantDock /></QueryClientProvider>)
  if (mode === 'pilot') await optIn()
  else await waitFor(() => expect((screen.getByRole('checkbox', { name: 'Check answers' }) as HTMLInputElement).checked).toBe(true))
  fireEvent.click(screen.getByTestId('assistant-dock-collapse'))
  expect(screen.queryByRole('region', { name: 'In-page chat access' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Chat here' }))
  expect(await screen.findByText('Answer checks on for supported profiles')).toBeTruthy()
  expect(screen.getByText('Your rule: Read-only tools')).toBeTruthy()
  expect(createChatStream).not.toHaveBeenCalled()
  fireEvent.change(screen.getByRole('textbox', { name: 'Message the assistant' }), { target: { value: 'Explain this asset.' } })
  fireEvent.click(screen.getByTestId('companion-live-send'))
  await waitFor(() => expect(createChatStream).toHaveBeenCalledOnce())
  expect(vi.mocked(createChatStream).mock.calls[0][0].assurance).toEqual({ version: 1, mode: 'strict' })
  expect(useUIStore.getState().assistantDockOpen).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Close chat here' }))
  expect(screen.queryByRole('region', { name: 'In-page chat access' })).toBeNull()
  expect(useChatStore.getState().streaming).toBe(true)
})

it.each(['disabled', 'shadow'])('never falls back to an unchecked gap send in %s mode', async mode => {
  capabilities.mode = mode
  mount()
  await screen.findByRole('checkbox', { name: 'Check answers' })
  act(() => useChatStore.getState().setSourceGapDraft(preparedGap()))
  const button = await screen.findByRole('button', { name: 'Send checked follow-up' })
  expect((button as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(button)
  expect(createChatStream).not.toHaveBeenCalled()
  expect(useChatStore.getState().sourceGapDraft).not.toBeNull()
})

it.each(['project', 'new chat', 'dismiss'])('clears the prepared request on %s without sending', async change => {
  mount()
  await screen.findByRole('checkbox', { name: 'Check answers' })
  act(() => useChatStore.getState().setSourceGapDraft(preparedGap()))
  await screen.findByRole('region', { name: 'Prepared checked follow-up' })
  if (change === 'dismiss') fireEvent.click(screen.getByRole('button', { name: 'Dismiss follow-up' }))
  else act(() => {
    if (change === 'project') useUIStore.setState({ currentProject: 'Elsewhere' })
    else useChatStore.getState().startNewChat()
  })
  await waitFor(() => expect(useChatStore.getState().sourceGapDraft).toBeNull())
  expect(screen.queryByRole('region', { name: 'Prepared checked follow-up' })).toBeNull()
  expect(createChatStream).not.toHaveBeenCalled()
})
function finish(text = 'StorageUnit Battery: total direct cost 51 EUR.') {
  emit('answer_approved', { version: 1, answer_id: 'answer-1', turn_id: 'turn-1', verdict: 'passed',
    receipt: { policy_version: '1', content_sha256: createHash('sha256').update(text).digest('hex'),
      evidence_ids: ['evidence-1'], checked_block_ids: ['total_cost'], limitations: ['Direct costs only.'],
      reviewer_status: 'not_required', request_complete: true } })
  emit('token', { delta: text })
  emit('turn_done', { answer_id: 'answer-1' })
  return text
}

it('pilot opt-in sends the explicit contract and preserves current selected scope', async () => {
  mount(); await optIn(); send()
  await waitFor(() => expect(createChatStream).toHaveBeenCalledTimes(1))
  const request = vi.mocked(createChatStream).mock.calls[0][0]
  expect(request.assurance).toEqual({ version: 1, mode: 'strict' })
  expect(request.ui_context?.selected_component).toEqual({ class: 'StorageUnit', name: 'Battery' })
  expect(request.checked_scope).toBeUndefined()
})

it('ordinary pilot chat stays ordinary until the user opts in', async () => {
  mount(); await screen.findByRole('checkbox', { name: 'Check answers' }); send()
  expect(vi.mocked(createChatStream).mock.calls[0][0].assurance).toBeUndefined()
})

it.each(['disabled', 'shadow'])('does not offer checking when server mode is %s', async mode => {
  capabilities.mode = mode; mount()
  const toggle = await screen.findByRole('checkbox', { name: 'Check answers' })
  await waitFor(() => expect((toggle as HTMLInputElement).disabled).toBe(true))
  expect((toggle as HTMLInputElement).checked).toBe(false)
  send(); expect(vi.mocked(createChatStream).mock.calls[0][0].assurance).toBeUndefined()
})

it('server-required checks cannot be turned off', async () => {
  capabilities.mode = 'strict'; mount()
  const toggle = await screen.findByRole('checkbox', { name: 'Check answers' })
  await waitFor(() => expect((toggle as HTMLInputElement).checked).toBe(true))
  expect((toggle as HTMLInputElement).disabled).toBe(true)
  send(); expect(vi.mocked(createChatStream).mock.calls[0][0].assurance).toEqual({ version: 1, mode: 'strict' })
})

it('withholds text, thinking and raw tools before the checked acknowledgement', async () => {
  mount(); await optIn(); send()
  emit('token', { delta: unsafe }); emit('thinking', { delta: unsafe })
  emit('tool_result', { tool_name: 'get_component', tool_use_id: 'unapproved', result: { description: unsafe } })
  expect(JSON.stringify(useChatStore.getState().messages)).not.toContain(unsafe)
  expect(useChatStore.getState().messages.filter(m => m.role !== 'user')).toHaveLength(0)
})

it.each([undefined, { version: 2, mode: 'strict', state: 'checking' },
  { version: 1, mode: 'ordinary', state: 'checking' }])('rejects a missing or invalid acknowledgement %j', async assurance => {
  mount(); await optIn(); send()
  emit('session_init', { session_id: 'pilot-session', turn_id: 'turn-1', model: 'test', assurance })
  emit('token', { delta: unsafe }); emit('thinking', { delta: unsafe }); emit('turn_done', {})
  expect(useChatStore.getState().error?.error_kind).toBe('assurance_publication_failed')
  expect(JSON.stringify(useChatStore.getState().messages)).not.toContain(unsafe)
  expect(createChatStream).toHaveBeenCalledTimes(1)
  expect(speechOut.speak).not.toHaveBeenCalled()
})

it('publishes and speaks only the digest-approved answer', async () => {
  mount(); await optIn(); act(() => speech.onFinal?.('Read storage cost.'))
  fireEvent.click(screen.getByTestId('chat-send')); init()
  emit('token', { delta: unsafe }); emit('thinking', { delta: unsafe })
  const text = finish()
  expect(await screen.findByText(text)).toBeTruthy()
  await waitFor(() => expect(speechOut.speak).toHaveBeenCalledWith(text))
  expect(JSON.stringify(useChatStore.getState().messages)).not.toContain(unsafe)
})

it('reports a checked turn that ends without approval', async () => {
  mount(); await optIn(); send(); init(); emit('turn_done', {})
  expect(useChatStore.getState().error?.error_kind).toBe('assurance_publication_failed')
  expect(speechOut.speak).not.toHaveBeenCalled()
})

it('a failed checked HTTP request preserves the opt-in and never retries ordinary chat', async () => {
  mount(); const toggle = await optIn(); send()
  act(() => vi.mocked(createChatStream).mock.calls[0][2]?.(
    Object.assign(new Error('Checks rolled back'), { kind: 'assurance_unavailable' })))
  expect(toggle.checked).toBe(true)
  expect(useChatStore.getState().error?.error_kind).toBe('assurance_unavailable')
  expect(createChatStream).toHaveBeenCalledTimes(1)
})

it('keeps checked retry policy after the user turns off checking for new turns', async () => {
  mount(); const toggle = await optIn(); send(); init(); const text = finish()
  await screen.findByText(text); fireEvent.click(toggle)
  const assistant = useChatStore.getState().messages.find(m => m.answer_id === 'answer-1')!
  fireEvent.click(screen.getByTestId(`chat-retry-${assistant.id}`))
  await waitFor(() => expect(createChatStream).toHaveBeenCalledTimes(2))
  expect(postChatRewind).toHaveBeenCalledTimes(1)
  expect(vi.mocked(createChatStream).mock.calls[1][0].assurance).toEqual({ version: 1, mode: 'strict' })
})

it('retains checked policy for software clarification buttons after opting out of new turns', async () => {
  mount(); const toggle = await optIn(); send(); init()
  emit('choice_request', { title: 'Cost quantity', options: [{ label: 'Total declared-basis cost' }], allow_free_text: false })
  const text = finish('Which cost quantity do you mean?')
  await screen.findByText(text); fireEvent.click(toggle)
  fireEvent.click(screen.getByRole('button', { name: /Total declared-basis cost/ }))
  await waitFor(() => expect(createChatStream).toHaveBeenCalledTimes(2))
  expect(vi.mocked(createChatStream).mock.calls[1][0].assurance).toEqual({ version: 1, mode: 'strict' })
})

it.each(['queued', 'live'])('applies the opt-in to %s sends', async source => {
  mount(); await optIn()
  act(() => {
    if (source === 'live') window.dispatchEvent(new CustomEvent('companion:live-utterance', { detail: 'Check the selected results.' }))
    else useChatStore.getState().sendRequest('Check the selected results.', { source: 'hub-card' })
  })
  await waitFor(() => expect(createChatStream).toHaveBeenCalledTimes(1))
  expect(vi.mocked(createChatStream).mock.calls[0][0].assurance).toEqual({ version: 1, mode: 'strict' })
})

it.each(['project', 'chat'])('resets the opt-in on a new %s', async binding => {
  mount(); const toggle = await optIn()
  act(() => {
    if (binding === 'project') useUIStore.setState({ currentProject: 'Other' })
    else useChatStore.getState().startNewChat()
  })
  await waitFor(() => expect(toggle.checked).toBe(false))
  if (binding === 'project') {
    act(() => useUIStore.setState({ currentProject: 'Demo' }))
    expect(toggle.checked).toBe(false)
  }
})

it('availability rollback preserves the choice and draft until an explicit opt-out', async () => {
  mount(); const toggle = await optIn()
  capabilities = { ...capabilities, mode: 'disabled' }
  await act(async () => { await queryClient.invalidateQueries({ queryKey: ['chat', 'checked-answer-capabilities'] }) })
  expect(toggle.checked).toBe(true)
  send('Keep this checked question.')
  expect(createChatStream).not.toHaveBeenCalled()
  expect((screen.getByTestId('chat-input') as HTMLTextAreaElement).value).toBe('Keep this checked question.')
  expect(useChatStore.getState().error?.error_kind).toBe('assurance_unavailable')
  fireEvent.click(toggle); fireEvent.click(screen.getByTestId('chat-send'))
  expect(vi.mocked(createChatStream).mock.calls[0][0].assurance).toBeUndefined()
})

it('keeps reloaded unavailable checked history strict on retry without marking it approved', async () => {
  vi.mocked(getChatHistory).mockResolvedValue({ turns: [{ ts: 1, session_id: 'pilot-session', model: 'test',
    user: 'Check this storage cost.', requires_checked: true,
    assistant: [{ type: 'text', text: 'Previous checked answer is unavailable.' }],
    usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0 } }],
    last_session_id: 'pilot-session', bound_project: 'Demo', history_gap: 0, pending_turn: null })
  mount(); await screen.findByText('Previous checked answer is unavailable.')
  expect(screen.queryByText('Requested results checked')).toBeNull()
  fireEvent.click(screen.getByTestId('chat-retry-replay-a-1'))
  await waitFor(() => expect(createChatStream).toHaveBeenCalledTimes(1))
  expect(vi.mocked(createChatStream).mock.calls[0][0].assurance).toEqual({ version: 1, mode: 'strict' })
})

it('editing a checked question restores its opt-in', async () => {
  mount(); const toggle = await optIn(); send(); init(); const text = finish()
  await screen.findByText(text); fireEvent.click(toggle)
  const user = useChatStore.getState().messages.find(m => m.role === 'user')!
  fireEvent.click(screen.getByTestId(`chat-edit-${user.id}`))
  await waitFor(() => expect(toggle.checked).toBe(true))
  expect((screen.getByTestId('chat-input') as HTMLTextAreaElement).value).toBe(user.content)
})

it('a retriable checked failure retries the original question with checks', async () => {
  mount(); const toggle = await optIn(); send()
  init(); emit('error', { error_kind: 'rate_limited', message: 'Try again later.' })
  fireEvent.click(toggle)
  fireEvent.click(screen.getByTestId('chat-error-retry'))
  await waitFor(() => expect(createChatStream).toHaveBeenCalledTimes(2))
  expect(vi.mocked(createChatStream).mock.calls[1][0].assurance).toEqual({ version: 1, mode: 'strict' })
})

it.each(['project', 'roundtrip'])('does not clear or resend a changed %s binding while a retry rewind is pending', async binding => {
  let resolve!: () => void
  vi.mocked(postChatRewind).mockImplementationOnce(() => new Promise(done => {
    resolve = () => done({ ok: true, dropped: 2 })
  }))
  mount(); await optIn(); send(); init(); const text = finish(); await screen.findByText(text)
  const assistant = useChatStore.getState().messages.find(m => m.answer_id === 'answer-1')!
  fireEvent.click(screen.getByTestId(`chat-retry-${assistant.id}`))
  act(() => useUIStore.setState({ currentProject: 'Other' }))
  if (binding === 'roundtrip') act(() => useUIStore.setState({ currentProject: 'Demo' }))
  act(() => useChatStore.setState({ messages: [{ id: 'other', role: 'user', content: 'Other project question', ts: 1 }] }))
  await act(async () => resolve())
  expect(useChatStore.getState().messages[0].content).toBe('Other project question')
  expect(createChatStream).toHaveBeenCalledTimes(1)
})

it('a capability-fetch failure stays unavailable until the user retries availability', async () => {
  const normal = vi.mocked(client.get).getMockImplementation()!
  let fail = true
  vi.mocked(client.get).mockImplementation(async url => {
    if (url === '/chat/reliability/capabilities' && fail) throw new Error('Offline')
    return normal(url) as never
  })
  mount(); await screen.findByText('Could not load answer-check availability.')
  const toggle = screen.getByRole('checkbox', { name: 'Check answers' }) as HTMLInputElement
  expect(toggle.disabled).toBe(true); expect(toggle.checked).toBe(false)
  fail = false; fireEvent.click(screen.getByRole('button', { name: 'Retry answer-check availability' }))
  await waitFor(() => expect(toggle.disabled).toBe(false))
  expect(toggle.checked).toBe(false)
  expect(createChatStream).not.toHaveBeenCalled()
})

it('a pilot with all calculation profiles rolled back cannot advertise checks', async () => {
  capabilities.profiles = { 'storage-unit-cost/1': { enabled: false } }; mount()
  await screen.findByText('Calculation checks are unavailable.')
  const toggle = screen.getByRole('checkbox', { name: 'Check answers' }) as HTMLInputElement
  expect(toggle.disabled).toBe(true); expect(toggle.checked).toBe(false)
})

it('allows ordinary new messages after an explicit opt-out from pilot checking', async () => {
  mount(); const toggle = await optIn(); send(); init(); await screen.findByText(finish())
  fireEvent.click(toggle); send('An ordinary new question.')
  expect(vi.mocked(createChatStream).mock.calls[1][0].assurance).toBeUndefined()
  emit('session_init', { session_id: 'pilot-session', model: 'test' })
  emit('token', { delta: 'An ordinary reply.' }); emit('turn_done', {})
  expect(await screen.findByText('An ordinary reply.')).toBeTruthy()
})

it('does not replay a refused queued request when checks become available again', async () => {
  mount(); await optIn(); capabilities = { ...capabilities, mode: 'disabled' }
  await act(async () => { await queryClient.invalidateQueries({ queryKey: ['chat', 'checked-answer-capabilities'] }) })
  act(() => { useChatStore.getState().sendRequest('A checked queued request.', { source: 'hub-card' }) })
  await waitFor(() => expect(useChatStore.getState().error?.error_kind).toBe('assurance_unavailable'))
  expect(createChatStream).not.toHaveBeenCalled()
  capabilities = { ...capabilities, mode: 'pilot' }
  await act(async () => { await queryClient.invalidateQueries({ queryKey: ['chat', 'checked-answer-capabilities'] }) })
  expect(createChatStream).not.toHaveBeenCalled()
  expect(useChatStore.getState().requestQueue).toHaveLength(0)
})

it('rejects a second initialization instead of replacing checked publication state', async () => {
  mount(); await optIn(); send(); init(); init()
  emit('token', { delta: unsafe }); emit('turn_done', {})
  expect(useChatStore.getState().error?.error_kind).toBe('assurance_publication_failed')
  expect(JSON.stringify(useChatStore.getState().messages)).not.toContain(unsafe)
})
