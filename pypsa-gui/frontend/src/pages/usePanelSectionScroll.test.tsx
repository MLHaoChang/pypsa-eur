import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import { StrictMode } from 'react'
import { useUIStore } from '../store/uiStore'
import { usePanelSectionScroll } from './usePanelSectionScroll'

function Panel({ ready, withSection = true }: { ready: boolean; withSection?: boolean }) {
  usePanelSectionScroll('campusStudy', ready)
  return withSection ? <div data-panel-section="island" /> : <div />
}

const scrolled: string[] = []
beforeEach(() => {
  scrolled.length = 0
  useUIStore.setState({ panelSectionRequest: null })
  Element.prototype.scrollIntoView = function scroll(this: Element) {
    scrolled.push(this.getAttribute('data-panel-section') ?? '')
  }
})
afterEach(() => cleanup())

const ask = (panel: 'campusStudy' | 'gridspine', section: string) =>
  act(() => useUIStore.getState().requestPanelSection(panel, section))

describe('usePanelSectionScroll', () => {
  it('scrolls to the section once ready, then clears', () => {
    ask('campusStudy', 'island')
    const { rerender } = render(<Panel ready={false} />)
    expect(scrolled).toEqual([])
    expect(useUIStore.getState().panelSectionRequest).not.toBeNull()
    rerender(<Panel ready />)
    expect(scrolled).toEqual(['island'])
    expect(useUIStore.getState().panelSectionRequest).toBeNull()
  })

  it('clears an absent section without scrolling, and a later render of it does not scroll', () => {
    ask('campusStudy', 'island')
    const { rerender } = render(<Panel ready withSection={false} />)
    expect(scrolled).toEqual([])
    expect(useUIStore.getState().panelSectionRequest).toBeNull()
    rerender(<Panel ready withSection />)
    expect(scrolled).toEqual([])
  })

  it('clears a request whose data never arrives when the panel goes away', async () => {
    ask('campusStudy', 'island')
    const { unmount } = render(<Panel ready={false} />)
    unmount()
    await act(async () => { await Promise.resolve() })
    expect(useUIStore.getState().panelSectionRequest).toBeNull()
  })

  it('keeps a waiting request through StrictMode\'s mount, unmount, mount', async () => {
    ask('campusStudy', 'island')
    render(<StrictMode><Panel ready={false} /></StrictMode>)
    await act(async () => { await Promise.resolve() })
    expect(useUIStore.getState().panelSectionRequest).toEqual({ panel: 'campusStudy', section: 'island' })
  })

  it('a second request replaces the first', () => {
    ask('campusStudy', 'study_settings')
    ask('campusStudy', 'island')
    render(<Panel ready />)
    expect(scrolled).toEqual(['island'])
  })

  it('leaves another panel\'s request alone', async () => {
    ask('gridspine', 'readback')
    const { unmount } = render(<Panel ready />)
    unmount()
    await act(async () => { await Promise.resolve() })
    expect(scrolled).toEqual([])
    expect(useUIStore.getState().panelSectionRequest).toEqual({ panel: 'gridspine', section: 'readback' })
  })
})
