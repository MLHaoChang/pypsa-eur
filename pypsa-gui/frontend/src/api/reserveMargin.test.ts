// `GET /results/reserve_margin` through the api client (Phase 8 §4/§6).
//
// The axios client is mocked, so what this pins is the CONVENTION — 204 →
// null, `.data` unwrapped verbatim — and the KEY NAMES, which are copied from
// the backend (`services/adequacy/report.py::reserve_margin_payload` +
// `sanitize_reserve_margin_payload`, `models/adequacy.py`). A renamed key here
// would fork the contract silently: the panel is the only reader, so nothing
// else would go red.
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()

vi.mock('./client', () => ({
  default: { get, post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  formatApiDetail: (d: unknown, fallback = 'Unknown error') =>
    (typeof d === 'string' ? d : d == null ? fallback : String(d)),
}))

const { resultsApi } = await import('./simulation')

beforeEach(() => { get.mockReset() })

describe('resultsApi.getReserveMargin', () => {
  it('maps 204 (nothing solved / no margin set) to null, not to an empty payload', async () => {
    get.mockResolvedValue({ status: 204, data: '' })
    await expect(resultsApi.getReserveMargin()).resolves.toBeNull()
    expect(get).toHaveBeenCalledWith('/results/reserve_margin')
  })

  it('returns the persisted stash verbatim, every backend key intact', async () => {
    const payload = {
      margin: 0.15,
      horizon_wide: true,
      by_period: [{
        period: 'ALL',
        peak_mw: 150,
        required_mw: 172.5,
        firm_mw: 180,
        margin_achieved: 0.2,
        met: true,
        binding: false,
        n_peak_hours: 1,
        peak_snapshots: ['2030-01-01 00:00:00'],
        max_achievable_mw: null,
        max_achievable_unbounded: true,
      }],
      assets: [{
        name: 'peaker', period: 'ALL', kind: 'generator',
        capacity_mw: 40, derate: 0.95, basis: 'FOR',
        source: 'carrier_default', extendable: true, firm_mw: 38,
        energy_limited: false,
      }],
      derating_bases: { EFORd: 2, FOR: 1 },
    }
    get.mockResolvedValue({ status: 200, data: payload })
    await expect(resultsApi.getReserveMargin()).resolves.toEqual(payload)
  })
})
