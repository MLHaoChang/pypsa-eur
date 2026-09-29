import { describe, it, expect, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { mapClickOwner, closeDraft, useSiteDraw } from './useSiteDraw'
import { useUIStore } from '../store/uiStore'
import { useRescaleStore } from '../store/rescaleStore'

const sq: Array<[number, number]> = [[6.83, 53.44], [6.84, 53.44], [6.84, 53.43], [6.83, 53.43]]

describe('mapClickOwner', () => {
  it('placement wins, drawing next, else nobody', () => {
    expect(mapClickOwner({ placementActive: true, siteDrawMode: 'drawing' })).toBe('place')
    expect(mapClickOwner({ placementActive: false, siteDrawMode: 'drawing' })).toBe('draw')
    expect(mapClickOwner({ placementActive: false, siteDrawMode: 'idle' })).toBeNull()
  })
})

describe('closeDraft', () => {
  it('drops the double-click duplicates and needs three vertices', () => {
    expect(closeDraft([sq[0], sq[1]])).toEqual({ status: 'too_few' })
    expect(closeDraft([...sq, sq[3], sq[3]])).toEqual({ status: 'closed', boundary: sq })
  })
})

describe('useSiteDraw', () => {
  beforeEach(() => {
    useUIStore.setState({ siteDrawMode: 'idle', siteDraft: [], readOnly: false, readOnlyReason: 'writable' })
    useRescaleStore.setState({ placementActive: false })
  })

  it('click adds a vertex, Escape clears, Enter with < 3 reports too_few and keeps drawing', () => {
    const { result } = renderHook(() => useSiteDraw())
    act(() => { expect(result.current.start()).toBe(true) })
    expect(result.current.drawing).toBe(true)
    act(() => result.current.addVertex(6.83, 53.44))
    act(() => result.current.addVertex(6.84, 53.44))
    expect(result.current.draft).toHaveLength(2)
    let r: unknown
    act(() => { r = result.current.finish() })
    expect(r).toEqual({ status: 'too_few' })
    expect(result.current.drawing).toBe(true)
    act(() => result.current.cancel())
    expect(result.current.drawing).toBe(false)
    expect(result.current.draft).toEqual([])
  })

  it('Enter with ≥ 3 yields the boundary and leaves draw mode', () => {
    const { result } = renderHook(() => useSiteDraw())
    act(() => { result.current.start() })
    for (const v of sq) act(() => result.current.addVertex(v[0], v[1]))
    let r: unknown
    act(() => { r = result.current.finish() })
    expect(r).toEqual({ status: 'closed', boundary: sq })
    expect(result.current.drawing).toBe(false)
  })

  it('a double-click close (two clicks then dblclick on the same point) yields a valid boundary', () => {
    const { result } = renderHook(() => useSiteDraw())
    act(() => { result.current.start() })
    for (const v of sq.slice(0, 3)) act(() => result.current.addVertex(v[0], v[1]))
    // Leaflet: click, click, dblclick at the fourth point.
    act(() => result.current.addVertex(sq[3][0], sq[3][1]))
    act(() => result.current.addVertex(sq[3][0], sq[3][1]))
    let r: unknown
    act(() => { r = result.current.closeByDoubleClick(sq[3][0], sq[3][1]) })
    expect(r).toEqual({ status: 'closed', boundary: sq })
    expect(result.current.drawing).toBe(false)
  })

  it('clicks are ignored while bus placement owns the map, and drawing cannot start then', () => {
    useRescaleStore.setState({ placementActive: true })
    const { result } = renderHook(() => useSiteDraw())
    act(() => { expect(result.current.start()).toBe(false) })
    expect(result.current.owner).toBe('place')
    useRescaleStore.setState({ placementActive: false })
    act(() => { result.current.start() })
    useRescaleStore.setState({ placementActive: true })
    act(() => result.current.addVertex(6.83, 53.44))
    expect(useUIStore.getState().siteDraft).toEqual([])
  })

  it('read-only blocks entering draw mode', () => {
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
    const { result } = renderHook(() => useSiteDraw())
    act(() => { expect(result.current.start()).toBe(false) })
    expect(result.current.drawing).toBe(false)
  })
})
