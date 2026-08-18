// The chat-staleness defect (asset-write-chokepoint plan, Task 2).
//
// ChatPanel invalidated only meta/simulationStatus/snapshots, and only on
// project_rebound — NONE of the 64 mutating chat tools invalidated any
// component-level query. The compounding failure needs no race: the agent
// edits a generator, the `generators` cache keeps the pre-edit row, the
// user's next manual edit spreads that stale row into its PUT, and the
// backend's remove+add cycle writes the pre-agent values back. Silent
// revert of the agent's work.
//
// Ruling 2 (2026-08-14 grilling): tier-keyed blanket. The `tool_request`
// frame carries `safety_tier` (chat_service.py:1442, maintained because it
// gates confirmation cards); `tool_result` does not, so the handler
// remembers the tier by tool_use_id. Any non-`read` completion invalidates
// every COMPONENT_QUERY_ROOTS family — no per-tool table to rot.
//
// Own render harness rather than ChatPanel.test.tsx's `renderPanel()`:
// these tests must spy on the QueryClient, which the shared helper
// constructs internally and does not expose. Mock surface mirrors that
// file's setup — the panel's mount effects call getChatHistory /
// getApiKeySettings / listUploads, and an unmocked one turns every test
// here into a hang.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { COMPONENT_QUERY_ROOTS } from '../utils/assetWrite'
import { nk } from '../utils/queryKeys'
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

/** The component-family roots (for project 'Demo') this spy saw invalidated. */
function invalidatedRoots(spy: ReturnType<typeof renderWithSpy>): string[] {
  const keyOf = (root: string) => JSON.stringify(nk('Demo', root))
  const seen = spy.mock.calls
    .map((c) => JSON.stringify((c[0] as { queryKey?: unknown })?.queryKey))
  return COMPONENT_QUERY_ROOTS.filter((root) => seen.includes(keyOf(root)))
}

describe('chat tool mutations invalidate the component caches', () => {
  it('a write-tier tool_result invalidates every component family', async () => {
    const spy = renderWithSpy()
    await sendFrames([
      { event: 'session_init', data: { session_id: 's1' } },
      { event: 'tool_request', data: { tool_name: 'update_component', tool_use_id: 'tu1', safety_tier: 'write' } },
      { event: 'tool_result', data: { tool_name: 'update_component', tool_use_id: 'tu1' } },
      { event: 'turn_done', data: {} },
    ])
    const roots = invalidatedRoots(spy)
    for (const root of COMPONENT_QUERY_ROOTS) {
      expect(roots, `root ${root} was not invalidated`).toContain(root)
    }
  })

  it('a read-tier tool_result invalidates nothing component-level', async () => {
    const spy = renderWithSpy()
    await sendFrames([
      { event: 'session_init', data: { session_id: 's2' } },
      { event: 'tool_request', data: { tool_name: 'list_projects', tool_use_id: 'tu2', safety_tier: 'read' } },
      { event: 'tool_result', data: { tool_name: 'list_projects', tool_use_id: 'tu2' } },
      { event: 'turn_done', data: {} },
    ])
    expect(invalidatedRoots(spy)).toHaveLength(0)
  })

  it('a tool_result whose tool_request was never seen fails SAFE and invalidates', async () => {
    const spy = renderWithSpy()
    await sendFrames([
      { event: 'session_init', data: { session_id: 's3' } },
      { event: 'tool_result', data: { tool_name: 'mystery_tool', tool_use_id: 'tu-unseen' } },
      { event: 'turn_done', data: {} },
    ])
    expect(invalidatedRoots(spy)).toContain('generators')
  })

  it('a mutating tool_error also invalidates — a failed write may have partially applied', async () => {
    const spy = renderWithSpy()
    await sendFrames([
      { event: 'session_init', data: { session_id: 's4' } },
      { event: 'tool_request', data: { tool_name: 'delete_component', tool_use_id: 'tu4', safety_tier: 'destructive' } },
      { event: 'tool_error', data: { tool_name: 'delete_component', tool_use_id: 'tu4', error_kind: 'internal_error', message: 'boom' } },
      { event: 'turn_done', data: {} },
    ])
    expect(invalidatedRoots(spy)).toContain('generators')
  })
})
