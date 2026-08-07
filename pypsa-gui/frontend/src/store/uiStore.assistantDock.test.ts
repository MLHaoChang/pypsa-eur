import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useUIStore } from './uiStore'

const KEY = 'network-diagram:assistant-dock'

describe('assistant dock state', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: false })
  })

  it('defaults to closed', () => {
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
  })

  it('setAssistantDockOpen writes through to the store and localStorage', () => {
    useUIStore.getState().setAssistantDockOpen(true)
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(localStorage.getItem(KEY)).toBe('open')
  })

  it('toggleAssistantDock flips the current value', () => {
    useUIStore.getState().toggleAssistantDock()
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    useUIStore.getState().toggleAssistantDock()
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(localStorage.getItem(KEY)).toBe('closed')
  })

  it('survives a localStorage that throws', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError')
    })
    expect(() => useUIStore.getState().setAssistantDockOpen(true)).not.toThrow()
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    spy.mockRestore()
  })
})
