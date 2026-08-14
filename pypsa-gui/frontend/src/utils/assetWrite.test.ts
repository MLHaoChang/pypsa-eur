/**
 * assetWrite — the module that owns the Asset-write idiom (see CONTEXT.md).
 *
 * Task 1 seeds it with the pieces the chat-staleness defect fix needs:
 * the component→query-key table, the mutation-tier predicate, and the
 * blanket invalidation. The update() chokepoint itself arrives in Task 3
 * and grows FROM these — decision 1 of the 2026-08-14 grilling.
 */
import { describe, it, expect, vi } from 'vitest'
import { QueryClient } from '@tanstack/react-query'

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
