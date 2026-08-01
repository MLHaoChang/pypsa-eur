import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatStream, postChatConfirm, type ChatFrame } from '../api/chat'
import ChatPanel from './ChatPanel'

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(),
    getChatHistory: vi.fn().mockResolvedValue({ turns: [], last_session_id: null, bound_project: null }),
    postChatAbort: vi.fn(),
    postChatConfirm: vi.fn().mockResolvedValue({ ok: true }),
  }
})

vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(),
  getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]),
  uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  useChatStore.setState({ sessionId: null, pending: null, messages: [] })
  vi.mocked(postChatConfirm).mockClear()
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
}

async function sendAndScript(scriptedFrames: ChatFrame[]) {
  vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
    for (const f of scriptedFrames) onFrame(f)
    return () => {}
  })
  const user = userEvent.setup()
  await user.type(screen.getByTestId('chat-input'), 'hello')
  await user.click(screen.getByTestId('chat-send'))
}

it('renders tool_request and tool_result frames in the transcript', async () => {
  renderPanel()
  await sendAndScript([
    { event: 'session_init', data: { session_id: 'sess-1' } },
    { event: 'tool_request', data: { tool_name: 'list_projects', tool_use_id: 'tu1' } },
    { event: 'tool_result', data: { tool_name: 'list_projects', tool_use_id: 'tu1' } },
    { event: 'turn_done', data: {} },
  ])
  expect(await screen.findByText('→ list_projects')).toBeTruthy()
  expect(await screen.findByText('✓ list_projects')).toBeTruthy()
})

it('renders an approve/reject control for tool_pending_confirmation and calls postChatConfirm with confirmation_token on approve', async () => {
  renderPanel()
  await sendAndScript([
    { event: 'session_init', data: { session_id: 'sess-2' } },
    {
      event: 'tool_pending_confirmation',
      data: {
        tool_use_id: 'tu2',
        tool_name: 'delete_component',
        args: { component_class: 'Generator', name: 'SmokeSolar' },
        safety_tier: 'write',
        confirmation_token: 'tok-abc',
        ttl_seconds: 120,
      },
    },
  ])
  const card = await screen.findByTestId('chat-confirmation-card')
  expect(card).toBeTruthy()
  const approve = screen.getByTestId('chat-confirm-approve')
  // 'delete_component' is not in ChatPanel.tsx's TYPED_CONFIRMATION_TOOLS
  // set (only delete_project/save_project/save_project_as/
  // restore_project_snapshot/cascade_delete_bus require typed confirmation),
  // so Approve is enabled immediately with no typed input needed.
  expect((approve as HTMLButtonElement).disabled).toBe(false)
  const user = userEvent.setup()
  await user.click(approve)
  expect(postChatConfirm).toHaveBeenCalledWith('sess-2', {
    token: 'tok-abc',
    decision: 'approve',
  })
})
