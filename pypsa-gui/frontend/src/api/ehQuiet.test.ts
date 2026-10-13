// The `{quiet}` → `skipErrorToast` mapping of the two EH reads the hub-design
// panel shares with the Expert panel (P24-FE re-gate B4; P25 step 0). The
// hub reads quietly and shows its own error line; the Expert panel reads
// loudly and keeps the global toast. The axios client is mocked so the test
// pins the exact request config each call passes.
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()

vi.mock('./client', () => ({
  default: { get, post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  formatApiDetail: (d: unknown, fallback = 'Unknown error') =>
    (typeof d === 'string' ? d : d == null ? fallback : String(d)),
}))

const { resultsApi } = await import('./simulation')

beforeEach(() => {
  get.mockReset()
  get.mockResolvedValue({ status: 204, data: '' })
})

describe('resultsApi.getEhStudy quiet mapping', () => {
  it('reads loudly by default (the Expert panel keeps its toast)', async () => {
    await resultsApi.getEhStudy()
    expect(get).toHaveBeenCalledTimes(1)
    const [url, config] = get.mock.calls[0]
    expect(url).toBe('/results/eh_study')
    expect(config?.skipErrorToast).toBeUndefined()
  })

  it('reads loudly with quiet: false', async () => {
    await resultsApi.getEhStudy({ quiet: false })
    expect(get.mock.calls[0][1]?.skipErrorToast).toBeUndefined()
  })

  it('passes skipErrorToast with quiet: true (the hub panel)', async () => {
    await resultsApi.getEhStudy({ quiet: true })
    expect(get).toHaveBeenCalledWith('/results/eh_study', { skipErrorToast: true })
  })
})

describe('resultsApi.getEhTemplate quiet mapping', () => {
  it('reads loudly by default', async () => {
    await resultsApi.getEhTemplate('p one')
    expect(get).toHaveBeenCalledTimes(1)
    const [url, config] = get.mock.calls[0]
    expect(url).toBe('/projects/p%20one/eh_template')
    expect(config?.skipErrorToast).toBeUndefined()
  })

  it('reads loudly with quiet: false', async () => {
    await resultsApi.getEhTemplate('p', { quiet: false })
    expect(get.mock.calls[0][1]?.skipErrorToast).toBeUndefined()
  })

  it('passes skipErrorToast with quiet: true', async () => {
    await resultsApi.getEhTemplate('p', { quiet: true })
    expect(get).toHaveBeenCalledWith('/projects/p/eh_template', { skipErrorToast: true })
  })
})
