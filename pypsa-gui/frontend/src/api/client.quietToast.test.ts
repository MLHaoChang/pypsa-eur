// The axios interceptor's quiet-toast codes (whole-branch review S5 fix,
// second review pass). A structured refusal carries its kind as
// `detail.error_kind` — the solver-in-flight and study-in-flight 409s — while
// the middleware 409 carries a top-level `code`. The first version of the
// fix added `study_in_flight` to the quiet set but the interceptor only read
// `data.code`, so the autosave and the pre-switch save still toasted the
// study sentence every time. Both fields name a quiet code now.
import { beforeEach, describe, expect, it, vi } from 'vitest'

const toastError = vi.fn()
vi.mock('react-hot-toast', () => ({
  default: { error: (...a: unknown[]) => toastError(...a), success: vi.fn(), dismiss: vi.fn() },
  toast: { error: (...a: unknown[]) => toastError(...a), success: vi.fn(), dismiss: vi.fn() },
}))

const appLog = vi.fn()
vi.mock('../store/simulationStore', async (orig) => ({
  ...(await orig<typeof import('../store/simulationStore')>()),
  appLog: (...a: unknown[]) => appLog(...a),
}))

const client = (await import('./client')).default

type Rejected = (err: unknown) => Promise<never>

function rejectedHandler(): Rejected {
  const handlers = (client.interceptors.response as unknown as {
    handlers: Array<{ rejected?: Rejected }>
  }).handlers
  const h = handlers.map((x) => x.rejected).find(Boolean)
  if (!h) throw new Error('no rejected handler registered')
  return h
}

function axiosError(status: number, data: unknown, url = '/projects/demo', method = 'post') {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data },
    config: { url, method },
  })
}

beforeEach(() => {
  toastError.mockReset()
  appLog.mockReset()
})

describe('quiet-toast codes', () => {
  it('a study_in_flight refusal (detail.error_kind) does not toast', async () => {
    await expect(
      rejectedHandler()(
        axiosError(409, {
          detail: { error_kind: 'study_in_flight', study: 'frontier', message: 'Cannot save …' },
        }),
      ),
    ).rejects.toBeTruthy()
    expect(toastError).not.toHaveBeenCalled()
  })

  it('a solver_in_flight refusal (detail.error_kind) does not toast', async () => {
    await expect(
      rejectedHandler()(
        axiosError(409, { detail: { error_kind: 'solver_in_flight', message: 'Cannot save …' } }),
      ),
    ).rejects.toBeTruthy()
    expect(toastError).not.toHaveBeenCalled()
  })

  it('a top-level middleware code still names a quiet code', async () => {
    await expect(
      rejectedHandler()(axiosError(409, { code: 'solver_in_flight', detail: 'busy' })),
    ).rejects.toBeTruthy()
    expect(toastError).not.toHaveBeenCalled()
  })

  it('any other 409 detail still toasts once', async () => {
    await expect(
      rejectedHandler()(axiosError(409, { detail: 'a sequential-MC study is already running' })),
    ).rejects.toBeTruthy()
    expect(toastError).toHaveBeenCalledTimes(1)
    expect(String(toastError.mock.calls[0][0])).toContain('sequential-MC')
  })
})

// P30 (B8): a caller that shows the failure itself (`skipErrorToast`) still
// leaves a line in the app log — at INFO, never ERROR, and never a toast.
describe('quiet failures (skipErrorToast)', () => {
  it('quiet failure logs INFO', async () => {
    const err = axiosError(500, { detail: 'boom' }, '/results/eh_study', 'get')
    ;(err.config as Record<string, unknown>).skipErrorToast = true
    await expect(rejectedHandler()(err)).rejects.toBeTruthy()
    expect(toastError).not.toHaveBeenCalled()
    expect(appLog).toHaveBeenCalledTimes(1)
    expect(appLog.mock.calls[0][0]).toBe('INFO')
    expect(String(appLog.mock.calls[0][1])).toContain('GET /results/eh_study')
    expect(String(appLog.mock.calls[0][1])).toContain('boom')
  })

  it('a loud failure still logs ERROR and toasts', async () => {
    await expect(rejectedHandler()(axiosError(500, { detail: 'boom' }, '/results/eh_study', 'get')))
      .rejects.toBeTruthy()
    expect(toastError).toHaveBeenCalledTimes(1)
    expect(appLog.mock.calls.map(c => c[0])).toEqual(['ERROR'])
  })
})
