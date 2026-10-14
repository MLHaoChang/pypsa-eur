import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useUIStore } from '../store/uiStore'

const readiness = vi.hoisted(() => ({ ready: true as boolean | undefined }))
vi.mock('../hooks/useChatProfiles', () => ({
  useChatReadiness: () => ({ effectiveProfileId: 'p', ready: readiness.ready }),
}))

import { useDockAutoCollapse } from './dockAutoCollapse'

beforeEach(() => {
  readiness.ready = true
  localStorage.setItem('network-diagram:assistant-dock', 'open')
  useUIStore.setState({ activeSlidePanel: null, assistantDockOpen: true, assistantDockPinned: false })
})

describe('useDockAutoCollapse (owner decision Q5a)', () => {
  it('collapses the dock while Results is open and brings it back after, without touching the stored choice', () => {
    renderHook(() => useDockAutoCollapse())
    act(() => useUIStore.setState({ activeSlidePanel: 'results' }))
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(localStorage.getItem('network-diagram:assistant-dock')).toBe('open')
    act(() => useUIStore.setState({ activeSlidePanel: null }))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
  })

  it('leaves a pinned dock open', () => {
    useUIStore.setState({ assistantDockPinned: true })
    renderHook(() => useDockAutoCollapse())
    act(() => useUIStore.setState({ activeSlidePanel: 'results' }))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
  })

  it('collapses when no model key is set', () => {
    readiness.ready = false
    renderHook(() => useDockAutoCollapse())
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
  })

  it('keeps a dock the user reopens over Results', () => {
    renderHook(() => useDockAutoCollapse())
    act(() => useUIStore.setState({ activeSlidePanel: 'results' }))
    act(() => useUIStore.getState().setAssistantDockOpen(true))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
  })
})
