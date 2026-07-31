// Regression coverage for the rescale-preview ingest/apply logic lifted out
// of MapCanvasInner into a shared module (2026-07-31 review, Finding 1).
// Before the lift this logic was untestable in isolation — it only existed
// as closures inside a 1300-line Leaflet component. Mirrors the behaviour
// `MapCanvas.tsx` relied on before the lift: immaterial changes auto-apply,
// material/blocked changes queue for the dialog, a failed auto-apply
// re-queues instead of vanishing, and — the property this fix exists to
// guarantee — every caller that feeds a preview in gets the SAME treatment
// regardless of which surface produced it.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { useRescaleStore } from '../store/rescaleStore'
import { applyRescale, ingestRescale } from './rescaleActions'
import type { RescalePreview } from './rescale'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, rescaleImpedances: vi.fn() } }
})

const preview = (over: Partial<RescalePreview>): RescalePreview => ({
  name: 'L1',
  old_length: 1, new_length: 1,
  old: { r: 3, x: 17.5, b: 0.00015 },
  new: { r: 3.1, x: 18.0, b: 0.000155 },
  rel_change: 0,
  skipped_reason: null,
  ...over,
})

function freshClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
}

beforeEach(() => {
  vi.mocked(networkApi.rescaleImpedances).mockReset()
  useUIStore.setState({ currentProject: 'Demo' })
  useRescaleStore.setState({ pendingRescale: [], placementActive: false })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
  useRescaleStore.setState({ pendingRescale: [], placementActive: false })
})

describe('ingestRescale', () => {
  it('does nothing for an empty or undefined batch — no API call, no queue', () => {
    ingestRescale(freshClient(), undefined)
    ingestRescale(freshClient(), [])
    expect(networkApi.rescaleImpedances).not.toHaveBeenCalled()
    expect(useRescaleStore.getState().pendingRescale).toEqual([])
  })

  it('auto-applies an immaterial change without touching the queue', async () => {
    vi.mocked(networkApi.rescaleImpedances).mockResolvedValue({ updated: 1, skipped: [] })
    ingestRescale(freshClient(), [preview({ rel_change: 0.01 })])

    await vi.waitFor(() => expect(networkApi.rescaleImpedances).toHaveBeenCalledTimes(1))
    expect(networkApi.rescaleImpedances).toHaveBeenCalledWith([
      { name: 'L1', r: 3.1, x: 18.0, b: 0.000155 },
    ])
    expect(useRescaleStore.getState().pendingRescale).toEqual([])
  })

  it('queues a material change for the dialog instead of applying it', () => {
    ingestRescale(freshClient(), [preview({ name: 'MAT', rel_change: 2.5 })])

    expect(networkApi.rescaleImpedances).not.toHaveBeenCalled()
    expect(useRescaleStore.getState().pendingRescale.map(p => p.name)).toEqual(['MAT'])
  })

  it('queues a blocked line instead of applying it, regardless of rel_change', () => {
    // A blocked line must never be silently applied even if partitionRescale's
    // rel_change happens to be small — skipped_reason takes priority.
    ingestRescale(freshClient(), [
      preview({ name: 'ZERO', skipped_reason: 'old_length<=0', rel_change: 0 }),
    ])

    expect(networkApi.rescaleImpedances).not.toHaveBeenCalled()
    expect(useRescaleStore.getState().pendingRescale.map(p => p.name)).toEqual(['ZERO'])
  })

  it('re-queues a failed auto-apply instead of losing it', async () => {
    vi.mocked(networkApi.rescaleImpedances).mockRejectedValue(new Error('network down'))
    vi.spyOn(toast, 'error').mockImplementation(() => '')

    ingestRescale(freshClient(), [preview({ name: 'FAILS', rel_change: 0.01 })])

    await vi.waitFor(() => expect(useRescaleStore.getState().pendingRescale.map(p => p.name)).toEqual(['FAILS']))
  })

  it('splits a mixed batch: auto applies, ask+blocked both queue', async () => {
    vi.mocked(networkApi.rescaleImpedances).mockResolvedValue({ updated: 1, skipped: [] })
    ingestRescale(freshClient(), [
      preview({ name: 'AUTO', rel_change: 0.001 }),
      preview({ name: 'ASK', rel_change: 300 }),
      preview({ name: 'BLOCKED', skipped_reason: 'new_length<=0' }),
    ])

    await vi.waitFor(() => expect(networkApi.rescaleImpedances).toHaveBeenCalledTimes(1))
    expect(networkApi.rescaleImpedances).toHaveBeenCalledWith([
      { name: 'AUTO', r: 3.1, x: 18.0, b: 0.000155 },
    ])
    expect(useRescaleStore.getState().pendingRescale.map(p => p.name).sort()).toEqual(['ASK', 'BLOCKED'])
  })
})

describe('applyRescale', () => {
  it('is a no-op on an empty list — no API call', async () => {
    await applyRescale(freshClient(), [])
    expect(networkApi.rescaleImpedances).not.toHaveBeenCalled()
  })

  it('invalidates the project-scoped lines query on success', async () => {
    vi.mocked(networkApi.rescaleImpedances).mockResolvedValue({ updated: 1, skipped: [] })
    const qc = freshClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')

    await applyRescale(qc, [preview({})])

    expect(spy).toHaveBeenCalledWith({ queryKey: ['lines', 'Demo'] })
  })

  it('toasts and rethrows on failure, without invalidating', async () => {
    vi.mocked(networkApi.rescaleImpedances).mockRejectedValue(new Error('boom'))
    vi.spyOn(toast, 'error').mockImplementation(() => '')
    const qc = freshClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')

    await expect(applyRescale(qc, [preview({})])).rejects.toThrow('boom')
    expect(toast.error).toHaveBeenCalledTimes(1)
    expect(spy).not.toHaveBeenCalled()
  })
})
