// P28 (P32 gate N6, data safety): in auth mode a chat-driven rebind — the
// assistant creates, imports, opens or saves-as another project and the
// backend announces `project_rebound {from, to}` — moved the tab to `to`
// without touching the edit lock. The tab kept heart-beating the OLD
// project's lock (locking a teammate out of a project nobody here edits) and
// edited the NEW one holding no lock at all, so a second user could open it
// writable too. A rebind now does what `switchToProject` does: release the
// outgoing lock, acquire the target's, and fall to read-only ("locked-by-
// user") when someone else holds it — the project stays open for viewing.
//
// Harness as ProjectMismatchBanner.lock.test.tsx: real `client`, real
// `projectActions` lock code, an adapter answering every request that leaves.
// The chat API module is mocked (the stream is driven by frames).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), dismiss: vi.fn(), loading: vi.fn() }),
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), dismiss: vi.fn(), loading: vi.fn() }),
}))
vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(),
    getChatHistory: vi.fn().mockResolvedValue({ turns: [], last_session_id: null, bound_project: null }),
    getChatHealth: vi.fn().mockResolvedValue({ ok: true, chat_ready: true }),
    postChatAbort: vi.fn(),
    postChatConfirm: vi.fn().mockResolvedValue({ ok: true }),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: true, source: 'env', hint: null, overridden_by_environment: false, storage_path: '/tmp/u.env',
    }),
  }
})
// The greeting reads meta / status through the same client; it is not what
// this file tests.
vi.mock('./ChatLaunchGreeting', () => ({ default: () => null }))
vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(),
  getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]),
  uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))

const { setAuthEnabled } = await import('../auth/config')
const client = (await import('../api/client')).default
const { useUIStore } = await import('../store/uiStore')
const { useChatStore } = await import('../store/chatStore')
const { WRITABLE } = await import('../utils/lockState')
const { acquireProjectLock, stopLockHeartbeat, releaseProjectLock, lastHeldLockProject, LOCK_HEARTBEAT_MS } =
  await import('../utils/projectActions')
const { createChatStream } = await import('../api/chat')
const ChatPanel = (await import('./ChatPanel')).default

const seen: string[] = []
const lockedByOther = new Set<string>()
const adapter = vi.fn<AxiosAdapter>(async (config: InternalAxiosRequestConfig) => {
  const url = config.url ?? ''
  seen.push(`${config.method} ${url}`)
  const m = /^\/projects\/([^/]+)\/lock(\/heartbeat)?$/.exec(url)
  if (m && config.method === 'post' && lockedByOther.has(decodeURIComponent(m[1]))) {
    return Promise.reject(Object.assign(new Error('Request failed with status code 409'), {
      config, response: { status: 409, data: { detail: { lock: { holder_email: 'bob@x' } } } },
    }))
  }
  let data: unknown = { ok: true }
  if (m) data = { lock: { holder_email: 'me@x', yours: true } }
  else if (url === '/projects' || url === '/projects/') data = []
  return { data, status: 200, statusText: 'OK', headers: {}, config }
})
const original = client.defaults.adapter

type Frame = { event: string; data: Record<string, unknown> }

beforeEach(async () => {
  lockedByOther.clear()
  setAuthEnabled(true)
  client.defaults.adapter = adapter
  useUIStore.setState({ currentProject: 'X', projectMismatch: null, assistantDockOpen: false,
    activeSlidePanel: null, uiMode: 'expert', uiModeExplicit: true })
  useUIStore.getState().setLockState(WRITABLE)
  useChatStore.setState({ sessionId: null, pending: null, messages: [], streaming: false, streamCleanup: null })
  await acquireProjectLock('X')
  seen.length = 0
})
afterEach(async () => {
  cleanup()
  vi.useRealTimers()
  stopLockHeartbeat()
  for (const p of ['X', 'Y']) await releaseProjectLock(p).catch(() => {})
  setAuthEnabled(false)
  client.defaults.adapter = original
})

async function rebound(from: string | null, to: string | null, via_tool: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><ChatPanel /></QueryClientProvider>)
  vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
    for (const f of [
      { event: 'session_init', data: { session_id: 's1' } },
      { event: 'project_rebound', data: { from, to, via_tool } },
      { event: 'turn_done', data: {} },
    ] as Frame[]) onFrame(f as never)
    return () => {}
  })
  const user = userEvent.setup()
  await user.type(screen.getByTestId('chat-input'), 'go')
  await user.click(screen.getByTestId('chat-send'))
}

describe('a chat-driven project rebind moves the edit lock (auth mode, P32 N6)', () => {
  it.each(['create_project_from_template', 'import_project_bundle', 'activate_project',
    'load_project', 'save_project_as'])(
    '%s X → Y: releases X, acquires Y, lands writable on Y', async (tool) => {
      await rebound('X', 'Y', tool)
      await waitFor(() => expect(useUIStore.getState().currentProject).toBe('Y'))
      await waitFor(() => expect(seen).toContain('post /projects/Y/lock'))
      expect(seen).toContain('delete /projects/X/lock')
      expect(useUIStore.getState().readOnly).toBe(false)
      expect(lastHeldLockProject()).toBe('Y')
    })

  it('the heartbeat follows the new project, not the old one', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await rebound('X', 'Y', 'create_project_from_template')
    await waitFor(() => expect(seen).toContain('post /projects/Y/lock'))
    seen.length = 0
    await vi.advanceTimersByTimeAsync(LOCK_HEARTBEAT_MS + 10)
    expect(seen).toContain('post /projects/Y/lock/heartbeat')
    expect(seen).not.toContain('post /projects/X/lock/heartbeat')
  })

  it('someone else holds Y → read-only (locked by them), X still released, Y stays open for viewing', async () => {
    lockedByOther.add('Y')
    await rebound('X', 'Y', 'activate_project')
    await waitFor(() => expect(useUIStore.getState().readOnly).toBe(true))
    expect(useUIStore.getState().readOnlyReason).toBe('locked-by-user')
    expect(useUIStore.getState().currentProject).toBe('Y')
    expect(seen).toContain('delete /projects/X/lock')
    expect(seen.filter(s => s === 'post /projects/Y/lock')).toHaveLength(1)
  })

  it('a network import that unbinds (to: null) releases X and claims nothing', async () => {
    await rebound('X', null, 'import_network_nc')
    await waitFor(() => expect(useUIStore.getState().currentProject).toBeNull())
    await waitFor(() => expect(seen).toContain('delete /projects/X/lock'))
    expect(seen.some(s => s.startsWith('post /projects/') && s.endsWith('/lock'))).toBe(false)
    expect(lastHeldLockProject()).toBeNull()
  })

  it('a same-project frame moves no lock', async () => {
    await rebound('X', 'X', 'restore_project_snapshot')
    await new Promise(r => setTimeout(r, 50))
    expect(seen.some(s => /\/lock$/.test(s))).toBe(false)
    expect(lastHeldLockProject()).toBe('X')
  })

  it('auth disabled (local mode): no lock traffic at all', async () => {
    stopLockHeartbeat()
    setAuthEnabled(false)
    await rebound('X', 'Y', 'create_project_from_template')
    await waitFor(() => expect(useUIStore.getState().currentProject).toBe('Y'))
    await new Promise(r => setTimeout(r, 50))
    expect(seen.some(s => s.includes('/lock'))).toBe(false)
  })
})
