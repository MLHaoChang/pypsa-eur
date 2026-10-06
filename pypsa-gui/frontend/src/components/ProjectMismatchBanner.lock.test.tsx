// P27b gate B1 (adopted from the gate's probes `probeLock.test.tsx` and
// `probeHeartbeat.test.tsx`): auth mode is the default build, and the
// mismatch write block refused the multi-user edit-lock routes. The banner's
// Switch then landed read-only ("locked-by-user", heartbeat stopped), and a
// mismatch longer than one heartbeat (45 s) lost the tab its lock for good —
// Reload never re-acquired it. Real client, real switchToProject, real lock
// code; an adapter answers every request that leaves.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), dismiss: vi.fn(), loading: vi.fn() }),
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), dismiss: vi.fn(), loading: vi.fn() }),
}))
const { setAuthEnabled } = await import('../auth/config')
const client = (await import('../api/client')).default
const { useUIStore } = await import('../store/uiStore')
const { WRITABLE } = await import('../utils/lockState')
const { acquireProjectLock, stopLockHeartbeat, releaseProjectLock, LOCK_HEARTBEAT_MS } =
  await import('../utils/projectActions')
const Banner = (await import('./ProjectMismatchBanner')).default

const seen: string[] = []
let lockRefused = false
const adapter = vi.fn<AxiosAdapter>(async (config: InternalAxiosRequestConfig) => {
  const url = config.url ?? ''
  seen.push(`${config.method} ${url}`)
  if (lockRefused && /\/lock(\/heartbeat)?$/.test(url) && config.method === 'post') {
    return Promise.reject(Object.assign(new Error('Request failed with status code 409'), {
      config, response: { status: 409, data: { detail: { lock: { holder_email: 'other@x' } } } },
    }))
  }
  let data: unknown = { ok: true }
  if (url.includes('/activate')) data = { activated: 'Y', evicted: [] }
  else if (/\/lock(\/heartbeat)?$/.test(url)) data = { lock: { holder_email: 'me@x', yours: true } }
  else if (url.includes('lock_status')) data = { lock_held: false, worker_alive: false }
  else if (url === '/projects' || url === '/projects/') data = []
  return { data, status: 200, statusText: 'OK', headers: {}, config }
})
const original = client.defaults.adapter

beforeEach(() => {
  seen.length = 0
  lockRefused = false
  setAuthEnabled(true)
  client.defaults.adapter = adapter
  useUIStore.setState({ currentProject: 'X', projectMismatch: null })
  useUIStore.getState().setLockState(WRITABLE)
})
afterEach(async () => {
  vi.useRealTimers()
  stopLockHeartbeat()
  await releaseProjectLock('X').catch(() => {})
  setAuthEnabled(false)
  client.defaults.adapter = original
  useUIStore.setState({ projectMismatch: null })
})

function mountBanner() {
  const qc = new QueryClient()
  render(<QueryClientProvider client={qc}><Banner /></QueryClientProvider>)
}

describe('the edit lock across a tab / backend mismatch (auth mode, B1)', () => {
  it('banner Switch lands writable on Y: the lock is acquired, not refused client-side', async () => {
    useUIStore.setState({ projectMismatch: { tab: 'X', backend: 'Y' } })
    mountBanner()
    fireEvent.click(screen.getByTestId('project-mismatch-switch'))
    await waitFor(() => expect(useUIStore.getState().currentProject).toBe('Y'))
    await waitFor(() => expect(useUIStore.getState().projectMismatch).toBeNull())
    expect(seen).toContain('post /projects/Y/lock')
    expect(useUIStore.getState().readOnly).toBe(false)
    expect(useUIStore.getState().readOnlyReason).not.toBe('locked-by-user')
  })

  it('the heartbeat keeps the lock while mismatched, and after Reload', async () => {
    vi.useFakeTimers()
    await acquireProjectLock('X')
    expect(useUIStore.getState().readOnly).toBe(false)
    useUIStore.setState({ projectMismatch: { tab: 'X', backend: 'Y' } })
    await vi.advanceTimersByTimeAsync(LOCK_HEARTBEAT_MS + 10)
    expect(seen).toContain('post /projects/X/lock/heartbeat')
    expect(useUIStore.getState().readOnly).toBe(false)
    useUIStore.getState().setProjectMismatch(null)
    await vi.advanceTimersByTimeAsync(LOCK_HEARTBEAT_MS * 2)
    expect(useUIStore.getState().readOnly).toBe(false)
  })

  it('Reload re-acquires the edit lock the tab had held (lost meanwhile)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await acquireProjectLock('X')
    // the lock is lost while mismatched (e.g. it expired and was taken meanwhile)
    lockRefused = true
    useUIStore.setState({ projectMismatch: { tab: 'X', backend: 'Y' } })
    await vi.advanceTimersByTimeAsync(LOCK_HEARTBEAT_MS + 10)
    await waitFor(() => expect(useUIStore.getState().readOnly).toBe(true))
    lockRefused = false
    seen.length = 0
    mountBanner()
    await act(async () => { fireEvent.click(screen.getByTestId('project-mismatch-reload')) })
    await waitFor(() => expect(useUIStore.getState().projectMismatch).toBeNull())
    await waitFor(() => expect(seen).toContain('post /projects/X/lock'))
    await waitFor(() => expect(useUIStore.getState().readOnly).toBe(false))
  })

  it('Reload does not claim a lock the tab never held', async () => {
    useUIStore.setState({ projectMismatch: { tab: 'X', backend: 'Y' } })
    mountBanner()
    await act(async () => { fireEvent.click(screen.getByTestId('project-mismatch-reload')) })
    await waitFor(() => expect(useUIStore.getState().projectMismatch).toBeNull())
    expect(seen.some(s => s.endsWith('/lock'))).toBe(false)
  })
})
