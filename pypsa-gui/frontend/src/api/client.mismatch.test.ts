// A2 (deferred spec 2026-09-28 §2.1, "Write block (axios)"): while the tab's
// project and the backend's binding disagree, every axios write from the tab
// is refused client-side — no request leaves — with an AxiosError-shaped 409
// `{detail: {error_kind: 'project_mismatch', message}}`. Reads always pass
// (`projectsApi.load` is a GET, so the Reload button can never deadlock), and
// the allowlist keeps the way out open: `POST /projects/<x>/activate` (Switch),
// `/chat/*`, `/local-settings/*`, `/auth/*`, plus the tab's own layout PUT.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios'

const toastError = vi.fn()
vi.mock('react-hot-toast', () => ({
  default: { error: (...a: unknown[]) => toastError(...a), success: vi.fn(), dismiss: vi.fn() },
  toast: { error: (...a: unknown[]) => toastError(...a), success: vi.fn(), dismiss: vi.fn() },
}))

const client = (await import('./client')).default
const { useUIStore } = await import('../store/uiStore')
const { useSimulationStore } = await import('../store/simulationStore')

const adapter = vi.fn<AxiosAdapter>(async (config: InternalAxiosRequestConfig) => ({
  data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config,
}))
const original = client.defaults.adapter

beforeEach(() => {
  adapter.mockClear()
  toastError.mockReset()
  client.defaults.adapter = adapter
  useUIStore.setState({ currentProject: 'X', projectMismatch: { tab: 'X', backend: 'Y' } })
})
afterEach(() => {
  client.defaults.adapter = original
  useUIStore.setState({ projectMismatch: null })
})

function refusal(e: unknown) {
  return (e as { response?: { status?: number; data?: { detail?: { error_kind?: string; message?: string } } } })
    .response
}

describe('the client-side write block while the tab and the backend disagree', () => {
  it('PUT /network/buses/a is refused client-side with project_mismatch and no request leaves', async () => {
    const err = await client.put('/network/buses/a', { name: 'a' }).catch(e => e)
    expect(refusal(err)?.status).toBe(409)
    expect(refusal(err)?.data?.detail?.error_kind).toBe('project_mismatch')
    expect(refusal(err)?.data?.detail?.message).toBe(
      'This tab shows X, but the app is now on Y. Changes from this tab are paused.')
    expect(adapter).not.toHaveBeenCalled()
  })

  it.each(['post', 'delete', 'patch'] as const)('%s writes are refused too', async (m) => {
    const err = await (client[m] as (u: string) => Promise<unknown>)('/network/lines/L1').catch(e => e)
    expect(refusal(err)?.data?.detail?.error_kind).toBe('project_mismatch')
    expect(adapter).not.toHaveBeenCalled()
  })

  it('the refusal is quiet (the banner is the surface) and logged once', async () => {
    const before = useSimulationStore.getState().logLines?.length ?? 0
    await client.put('/network/buses/a', {}).catch(() => undefined)
    expect(toastError).not.toHaveBeenCalled()
    const lines = (useSimulationStore.getState().logLines ?? []).slice(before)
      .map((l: unknown) => JSON.stringify(l))
    expect(lines.filter(l => l.includes('project_mismatch'))).toHaveLength(1)
  })

  it('GET /projects/X passes (reads are never blocked)', async () => {
    await expect(client.get('/projects/X')).resolves.toBeTruthy()
    expect(adapter).toHaveBeenCalledTimes(1)
  })

  it('POST /projects/Y/activate passes (the Switch button)', async () => {
    await expect(client.post('/projects/Y/activate')).resolves.toBeTruthy()
    expect(adapter).toHaveBeenCalledTimes(1)
  })

  it('PUT /projects/X/layout passes (the tab\'s own layout)', async () => {
    await expect(client.put('/projects/X/layout', {})).resolves.toBeTruthy()
    expect(adapter).toHaveBeenCalledTimes(1)
  })

  it('PUT /projects/Y/layout (not the tab\'s project) is refused', async () => {
    const err = await client.put('/projects/Y/layout', {}).catch(e => e)
    expect(refusal(err)?.data?.detail?.error_kind).toBe('project_mismatch')
    expect(adapter).not.toHaveBeenCalled()
  })

  it('POST /projects/X (a save) is refused', async () => {
    const err = await client.post('/projects/X').catch(e => e)
    expect(refusal(err)?.data?.detail?.error_kind).toBe('project_mismatch')
  })

  it.each([
    ['post', '/chat/s1/confirm'],
    ['put', '/chat/settings/llm/profiles/p'],
    ['put', '/local-settings/anthropic-key'],
    ['post', '/auth/logout'],
  ] as const)('%s %s passes (allowlisted)', async (m, url) => {
    await expect((client[m] as (u: string) => Promise<unknown>)(url)).resolves.toBeTruthy()
    expect(adapter).toHaveBeenCalledTimes(1)
  })

  // P27b gate B1: the multi-user edit-lock routes move lease metadata, not the
  // live network. Refusing them left the banner's Switch read-only in auth
  // mode (acquire refused → "locked-by-user") and cost a mismatched tab its
  // lock at the next 45 s heartbeat.
  it.each([
    ['post', '/projects/Y/lock'],
    ['delete', '/projects/X/lock'],
    ['post', '/projects/X/lock/heartbeat'],
    ['post', '/projects/My%20Project/lock'],
  ] as const)('%s %s passes (the edit lock, B1)', async (m, url) => {
    await expect((client[m] as (u: string) => Promise<unknown>)(url)).resolves.toBeTruthy()
    expect(adapter).toHaveBeenCalledTimes(1)
  })

  it('a lock-looking route with more segments is still refused', async () => {
    const err = await client.post('/projects/X/lock/steal').catch(e => e)
    expect(refusal(err)?.data?.detail?.error_kind).toBe('project_mismatch')
  })

  it('without a mismatch every write passes', async () => {
    useUIStore.setState({ projectMismatch: null })
    await expect(client.put('/network/buses/a', {})).resolves.toBeTruthy()
    expect(adapter).toHaveBeenCalledTimes(1)
  })
})
