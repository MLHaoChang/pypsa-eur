// Guided-mode spec §6.1: `sendRequest` is how a hub-design card sends a
// request to the assistant. It only QUEUES (ChatPanel dispatches through the
// typed-message path); it opens the dock; identical text within 2 s is a
// double click and is dropped (queue-only dedupe — `ChatMessage` carries no
// timestamp); a project switch clears the queue.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useChatStore } from './chatStore'
import { useUIStore } from './uiStore'

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-27T10:00:00Z'))
  useChatStore.setState({ requestQueue: [], lastRequest: null, composerSeed: null })
  useUIStore.setState({ assistantDockOpen: false })
})
afterEach(() => { vi.useRealTimers() })

const queue = () => useChatStore.getState().requestQueue

describe('chatStore.sendRequest', () => {
  it('queues the trimmed text and returns its id', () => {
    const id = useChatStore.getState().sendRequest('  do it  ', { source: 'hub-design' })
    expect(typeof id).toBe('string')
    expect(queue()).toHaveLength(1)
    expect(queue()[0]).toMatchObject({ id, text: 'do it', source: 'hub-design' })
    expect(queue()[0].queuedAt).toBe(Date.now())
  })

  it('opens the assistant dock', () => {
    useChatStore.getState().sendRequest('do it')
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
  })

  it('empty or blank text → null, nothing queued, dock untouched', () => {
    expect(useChatStore.getState().sendRequest('   ')).toBeNull()
    expect(queue()).toHaveLength(0)
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
  })

  it('does not touch the composer draft seed', () => {
    useChatStore.getState().sendRequest('do it')
    expect(useChatStore.getState().composerSeed).toBeNull()
  })

  it('dedupe: identical text within 2 s → null', () => {
    expect(useChatStore.getState().sendRequest('do it')).not.toBeNull()
    vi.advanceTimersByTime(1999)
    expect(useChatStore.getState().sendRequest('do it')).toBeNull()
    expect(queue()).toHaveLength(1)
  })

  it('dedupe compares the trimmed text', () => {
    useChatStore.getState().sendRequest('do it')
    expect(useChatStore.getState().sendRequest(' do it ')).toBeNull()
  })

  it('different text within 2 s → queued, in order', () => {
    useChatStore.getState().sendRequest('first')
    expect(useChatStore.getState().sendRequest('second')).not.toBeNull()
    expect(queue().map(r => r.text)).toEqual(['first', 'second'])
  })

  it('same text after 2.1 s → queued', () => {
    useChatStore.getState().sendRequest('do it')
    vi.advanceTimersByTime(2100)
    expect(useChatStore.getState().sendRequest('do it')).not.toBeNull()
    expect(queue()).toHaveLength(2)
  })

  it('ids are distinct', () => {
    const a = useChatStore.getState().sendRequest('a')
    const b = useChatStore.getState().sendRequest('b')
    expect(a).not.toBe(b)
  })
})

describe('chatStore.takeNextRequest', () => {
  it('takes FIFO and removes the request; null when empty', () => {
    useChatStore.getState().sendRequest('first')
    useChatStore.getState().sendRequest('second')
    expect(useChatStore.getState().takeNextRequest()?.text).toBe('first')
    expect(queue().map(r => r.text)).toEqual(['second'])
    expect(useChatStore.getState().takeNextRequest()?.text).toBe('second')
    expect(useChatStore.getState().takeNextRequest()).toBeNull()
  })

  it('a take refreshes lastRequest, so a click right after the dispatch is a duplicate', () => {
    useChatStore.getState().sendRequest('do it')
    vi.advanceTimersByTime(1900)
    useChatStore.getState().takeNextRequest()
    vi.advanceTimersByTime(1900)
    // 3.8 s after the send, but only 1.9 s after the take.
    expect(useChatStore.getState().sendRequest('do it')).toBeNull()
  })
})

describe('resetForProjectSwitch', () => {
  it('clears the queue', () => {
    useChatStore.getState().sendRequest('a')
    useChatStore.getState().sendRequest('b')
    useChatStore.getState().resetForProjectSwitch()
    expect(queue()).toEqual([])
  })
})

// P25 gate B2 / note 2: a card request carries its display label and its
// card group; a denial drops the rest of the group.
describe('labels and groups (P25 gate)', () => {
  beforeEach(() => { useChatStore.setState({ activeRequest: null }) })

  it('keeps the label and group with the queued request', () => {
    useChatStore.getState().sendRequest('raw text', { source: 'hub-design',
      label: 'Apply this recommendation: X', group: 'g1' })
    expect(queue()[0]).toMatchObject({ text: 'raw text', label: 'Apply this recommendation: X',
      group: 'g1' })
  })

  it('a take makes the request the active one', () => {
    useChatStore.getState().sendRequest('a', { group: 'g1' })
    const r = useChatStore.getState().takeNextRequest()
    expect(useChatStore.getState().activeRequest).toEqual(r)
  })

  it('dropRequestGroup removes only that group\'s queued requests', () => {
    useChatStore.getState().sendRequest('a1', { group: 'g1' })
    useChatStore.getState().sendRequest('b', { group: 'g2' })
    useChatStore.getState().sendRequest('a2', { group: 'g1' })
    useChatStore.getState().sendRequest('c')
    useChatStore.getState().dropRequestGroup('g1')
    expect(queue().map(r => r.text)).toEqual(['b', 'c'])
  })

  it('resetForProjectSwitch clears the active request', () => {
    useChatStore.getState().sendRequest('a', { group: 'g1' })
    useChatStore.getState().takeNextRequest()
    useChatStore.getState().resetForProjectSwitch()
    expect(useChatStore.getState().activeRequest).toBeNull()
  })
})
