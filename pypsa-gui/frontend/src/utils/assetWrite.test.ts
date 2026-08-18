/**
 * assetWrite — the module that owns the Asset-write idiom (see CONTEXT.md).
 *
 * Task 1 seeds it with the pieces the chat-staleness defect fix needs:
 * the component→query-key table, the mutation-tier predicate, and the
 * blanket invalidation. The update() chokepoint itself arrives in Task 3
 * and grows FROM these — decision 1 of the 2026-08-14 grilling.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient } from '@tanstack/react-query'

// Mock the api client BEFORE assetWrite imports it. The factory parks the
// mock object on globalThis because vi.mock hoists above every const in
// this module scope (TDZ) — tests reach it through the `api()` helper.
vi.mock('../api/network', () => {
  const m = {
    getBuses: vi.fn(), updateBus: vi.fn(),
    getCarriers: vi.fn(), updateCarrier: vi.fn(),
    getLines: vi.fn(), updateLine: vi.fn(),
    getLinks: vi.fn(), updateLink: vi.fn(),
    getGenerators: vi.fn(), updateGenerator: vi.fn(),
    getStorageUnits: vi.fn(), updateStorageUnit: vi.fn(),
    getStores: vi.fn(), updateStore: vi.fn(),
    getLoads: vi.fn(), updateLoad: vi.fn(),
    getTransformers: vi.fn(), updateTransformer: vi.fn(),
  }
  ;(globalThis as never as { __awMock: typeof m }).__awMock = m
  return { networkApi: m }
})

import {
  COMPONENT_QUERY_ROOTS,
  isMutatingTier,
  invalidateAssetQueries,
} from './assetWrite'
import { nk } from './queryKeys'

describe('COMPONENT_QUERY_ROOTS', () => {
  it('covers every component class the api client can update, plus meta', () => {
    // Derived from networkApi's update surface (api/network.ts): a class that
    // can be written must be invalidatable, or a chat edit to it goes stale —
    // the exact defect this module exists to close. `meta` rides along because
    // component counts render in the status bar.
    for (const root of [
      'buses', 'carriers', 'lines', 'links', 'generators',
      'storage_units', 'stores', 'loads', 'transformers', 'meta',
    ]) {
      expect(COMPONENT_QUERY_ROOTS, `missing root: ${root}`).toContain(root)
    }
  })
})

describe('isMutatingTier', () => {
  it('read is the only non-mutating tier', () => {
    expect(isMutatingTier('read')).toBe(false)
    expect(isMutatingTier('write')).toBe(true)
    expect(isMutatingTier('destructive')).toBe(true)
    expect(isMutatingTier('execution')).toBe(true)
    expect(isMutatingTier('execution_long_running')).toBe(true)
  })

  it('fails SAFE on an unknown or absent tier — a spurious refetch beats a silent revert', () => {
    expect(isMutatingTier(undefined)).toBe(true)
    expect(isMutatingTier(null)).toBe(true)
    expect(isMutatingTier('')).toBe(true)
    expect(isMutatingTier('some_future_tier')).toBe(true)
  })
})

describe('invalidateAssetQueries', () => {
  it('invalidates every component root under the given project key', () => {
    const qc = new QueryClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')

    invalidateAssetQueries(qc, 'proj-a')

    expect(spy).toHaveBeenCalledTimes(COMPONENT_QUERY_ROOTS.length)
    for (const root of COMPONENT_QUERY_ROOTS) {
      expect(spy).toHaveBeenCalledWith({ queryKey: nk('proj-a', root) })
    }
  })

  it('works for a null project (desktop single-project mode keys)', () => {
    const qc = new QueryClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    invalidateAssetQueries(qc, null)
    expect(spy).toHaveBeenCalledWith({ queryKey: nk(null, 'generators') })
  })
})

describe('updateAsset — the chokepoint (Task 3)', () => {
  // vi.mock is hoisted; the factory refs live on globalThis to dodge TDZ.
  const api = () => (globalThis as never as { __awMock: Record<string, ReturnType<typeof vi.fn>> }).__awMock

  beforeEach(() => vi.clearAllMocks())

  it('spreads the cached row under the patch — a partial PUT is unrepresentable', async () => {
    const { updateAsset } = await import('./assetWrite')
    const qc = new QueryClient()
    qc.setQueryData(nk('p1', 'generators'), [
      { name: 'g1', p_nom: 100, marginal_cost: 50, carrier: 'gas' },
    ])
    api().updateGenerator.mockResolvedValue({})

    await updateAsset(qc, 'p1', 'generators', 'g1', { p_nom: 120 })

    expect(api().updateGenerator).toHaveBeenCalledWith('g1', {
      name: 'g1', p_nom: 120, marginal_cost: 50, carrier: 'gas',
    })
  })

  it('cold cache: fetches the list first, then spreads — no bare-fields path exists', async () => {
    const { updateAsset } = await import('./assetWrite')
    const qc = new QueryClient()
    api().getGenerators.mockResolvedValue([
      { name: 'g1', p_nom: 100, marginal_cost: 50, carrier: 'gas' },
    ])
    api().updateGenerator.mockResolvedValue({})

    await updateAsset(qc, 'p1', 'generators', 'g1', { marginal_cost: 60 })

    expect(api().getGenerators).toHaveBeenCalled()
    expect(api().updateGenerator).toHaveBeenCalledWith('g1', {
      name: 'g1', p_nom: 100, marginal_cost: 60, carrier: 'gas',
    })
  })

  it('real absence: throws and issues NO put', async () => {
    const { updateAsset } = await import('./assetWrite')
    const qc = new QueryClient()
    qc.setQueryData(nk('p1', 'generators'), [{ name: 'other', p_nom: 1 }])

    await expect(
      updateAsset(qc, 'p1', 'generators', 'ghost', { p_nom: 5 }),
    ).rejects.toThrow(/ghost/)
    expect(api().updateGenerator).not.toHaveBeenCalled()
  })

  it('invalidates the component families after the PUT', async () => {
    const { updateAsset } = await import('./assetWrite')
    const qc = new QueryClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    qc.setQueryData(nk('p1', 'buses'), [{ name: 'b1', v_nom: 380 }])
    api().updateBus.mockResolvedValue({})

    await updateAsset(qc, 'p1', 'buses', 'b1', { v_nom: 220 })

    expect(spy).toHaveBeenCalledWith({ queryKey: nk('p1', 'buses') })
  })

  it('a failed PUT propagates and does NOT invalidate (state unchanged, cache still true)', async () => {
    const { updateAsset } = await import('./assetWrite')
    const qc = new QueryClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    qc.setQueryData(nk('p1', 'loads'), [{ name: 'l1', p_set: 10 }])
    api().updateLoad.mockRejectedValue(new Error('422'))

    await expect(
      updateAsset(qc, 'p1', 'loads', 'l1', { p_set: 20 }),
    ).rejects.toThrow('422')
    expect(spy).not.toHaveBeenCalled()
  })
})

describe('updateAsset with a patch BUILDER', () => {
  const api = () => (globalThis as never as { __awMock: Record<string, ReturnType<typeof vi.fn>> }).__awMock

  beforeEach(() => vi.clearAllMocks())

  it('hands the current row to the builder and spreads its return', async () => {
    // The PropertiesPanel mappings need `current` for their fallbacks
    // (nf(form, 'p_nom', current.p_nom)) — the builder form keeps that
    // per-form knowledge at the call site while the chokepoint still owns
    // fetch, spread, PUT and invalidation.
    const { updateAsset } = await import('./assetWrite')
    const qc = new QueryClient()
    qc.setQueryData(nk('p1', 'generators'), [
      { name: 'g1', p_nom: 100, marginal_cost: 50, carrier: 'gas' },
    ])
    api().updateGenerator.mockResolvedValue({})

    await updateAsset(qc, 'p1', 'generators', 'g1',
      (current) => ({ p_nom: (current.p_nom as number) * 2 }))

    expect(api().updateGenerator).toHaveBeenCalledWith('g1', {
      name: 'g1', p_nom: 200, marginal_cost: 50, carrier: 'gas',
    })
  })

  it('builder + cold cache: fetches first, builder sees the FETCHED row', async () => {
    const { updateAsset } = await import('./assetWrite')
    const qc = new QueryClient()
    api().getLoads.mockResolvedValue([{ name: 'l1', p_set: 10, carrier: 'AC' }])
    api().updateLoad.mockResolvedValue({})

    await updateAsset(qc, 'p1', 'loads', 'l1',
      (current) => ({ p_set: (current.p_set as number) + 5 }))

    expect(api().updateLoad).toHaveBeenCalledWith('l1', {
      name: 'l1', p_set: 15, carrier: 'AC',
    })
  })
})

describe('updateAsset returns the PUT response', () => {
  const api = () => (globalThis as never as { __awMock: Record<string, ReturnType<typeof vi.fn>> }).__awMock

  beforeEach(() => vi.clearAllMocks())

  it('resolves with the api response body — the Bus card consumes data.rescale', async () => {
    // updateBus responds {name, rescale: RescalePreview[]}; the Bus card
    // feeds `data.rescale` into ingestRescale. A chokepoint that swallowed
    // the response would silently drop that preview — the exact regression
    // the 2026-08-09 architecture report told this module to prevent.
    const { updateAsset } = await import('./assetWrite')
    const qc = new QueryClient()
    qc.setQueryData(nk('p1', 'buses'), [{ name: 'b1', v_nom: 380 }])
    api().updateBus.mockResolvedValue({ name: 'b1', rescale: [{ line: 'L1' }] })

    const resp = await updateAsset(qc, 'p1', 'buses', 'b1', { v_nom: 220 })

    expect(resp).toEqual({ name: 'b1', rescale: [{ line: 'L1' }] })
  })
})
