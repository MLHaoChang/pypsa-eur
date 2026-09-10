// The two /results/mc api-client functions. The axios client is mocked so the
// test pins the CONVENTIONS (204 → null, bare POST body, `.data` unwrapping)
// rather than the transport — same shape of check the frontier/copt getters
// rely on implicitly.
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()

vi.mock('./client', () => ({
  default: { get, post, put: vi.fn(), delete: vi.fn() },
  formatApiDetail: (d: unknown, fallback = 'Unknown error') =>
    (typeof d === 'string' ? d : d == null ? fallback : String(d)),
}))

const { resultsApi } = await import('./simulation')

beforeEach(() => {
  get.mockReset()
  post.mockReset()
})

describe('resultsApi.getMc', () => {
  it('maps 204 (never run this session) to null, not to an empty payload', async () => {
    get.mockResolvedValue({ status: 204, data: '' })
    await expect(resultsApi.getMc()).resolves.toBeNull()
    expect(get).toHaveBeenCalledWith('/results/mc')
  })

  it('returns the stored payload verbatim otherwise', async () => {
    const payload = {
      status: 'done',
      result: {
        engine: 'mc', fidelity: 'sequential_mc',
        metrics: { lole_hours: 1, lole_ci: [0.9, 1.1], eue_mwh: 2,
          eue_ci: [1.8, 2.2], n_samples: 500, time_basis: 'hours_per_year',
          horizon_years: 1, resolution_floor_h: 0.002 },
        elcc: [], warning: 'w',
      },
      error: null, started_at: 1, finished_at: 2,
    }
    get.mockResolvedValue({ status: 200, data: payload })
    await expect(resultsApi.getMc()).resolves.toEqual(payload)
  })
})

describe('resultsApi.startMc', () => {
  it('posts an empty body when no options are given', async () => {
    post.mockResolvedValue({ status: 200, data: { status: 'running' } })
    await expect(resultsApi.startMc()).resolves.toEqual({ status: 'running' })
    expect(post).toHaveBeenCalledWith('/results/mc', {})
  })

  it('forwards draws / seed / cov_target / elcc_assets', async () => {
    post.mockResolvedValue({ status: 200, data: { status: 'running' } })
    const body = {
      draws: 250, seed: 7, cov_target: 0.02,
      elcc_assets: [{ kind: 'generator', name: 'CCGT-1' }],
    }
    await resultsApi.startMc(body)
    expect(post).toHaveBeenCalledWith('/results/mc', body)
  })
})
