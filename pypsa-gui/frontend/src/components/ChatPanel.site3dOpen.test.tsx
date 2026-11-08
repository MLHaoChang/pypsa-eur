import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import {
  createChatStream,
  getChatHistory,
  postChatConfirm,
  putApiKeySettings,
  type ChatFrame,
} from '../api/chat'
import ChatPanel from './ChatPanel'
import { acknowledgeMountedView } from '../utils/verifiedNavigation'

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(),
    getChatHistory: vi.fn().mockResolvedValue({ turns: [], last_session_id: null, bound_project: null }),
    postChatAbort: vi.fn(),
    postChatConfirm: vi.fn().mockResolvedValue({ ok: true }),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: false,
      source: null,
      hint: null,
      overridden_by_environment: false,
      storage_path: '/tmp/user.env',
    }),
    putApiKeySettings: vi.fn(),
    deleteApiKeySettings: vi.fn(),
    // Task 15: ApiKeySetup (rendered inline for `missing_api_key`) now reads
    // this to decide inline-form vs. deep-link. `importOriginal` would
    // otherwise leave it real, hitting a live network call from inside this
    // suite. A builtin-Anthropic active profile keeps every test below on
    // the pinned inline-form path.
    getChatHealth: vi.fn().mockResolvedValue({
      ok: true,
      anthropic_api_key_present: false,
      default_model: 'claude-sonnet-5',
      confirmation_ttl_seconds: 300,
      active_profile: { id: 'anthropic-sonnet', label: 'Claude Sonnet', wire: 'anthropic' },
      chat_ready: true,
    }),
  }
})

vi.mock('../utils/verifiedNavigation', () => ({ acknowledgeMountedView: vi.fn(() => () => {}) }))

vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(),
  getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]),
  uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null, assistantDockOpen: false })
  // `streaming` and `streamCleanup` have to be reset too: `onSend` returns
  // early while `streaming` is true, so a test that leaves it set makes every
  // later test's send a silent no-op — which reads as "the component ignored
  // my click" rather than as pollution.
  useChatStore.setState({
    sessionId: null, pending: null, messages: [],
    streaming: false, streamCleanup: null,
  })
  vi.mocked(postChatConfirm).mockClear()
  // `createChatStream` accumulates across every `it()` in this file — each one
  // sends at least once — so an assertion on its call COUNT is otherwise
  // order-dependent. `mockClear` resets calls only; the per-test
  // `mockImplementation` in `sendAndScript` is unaffected.
  vi.mocked(createChatStream).mockClear()
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
}

const acknowledgement = {
  version: 1 as const, action_id: 'a-1', turn_id: 't-1', binding_generation: 0,
  project_name: 'Demo', project_id: 'p-1', input_sha256: 'i'.repeat(64),
  target: { panel_id: 'map', canvas_view: 'site' as const },
}

// SC-07 S5b R6 (UX condition 5; GK B V3): a chat navigate into Site 3D says
// "Opened" only once its browser ACK completes.
async function openSite3d(withAck: boolean) {
  vi.mocked(acknowledgeMountedView).mockClear()
  vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
    onFrame({ event: 'session_init', data: { session_id: 'sess-site' } })
    onFrame({ event: 'tool_result', data: { tool_use_id: 'tu-1', tool_name: 'ui_open_panel',
      result: { ok: true, ui_navigated: true, kind: 'navigate', panel_id: 'map', canvas_view: 'site' } } } as ChatFrame)
    onFrame({ event: 'ui_event', data: { kind: 'navigate', panel_id: 'map', canvas_view: 'site', tool_use_id: 'tu-1',
      ...(withAck ? { acknowledgement } : {}) } } as ChatFrame)
    return () => {}
  })
  renderPanel()
  const user = userEvent.setup()
  await user.type(screen.getByTestId('chat-input'), 'open the 3D site')
  await user.click(screen.getByTestId('chat-send'))
  return () => screen.getByTestId('chat-site3d-open').textContent
}

it.each(['guided', 'expert'] as const)('%s: pending, then Opened only on the ACK', async mode => {
  useUIStore.setState({ uiMode: mode })
  const words = await openSite3d(true)
  await waitFor(() => expect(acknowledgeMountedView).toHaveBeenCalledTimes(1))
  expect(useUIStore.getState().canvasView).toBe('site')
  expect(words()).toBe('Opening the 3D site…')
  const [, , onVerified] = vi.mocked(acknowledgeMountedView).mock.calls[0]
  act(() => onVerified!())
  await waitFor(() => expect(words()).toBe('Opened the 3D site'))
})

it.each(['guided', 'expert'] as const)('%s: an ACK that cannot complete says it could not be confirmed', async mode => {
  useUIStore.setState({ uiMode: mode })
  const words = await openSite3d(true)
  await waitFor(() => expect(acknowledgeMountedView).toHaveBeenCalledTimes(1))
  const [, , , onUnverified] = vi.mocked(acknowledgeMountedView).mock.calls[0]
  act(() => onUnverified!())
  await waitFor(() => expect(words()).toBe('Could not confirm the 3D site opened'))
})

it('with no ACK issued (no network open) it is never Opened', async () => {
  const words = await openSite3d(false)
  await waitFor(() => expect(words()).toBe('Could not confirm the 3D site opened'))
  expect(acknowledgeMountedView).not.toHaveBeenCalled()
})
