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
  version: 2 as const, action_id: 'a-1', turn_id: 't-1', binding_generation: 0,
  project_name: 'Demo', project_id: 'p-1', input_sha256: 'i'.repeat(64),
  target: { panel_id: 'results', results_tab: 'loadflow' },
}

// LC-03 (LC-09 item 3): the browser proves what an ordinary turn's
// navigation shows, not only a checked turn's. Without that ACK the copilot
// can only say it opened the tab.
it('acknowledges source-bound navigation in an ordinary turn and says it can', async () => {
  vi.mocked(acknowledgeMountedView).mockClear()
  vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
    onFrame({ event: 'session_init', data: { session_id: 'sess-ack' } })
    onFrame({ event: 'ui_event', data: { kind: 'navigate', panel_id: 'results', results_tab: 'loadflow',
      tool_use_id: 'tu-1', acknowledgement } })
    return () => {}
  })
  renderPanel()
  const user = userEvent.setup()
  await user.type(screen.getByTestId('chat-input'), 'show me the load flow')
  await user.click(screen.getByTestId('chat-send'))
  await waitFor(() => expect(acknowledgeMountedView).toHaveBeenCalledTimes(1))
  expect(vi.mocked(acknowledgeMountedView).mock.calls[0][0]).toEqual(acknowledgement)
  expect(vi.mocked(createChatStream).mock.calls[0][0]).toMatchObject({ ui_ack: true })
  expect(useUIStore.getState().activeSlidePanel).toBe('results')
})
