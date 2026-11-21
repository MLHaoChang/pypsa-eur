// The 03a supply-architecture and 03d grid-extension thunks map one to one onto
// `backend/routers/studies.py` (`/supply-architectures`, `/abort`, the study
// PATCH's `settings`) and `backend/routers/grid_extension.py`. Pinned against the
// axios client with the network mocked out: the URL (names are encoded), the body
// and the quiet flag (each refusal renders in the card, never a toast).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import client from './client'
import { decisionStudiesApi as api, type GridExtensionRequest } from './decisionStudies'

const get = vi.spyOn(client, 'get')
const post = vi.spyOn(client, 'post')
const patch = vi.spyOn(client, 'patch')
const quiet = expect.objectContaining({ skipErrorToast: true })
const STUDY = '/projects/Island%20A/studies/s%201'

beforeEach(() => {
  for (const m of [get, post, patch]) m.mockResolvedValue({ data: { ok: true } } as never)
})
afterEach(() => vi.clearAllMocks())

describe('decisionStudiesApi: grid extension vs island', () => {
  it('starts the comparison with the optional body and returns the record', async () => {
    expect(await api.startSupplyArchitectures('Island A', 's 1')).toEqual({ ok: true })
    expect(post).toHaveBeenCalledWith(`${STUDY}/supply-architectures`, {}, quiet)
    await api.startSupplyArchitectures('Island A', 's 1', { architectures: ['pv'], budget_solves: 3 })
    expect(post).toHaveBeenLastCalledWith(`${STUDY}/supply-architectures`,
      { architectures: ['pv'], budget_solves: 3 }, quiet)
  })

  it('reads and stops the comparison', async () => {
    await api.supplyArchitectures('Island A', 's 1')
    expect(get).toHaveBeenCalledWith(`${STUDY}/supply-architectures`, quiet)
    await api.abortSupplyArchitectures('Island A', 's 1')
    expect(post).toHaveBeenCalledWith(`${STUDY}/supply-architectures/abort`, {}, quiet)
  })

  it('asks for the break-even distance with the request as the body', async () => {
    const body: GridExtensionRequest = {
      architecture_id: 'pv+genset', eur_per_km: 20000, grid_supply_npc: 1000, fixed_eur: 0, max_km: 10000,
      grid_basis: { currency: 'EUR', currency_year: 2025, discount_rate: 0.07, horizon_years: 25 },
    }
    await api.gridExtensionBreakEven('Island A', 's 1', body)
    expect(post).toHaveBeenCalledWith(`${STUDY}/grid-extension-break-even`, body, quiet)
  })

  it('sets the study currency year through the PATCH settings', async () => {
    await api.patchSettings('Island A', 's 1', { currency_year: 2025 })
    expect(patch).toHaveBeenCalledWith(STUDY, { settings: { currency_year: 2025 } }, quiet)
  })
})
