import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { SkeletonLines, SKELETON_DELAY_MS } from './Skeleton'

afterEach(() => { cleanup(); vi.useRealTimers() })

describe('Skeleton', () => {
  it('stays invisible for the first 200 ms, in a busy status region', () => {
    vi.useFakeTimers()
    const { container } = render(<SkeletonLines what="the site check" testId="sk" />)
    const region = screen.getByTestId('sk')
    expect(region.getAttribute('aria-busy')).toBe('true')
    expect(region.getAttribute('role')).toBe('status')
    const bars = container.querySelectorAll('[aria-hidden="true"]')
    expect(bars.length).toBe(3)
    expect(bars[0].className).toContain('invisible')
    act(() => { vi.advanceTimersByTime(SKELETON_DELAY_MS) })
    expect(bars[0].className).not.toContain('invisible')
  })
})
