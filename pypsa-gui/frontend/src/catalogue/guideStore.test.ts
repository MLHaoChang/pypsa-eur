import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HISTORY_LIMIT, focusTargetAfterClose, useGuideStore } from './guideStore'
import { useUIStore } from '../store/uiStore'

beforeEach(() => useGuideStore.setState({ view: null, history: [], opener: null }))
afterEach(() => { document.body.innerHTML = ''; vi.useRealTimers() })

describe('guide state', () => {
  it('opens on an entry, keeps a Back history, and Back retraces it', () => {
    const g = useGuideStore.getState()
    g.openGuide('a.one')
    g.openGuide('a.two')
    g.showView({ kind: 'home', query: 'x', tab: 'type', item: null })
    expect(useGuideStore.getState().history).toEqual([{ kind: 'entry', id: 'a.one' }, { kind: 'entry', id: 'a.two' }])
    useGuideStore.getState().back()
    expect(useGuideStore.getState().view).toEqual({ kind: 'entry', id: 'a.two' })
    useGuideStore.getState().back()
    useGuideStore.getState().back()
    expect(useGuideStore.getState().view).toEqual({ kind: 'entry', id: 'a.one' })
  })

  it('bounds the history', () => {
    const g = useGuideStore.getState()
    for (let i = 0; i < HISTORY_LIMIT + 10; i++) g.openGuide(`a.e${i}`)
    const { history } = useGuideStore.getState()
    expect(history).toHaveLength(HISTORY_LIMIT)
    expect(history[0]).toEqual({ kind: 'entry', id: 'a.e9' })
  })

  it('no id opens the search', () => {
    useGuideStore.getState().openGuide()
    expect(useGuideStore.getState().view).toMatchObject({ kind: 'home', query: '' })
  })

  it('returns focus to what had focus when it opened', () => {
    vi.useFakeTimers()
    const opener = document.createElement('button')
    document.body.append(opener)
    opener.focus()
    useGuideStore.getState().openGuide('a.b')
    opener.blur()
    useGuideStore.getState().closeGuide()
    vi.runAllTimers()
    expect(document.activeElement).toBe(opener)
    expect(useGuideStore.getState().view).toBeNull()
  })

  it('an opener that is gone or hidden falls back to the palette launcher', () => {
    const trigger = document.createElement('button')
    trigger.setAttribute('data-palette-trigger', '')
    document.body.append(trigger)
    const gone = document.createElement('button')
    expect(gone.isConnected).toBe(false)
    expect(focusTargetAfterClose(gone)).toBe(trigger)
    const box = document.createElement('div')
    box.hidden = true
    const hidden = document.createElement('button')
    box.append(hidden)
    document.body.append(box)
    expect(focusTargetAfterClose(hidden)).toBe(trigger)
    expect(focusTargetAfterClose(null)).toBe(trigger)
  })

  it('closing for a jump leaves focus where the jump put it', () => {
    vi.useFakeTimers()
    const opener = document.createElement('button')
    const other = document.createElement('input')
    document.body.append(opener, other)
    useGuideStore.getState().openGuide('a.b', opener)
    other.focus()
    useGuideStore.getState().closeGuide({ restoreFocus: false })
    vi.runAllTimers()
    expect(document.activeElement).toBe(other)
  })

  it('a mode switch closes it', () => {
    useUIStore.setState({ uiMode: 'expert' })
    useGuideStore.getState().openGuide('a.b')
    useUIStore.getState().setUiMode('guided')
    expect(useGuideStore.getState().view).toBeNull()
  })
})
