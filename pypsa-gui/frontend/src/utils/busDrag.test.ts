// A bus drag writes x/y (lng/lat, PyPSA's order) and offers the rescale the
// backend previewed (plan M5 coverage for the map's drag path).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient } from '@tanstack/react-query'

vi.mock('./assetWrite', () => ({ updateAsset: vi.fn() }))
vi.mock('./rescaleActions', () => ({ ingestRescale: vi.fn() }))
vi.mock('../store/simulationStore', () => ({ appLog: vi.fn() }))
vi.mock('../api/mapLayout', async importOriginal => {
  const real = await importOriginal<typeof import('../api/mapLayout')>()
  return { ...real, mapLayoutApi: { getMapLayout: vi.fn(), putMapLayout: vi.fn() } }
})
vi.mock('../api/network', () => ({ networkApi: { lengthsFromGeometry: vi.fn() } }))

import { updateAsset } from './assetWrite'
import { ingestRescale } from './rescaleActions'
import { appLog } from '../store/simulationStore'
import { useMapLayoutStore } from '../pages/mapLayoutStore'
import { useUIStore } from '../store/uiStore'
import { busPositionPatch, commitBusDrag } from './busDrag'

const PREVIEW = { name: 'L1', old_length: 1, new_length: 2, old: { r: 1, x: 1, b: 1 }, new: { r: 2, x: 2, b: 2 }, rel_change: 1, skipped_reason: null }

beforeEach(() => {
  vi.mocked(updateAsset).mockReset()
  vi.mocked(ingestRescale).mockReset()
  useMapLayoutStore.getState().resetForTests()
  useUIStore.setState({ readOnly: false, readOnlyReason: 'writable' })
})
afterEach(() => { vi.clearAllMocks() })

describe('busPositionPatch', () => {
  it('maps lat/lng onto PyPSA\'s y/x', () => {
    expect(busPositionPatch(53.44, 6.83)).toEqual({ x: 6.83, y: 53.44 })
  })
})

describe('commitBusDrag', () => {
  it('PUTs the new x/y through the chokepoint and offers the backend\'s rescale previews', async () => {
    vi.mocked(updateAsset).mockResolvedValue({ name: 'B1', rescale: [PREVIEW], length_sources: { 'line:L1': 'route', 'link:K1': 'chord' } })
    const qc = new QueryClient()
    const result = await commitBusDrag(qc, 'p', 'B1', 53.44, 6.83)
    expect(updateAsset).toHaveBeenCalledWith(qc, 'p', 'buses', 'B1', { x: 6.83, y: 53.44 })
    expect(ingestRescale).toHaveBeenCalledWith(qc, [PREVIEW])
    expect(result.rescale).toEqual([PREVIEW])
    // The provenance the backend recorded is mirrored into the map document…
    expect(useMapLayoutStore.getState().docFor('p').lengths).toEqual({ 'line:L1': { source: 'route' }, 'link:K1': { source: 'chord' } })
    // …and the log says routes were followed.
    expect(vi.mocked(appLog).mock.calls[0][1]).toMatch(/2 connected branch lengths recalculated \(routes followed\)/)
  })

  it('a move with nothing connected still writes the position and offers nothing', async () => {
    vi.mocked(updateAsset).mockResolvedValue({ name: 'B1' })
    const qc = new QueryClient()
    await commitBusDrag(qc, 'p', 'B1', 1, 2)
    expect(updateAsset).toHaveBeenCalledWith(qc, 'p', 'buses', 'B1', { x: 2, y: 1 })
    expect(ingestRescale).toHaveBeenCalledWith(qc, [])
    expect(useMapLayoutStore.getState().docFor('p').lengths).toBeUndefined()
  })

  it('a failed PUT propagates and offers no rescale', async () => {
    vi.mocked(updateAsset).mockRejectedValue(new Error('locked'))
    await expect(commitBusDrag(new QueryClient(), 'p', 'B1', 1, 2)).rejects.toThrow('locked')
    expect(ingestRescale).not.toHaveBeenCalled()
  })
})
