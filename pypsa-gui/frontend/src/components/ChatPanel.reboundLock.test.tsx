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
const { acquireProjectLock, stopLockHeartbeat, releaseProjectLock, lastHeldLockProject, LOCK_HEARTBEAT_MS,
  switchToProject } = await import('../utils/projectActions')
const { createChatStream } = await import('../api/chat')
const ChatPanel = (await import('./ChatPanel')).default

const seen: string[] = []
const lockedByOther = new Set<string>()
// P28 gate N-a / N-b: hold one reply so it lands out of order.
let holdHeartbeat: Promise<void> | null = null
let holdY: Promise<void> | null = null
const adapter = vi.fn<AxiosAdapter>(async (config: InternalAxiosRequestConfig) => {
  const url = config.url ?? ''
  seen.push(`${config.method} ${url}`)
  if (holdHeartbeat && url.endsWith('/X/lock/heartbeat')) await holdHeartbeat
  if (holdY && url === '/projects/Y/lock' && config.method === 'post') await holdY
  const m = /^\/projects\/([^/]+)\/lock(\/heartbeat)?$/.exec(url)
  if (m && config.method === 'post' && lockedByOther.has(decodeURIComponent(m[1]))) {
    return Promise.reject(Object.assign(new Error('Request failed with status code 409'), {
      config, response: { status: 409, data: { detail: { lock: { holder_email: 'bob@x' } } } },
    }))
  }
  let data: unknown = { ok: true }
  const act = /^\/projects\/([^/]+)\/activate$/.exec(url)
  if (act) data = { activated: decodeURIComponent(act[1]), evicted: [] }
  else if (url.includes('lock_status')) data = { lock_held: false, worker_alive: false }
  else if (m) data = { lock: { holder_email: 'me@x', yours: true } }
  else if (url === '/projects' || url === '/projects/') data = []
  return { data, status: 200, statusText: 'OK', headers: {}, config }
})
const original = client.defaults.adapter

type Frame = { event: string; data: Record<string, unknown> }

beforeEach(async () => {
  lockedByOther.clear()
  holdHeartbeat = null
  holdY = null
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
  for (const p of ['X', 'Y', 'Z']) await releaseProjectLock(p).catch(() => {})
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

// P28 gate N-a / N-b (adopted from the reviewer's `QA28.race.test.tsx`):
// lock replies that land out of order. A move bumps a lock generation; a
// heartbeat, re-acquire or acquire reply from an older generation is ignored
// (an acquire that won a lock nobody wants any more gives it back).
describe('lock replies that arrive after a later move are ignored (P28 gate N-a, N-b)', () => {
  it('N-a: a heartbeat for X answered AFTER the rebind to a foreign-locked Y leaves the tab read-only', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    stopLockHeartbeat(); await acquireProjectLock('X'); seen.length = 0
    let release!: () => void
    holdHeartbeat = new Promise<void>(r => { release = r })
    await vi.advanceTimersByTimeAsync(LOCK_HEARTBEAT_MS + 10)
    expect(seen).toContain('post /projects/X/lock/heartbeat')
    lockedByOther.add('Y')
    await rebound('X', 'Y', 'activate_project')
    await waitFor(() => expect(useUIStore.getState().readOnly).toBe(true))
    release(); holdHeartbeat = null
    await new Promise(r => setTimeout(r, 50))
    expect(useUIStore.getState().readOnly).toBe(true)
    expect(useUIStore.getState().readOnlyReason).toBe('locked-by-user')
  })

  it('N-a: a heartbeat 409 for X answered after the move re-claims nothing', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    stopLockHeartbeat(); await acquireProjectLock('X'); seen.length = 0
    let release!: () => void
    holdHeartbeat = new Promise<void>(r => { release = r })
    await vi.advanceTimersByTimeAsync(LOCK_HEARTBEAT_MS + 10)
    lockedByOther.add('X')   // X's heartbeat will come back 409
    await rebound('X', 'Y', 'activate_project')
    await waitFor(() => expect(seen).toContain('post /projects/Y/lock'))
    const n = seen.length
    release(); holdHeartbeat = null
    await new Promise(r => setTimeout(r, 50))
    expect(seen.slice(n)).not.toContain('post /projects/X/lock')
    expect(useUIStore.getState().readOnly).toBe(false)
    expect(lastHeldLockProject()).toBe('Y')
  })

  it('N-a via switchToProject: a stale heartbeat success does not make a foreign-locked target writable', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    stopLockHeartbeat(); await acquireProjectLock('X'); seen.length = 0
    let release!: () => void
    holdHeartbeat = new Promise<void>(r => { release = r })
    await vi.advanceTimersByTimeAsync(LOCK_HEARTBEAT_MS + 10)
    lockedByOther.add('Y')
    const qc = new QueryClient()
    await switchToProject('Y', qc)
    await waitFor(() => expect(useUIStore.getState().readOnly).toBe(true))
    release(); holdHeartbeat = null
    await new Promise(r => setTimeout(r, 50))
    expect(useUIStore.getState().readOnly).toBe(true)
  })

  it("N-b: X → Y → Z in one turn with Y's acquire answered last: the heartbeat follows Z, Y is given back", async () => {
    let release!: () => void
    holdY = new Promise<void>(r => { release = r })
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><ChatPanel /></QueryClientProvider>)
    vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
      for (const f of [
        { event: 'session_init', data: { session_id: 's1' } },
        { event: 'project_rebound', data: { from: 'X', to: 'Y', via_tool: 'create_project_from_template' } },
        { event: 'project_rebound', data: { from: 'Y', to: 'Z', via_tool: 'save_project_as' } },
        { event: 'turn_done', data: {} },
      ] as Frame[]) onFrame(f as never)
      return () => {}
    })
    const user = userEvent.setup()
    await user.type(screen.getByTestId('chat-input'), 'go')
    await user.click(screen.getByTestId('chat-send'))
    await waitFor(() => expect(seen).toContain('post /projects/Z/lock'))
    await new Promise(r => setTimeout(r, 30))
    const n = seen.length
    release(); holdY = null
    await new Promise(r => setTimeout(r, 50))
    expect(useUIStore.getState().currentProject).toBe('Z')
    expect(lastHeldLockProject()).toBe('Z')
    expect(useUIStore.getState().readOnly).toBe(false)
    // The late acquire won Y after the move had released it: give it back.
    expect(seen.slice(n)).toContain('delete /projects/Y/lock')
  })

  it('an unbind (release only) with a heartbeat 409 for X in flight re-claims nothing', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    stopLockHeartbeat(); await acquireProjectLock('X'); seen.length = 0
    let release!: () => void
    holdHeartbeat = new Promise<void>(r => { release = r })
    await vi.advanceTimersByTimeAsync(LOCK_HEARTBEAT_MS + 10)
    lockedByOther.add('X')
    await rebound('X', null, 'import_network_nc')
    await waitFor(() => expect(seen).toContain('delete /projects/X/lock'))
    const n = seen.length
    release(); holdHeartbeat = null
    await new Promise(r => setTimeout(r, 50))
    expect(seen.slice(n)).not.toContain('post /projects/X/lock')
  })
})
