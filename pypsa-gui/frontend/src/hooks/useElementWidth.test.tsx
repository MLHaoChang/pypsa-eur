import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useElementWidth } from './useElementWidth'

type Cb = (entries: Array<{ contentRect: { width: number } }>) => void

afterEach(() => { vi.unstubAllGlobals() })

describe('useElementWidth', () => {
  it('is null (unknown) where ResizeObserver does not exist', () => {
    vi.stubGlobal('ResizeObserver', undefined)
    const { result } = renderHook(() => useElementWidth(document.createElement('div')))
    expect(result.current).toBeNull()
  })

  it('tracks the observed width and disconnects on unmount', () => {
    let cb: Cb = () => {}
    const disconnect = vi.fn()
    vi.stubGlobal('ResizeObserver', class {
      constructor(c: Cb) { cb = c }
      observe() {}
      disconnect() { disconnect() }
    })
    const { result, unmount } = renderHook(() => useElementWidth(document.createElement('div')))
    expect(result.current).toBeNull()
    act(() => cb([{ contentRect: { width: 640 } }]))
    expect(result.current).toBe(640)
    unmount()
    expect(disconnect).toHaveBeenCalled()
  })
})
