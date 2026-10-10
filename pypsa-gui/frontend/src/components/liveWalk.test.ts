import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useUIStore } from '../store/uiStore'
import {
  KEY_RESULT_TABS,
  keyResultsWalkRequested,
  openProjectList,
  openProjectRequested,
  startKeyResultsWalk,
} from './liveWalk'

describe('live results walk', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  beforeEach(() => {
    useUIStore.setState({
      assistantDockOpen: false,
      activeSlidePanel: null,
      uiMode: 'guided',
      resultsTabRequest: null,
    })
  })

  it('recognises a request to tour the results tabs', () => {
    expect(keyResultsWalkRequested('Walk me through the key results in each tab')).toBe(true)
    expect(keyResultsWalkRequested('hello')).toBe(false)
    expect(openProjectRequested('Open the project')).toBe(true)
    expect(openProjectRequested('Walk me through the key results in each tab')).toBe(false)
  })

  it('opens every key results tab through the harness navigation and leaves the console closed', () => {
    const seen: string[] = []
    const request = useUIStore.getState().requestResultsTab
    useUIStore.setState({
      requestResultsTab: (tab: string) => {
        seen.push(tab)
        request(tab)
      },
    })
    const queued: Array<() => void> = []
    const cancel = startKeyResultsWalk({
      schedule: (fn) => {
        queued.push(fn)
        return queued.length
      },
      stepMs: 0,
    })
    expect(seen).toEqual([KEY_RESULT_TABS[0].id])
    expect(queued).toHaveLength(1)
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(useUIStore.getState().uiMode).toBe('expert')
    while (queued.length > 0) queued.shift()?.()
    expect(seen).toEqual(KEY_RESULT_TABS.map((tab) => tab.id))
    expect(useUIStore.getState().resultsTabRequest).toBe('investment')
    cancel()
  })

  it('opens the next tab only after the server body proves the pause', async () => {
    const seen: string[] = []
    vi.stubGlobal('fetch', async (input: RequestInfo | URL) => {
      const ms = new URL(String(input), 'http://127.0.0.1').searchParams.get('ms')
      return new Response(`paced:${ms}`, { status: 200 })
    })
    const cancel = startKeyResultsWalk({
      onStep: (tab) => { seen.push(tab.id) },
    })
    expect(seen).toEqual(['overview'])
    await vi.waitFor(() => {
      expect(seen).toEqual(KEY_RESULT_TABS.map((tab) => tab.id))
    })
    cancel()
  })

  it('stops the walk when a fast 200 is not a server sleep', async () => {
    const seen: string[] = []
    const misses: string[] = []
    const timers = vi.spyOn(window, 'setTimeout')
    vi.stubGlobal('fetch', async () => new Response('<!doctype html>', { status: 200 }))
    const cancel = startKeyResultsWalk({
      onStep: (tab) => { seen.push(tab.id) },
      onPaceMiss: (detail) => { misses.push(detail) },
    })
    expect(seen).toEqual(['overview'])
    await vi.waitFor(() => { expect(misses.length).toBe(1) })
    expect(seen).toEqual(['overview'])
    expect(timers.mock.calls.some((call) => call[1] === 1800)).toBe(false)
    cancel()
  })

  it('opens the project list without opening the console', () => {
    const events: string[] = []
    window.addEventListener('chat:open-project-picker', () => { events.push('picker') })
    openProjectList()
    expect(events).toEqual(['picker'])
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
  })
})
