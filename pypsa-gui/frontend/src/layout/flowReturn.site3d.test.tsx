// SC-01b: the frozen FlowReturn extension. A jump that leaves Site 3D shows
// "Back to Site 3D" and lands on the same Site, part and selection; Guided
// stays in Guided (in place), Expert opens the horizon panel.
import { beforeEach, describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { useUIStore } from '../store/uiStore'
import { FlowReturnButton } from './FlowReturnButton'
import { SITE_ANCHOR_TARGET, openSiteAnchor, openSiteEntry } from './flowNavigation'

const inSite3D = (uiMode: 'guided' | 'expert') => useUIStore.setState({
  uiMode, canvasView: 'site', activeSiteId: 'campus', sitePart: 'generate',
  sitePartSelection: { kind: 'building', id: 'osm:42' }, fieldDrawer: null, flowReturn: null,
  activeSlidePanel: null, activeSection: null, siteAnchorRequest: false,
})

describe('Back to Site 3D', () => {
  beforeEach(() => inSite3D('guided'))

  it('Guided: the coordinate field opens in place, and Back restores Site, part and selection', () => {
    render(<FlowReturnButton />)
    act(() => openSiteAnchor({ fromSite3D: true }))
    const ui = useUIStore.getState()
    expect(ui.uiMode).toBe('guided')
    expect(ui.fieldDrawer).toEqual(SITE_ANCHOR_TARGET)
    expect(ui.siteAnchorRequest).toBe(true)
    expect(screen.getByTestId('flow-return').textContent).toContain('Back to Site 3D')
    act(() => useUIStore.setState({ activeSiteId: 'other', sitePart: 'map', sitePartSelection: null, canvasView: 'satellite' }))
    // setState bypasses navigationChanged, so the return is still pending.
    fireEvent.click(screen.getByTestId('flow-return'))
    const back = useUIStore.getState()
    expect(back.canvasView).toBe('site')
    expect(back.activeSiteId).toBe('campus')
    expect(back.sitePart).toBe('generate')
    expect(back.sitePartSelection).toEqual({ kind: 'building', id: 'osm:42' })
    expect(back.fieldDrawer).toBeNull()
  })

  it('Expert: the coordinate field opens the horizon panel; Back closes it', () => {
    inSite3D('expert')
    render(<FlowReturnButton />)
    act(() => openSiteAnchor({ fromSite3D: true }))
    expect(useUIStore.getState().activeSlidePanel).toBe('horizon')
    expect(useUIStore.getState().uiMode).toBe('expert')
    fireEvent.click(screen.getByTestId('flow-return'))
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    expect(useUIStore.getState().canvasView).toBe('site')
  })

  it('the empty state link shows the one entry: the card in Guided, horizon in Expert', () => {
    act(() => openSiteEntry({ fromSite3D: true }))
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
    expect(useUIStore.getState().activeSection).toBe('project')
    expect(useUIStore.getState().uiMode).toBe('guided')
    inSite3D('expert')
    act(() => openSiteEntry({ fromSite3D: true }))
    expect(useUIStore.getState().activeSlidePanel).toBe('horizon')
    expect(useUIStore.getState().flowReturn?.label).toBe('Site 3D')
  })

  it('a jump not from Site 3D records no map mode', () => {
    useUIStore.setState({ canvasView: 'blank' })
    act(() => openSiteAnchor())
    expect(useUIStore.getState().flowReturn?.mapMode ?? null).toBeNull()
  })
})
