import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { formatElapsed, useElapsed } from './useElapsed'

describe('useElapsed', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('counts seconds while active and resets when it stops', () => {
    const { result, rerender } = renderHook(({ on }) => useElapsed(on), { initialProps: { on: true } })
    expect(result.current).toBe(0)
    act(() => { vi.advanceTimersByTime(41_000) })
    expect(result.current).toBe(41)
    rerender({ on: false })
    expect(result.current).toBe(0)
  })

  it('formats minutes and seconds', () => {
    expect(formatElapsed(41)).toBe('0:41')
    expect(formatElapsed(188)).toBe('3:08')
  })
})
