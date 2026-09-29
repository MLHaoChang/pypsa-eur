// ChatPanel's `project_rebound` handler (P27a gate finding 3; P27b).
//
// Harness copied from ChatPanel.invalidation.test.tsx (the panel's mount
// effects call getChatHistory / getApiKeySettings / listUploads, and an
// unmocked one hangs the test), plus a callable react-hot-toast mock so the
// rebind toasts can be read.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { COMPONENT_QUERY_ROOTS } from '../utils/assetWrite'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(),
    getChatHistory: vi.fn().mockResolvedValue({ turns: [], last_session_id: null, bound_project: null }),
    postChatAbort: vi.fn(),
    postChatConfirm: vi.fn().mockResolvedValue({ ok: true }),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: true,
      source: 'env',
      hint: null,
      overridden_by_environment: false,
      storage_path: '/tmp/user.env',
    }),
    putApiKeySettings: vi.fn(),
    deleteApiKeySettings: vi.fn(),
  }
})


const { toastFn } = vi.hoisted(() => {
  const fn = Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), dismiss: vi.fn(), loading: vi.fn() })
  return { toastFn: fn }
})
vi.mock('react-hot-toast', () => ({ default: toastFn, toast: toastFn }))

vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(),
  getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]),
  uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))

import { createChatStream } from '../api/chat'
import ChatPanel from './ChatPanel'

type Frame = { event: string; data: Record<string, unknown> }

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null, assistantDockOpen: false })
  useChatStore.setState({
    sessionId: null, pending: null, messages: [],
    streaming: false, streamCleanup: null,
  })
  vi.mocked(createChatStream).mockClear()
})

function renderWithSpy() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const spy = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
  return spy
}

async function sendFrames(frames: Frame[]) {
  vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
    for (const f of frames) onFrame(f as never)
    return () => {}
  })
  const user = userEvent.setup()
  await user.type(screen.getByTestId('chat-input'), 'hello')
  await user.click(screen.getByTestId('chat-send'))
}


// P27a gate finding 3, carried to P27b: a network import run by the assistant
// (`import_network_nc`, `import_csv_bundle`, `import_excel`, `import_matpower`)
// replaces the backend's network with an UNBOUND draft and announces it as
// `project_rebound {to: null}`. The handler used to ignore a null `to` for the
// store, so the tab kept showing — and autosaving under — the old project,
// while the backend now held the imported network (the identity guard lets an
// unbound network through, so the next autosave would overwrite the old
// project's folder with the import). The tab now shows the unbound state:
// `currentProject` is cleared, and one toast says the network is not saved.
describe('project_rebound with to: null (a network import unbinds the tab)', () => {
  beforeEach(() => { toastFn.mockClear() })

  it('clears currentProject and says the imported network is not saved to a project', async () => {
    renderWithSpy()
    await sendFrames([
      { event: 'session_init', data: { session_id: 's1' } },
      { event: 'project_rebound', data: { from: 'Demo', to: null, via_tool: 'import_network_nc' } },
      { event: 'turn_done', data: {} },
    ])
    expect(useUIStore.getState().currentProject).toBeNull()
    const said = toastFn.mock.calls.map(c => String(c[0]))
    expect(said).toContain(
      "The assistant replaced the network — it is no longer 'Demo' and is not saved to a project yet. Save it under a name to keep it.")
  })

  it('a null → null frame (already unbound) changes nothing and toasts nothing', async () => {
    useUIStore.setState({ currentProject: null })
    renderWithSpy()
    await sendFrames([
      { event: 'session_init', data: { session_id: 's2' } },
      { event: 'project_rebound', data: { from: null, to: null, via_tool: 'import_excel' } },
      { event: 'turn_done', data: {} },
    ])
    expect(useUIStore.getState().currentProject).toBeNull()
    expect(toastFn).not.toHaveBeenCalled()
  })

  it('the control: a named rebind still moves the tab', async () => {
    renderWithSpy()
    await sendFrames([
      { event: 'session_init', data: { session_id: 's3' } },
      { event: 'project_rebound', data: { from: 'Demo', to: 'Other', via_tool: 'activate_project' } },
      { event: 'turn_done', data: {} },
    ])
    expect(useUIStore.getState().currentProject).toBe('Other')
    expect(toastFn.mock.calls.map(c => String(c[0]))).toContain('Active project: Other')
  })
})
