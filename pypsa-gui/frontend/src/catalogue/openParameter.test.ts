import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useUIStore } from '../store/uiStore'
import { findEntry, openParameter, pickAssetFor, takePaletteSeed } from './openParameter'
import { returnToOrigin } from '../layout/flowNavigation'
import { entry } from './testEntries'

// Spec expert-catalogue §4.3 / §10.1 "Search": the jump in both modes.
const P_NOM = entry({ id: 'generator.p_nom', plain_name: 'Capacity' })

beforeEach(() => {
  useUIStore.setState({
    activeSlidePanel: 'scenarios', selectedComponent: null, rightPanelOpen: false,
    propertiesEditRequest: null, fieldDrawer: null, flowReturn: null,
  })
})

describe('openParameter', () => {
  it('Expert: opens the asset in Properties with that field requested, and remembers the origin', () => {
    useUIStore.setState({ uiMode: 'expert' })
    expect(openParameter({ entry: P_NOM, asset: 'PV roof' })).toBe(true)
    const s = useUIStore.getState()
    expect(s.uiMode).toBe('expert')
    expect(s.activeSlidePanel).toBeNull()
    expect(s.selectedComponent).toEqual({ type: 'Generator', name: 'PV roof' })
    expect(s.rightPanelOpen).toBe(true)
    expect(s.propertiesEditRequest).toEqual({ type: 'Generator', name: 'PV roof', field: 'p_nom' })
    expect(s.flowReturn).toMatchObject({ mode: 'expert', panel: 'scenarios' })
    expect(s.fieldDrawer).toBeNull()
  })

  it('Guided, from another view: opens the asset section card, then the drawer; Back returns; mode unchanged', () => {
    useUIStore.setState({ uiMode: 'guided', activeSlidePanel: 'results', activeSection: 'results' })
    expect(openParameter({ entry: P_NOM, asset: 'PV roof' })).toBe(true)
    const s = useUIStore.getState()
    expect(s.uiMode).toBe('guided')
    expect(s.activeSlidePanel).toBe('hubDesign')
    expect(s.activeSection).toBe('assets')
    expect(s.fieldDrawer).toMatchObject({ section: 'assets', component: 'Generator', entity: 'PV roof', field: 'p_nom' })
    expect(s.propertiesEditRequest).toEqual({ type: 'Generator', name: 'PV roof', field: 'p_nom' })
    expect(s.flowReturn).toMatchObject({ mode: 'guided', panel: 'results' })
    returnToOrigin()
    expect(useUIStore.getState()).toMatchObject({ uiMode: 'guided', activeSlidePanel: 'results', fieldDrawer: null })
  })

  it('Guided, already on that card: the drawer opens over it with no Back pill', () => {
    useUIStore.setState({ uiMode: 'guided', activeSlidePanel: 'hubDesign', activeSection: 'assets' })
    openParameter({ entry: P_NOM, asset: 'PV roof' })
    const s = useUIStore.getState()
    expect(s.fieldDrawer).toMatchObject({ entity: 'PV roof', field: 'p_nom' })
    expect(s.flowReturn).toBeNull()
    expect(s.uiMode).toBe('guided')
  })

  it('Expert: Back returns to the panel the jump left', () => {
    useUIStore.setState({ uiMode: 'expert' })
    openParameter({ entry: P_NOM, asset: 'PV roof' })
    returnToOrigin()
    expect(useUIStore.getState()).toMatchObject({ uiMode: 'expert', activeSlidePanel: 'scenarios' })
  })

  it('moves nothing for a class with no Properties card, or no asset', () => {
    useUIStore.setState({ uiMode: 'expert' })
    const set = vi.spyOn(useUIStore, 'setState')
    expect(openParameter({ entry: entry({ id: 'globalconstraint.constant', plain_name: 'Limit', scope: 'GlobalConstraint' }), asset: 'co2' })).toBe(false)
    expect(openParameter({ entry: P_NOM, asset: '' })).toBe(false)
    expect(useUIStore.getState().selectedComponent).toBeNull()
    expect(set).not.toHaveBeenCalled()
    set.mockRestore()
  })
})

describe('findEntry', () => {
  const cat = { entries: [P_NOM, entry({ id: 'generator.efficiency', plain_name: 'Efficiency', former_ids: ['generator.eta'] })] }
  it('finds an id, a former id, and nothing for an unknown id', () => {
    expect(findEntry(cat, 'generator.p_nom')?.id).toBe('generator.p_nom')
    expect(findEntry(cat, 'generator.eta')?.id).toBe('generator.efficiency')
    expect(findEntry(cat, 'generator.nope')).toBeNull()
  })
})

describe('pickAssetFor', () => {
  it('opens the palette on the plain name once, so the user picks the asset', () => {
    useUIStore.setState({ paletteMode: null })
    pickAssetFor(P_NOM)
    expect(useUIStore.getState().paletteMode).toBe('all')
    expect(takePaletteSeed()).toBe('Capacity')
    expect(takePaletteSeed()).toBe('')
  })
})

describe('focus poll teardown', () => {
  it('stops polling once cancelled: no further tick, no error', async () => {
    vi.useFakeTimers()
    const af = await import('../layout/properties/AssetFields')
    const spy = vi.spyOn(af, 'focusFieldControl').mockReturnValue(false)
    try {
      useUIStore.setState({ uiMode: 'expert' })
      openParameter({ entry: P_NOM, asset: 'PV roof' })
      vi.advanceTimersByTime(250)
      const calls = spy.mock.calls.length
      expect(calls).toBeGreaterThan(0)
      const { cancelParameterFocus } = await import('./openParameter')
      cancelParameterFocus()
      vi.advanceTimersByTime(5000)
      expect(spy.mock.calls.length).toBe(calls)
      expect(vi.getTimerCount()).toBe(0)
    } finally { spy.mockRestore(); vi.useRealTimers() }
  })

  it('stops when the document is gone', () => {
    vi.useFakeTimers()
    try {
      useUIStore.setState({ uiMode: 'expert' })
      openParameter({ entry: P_NOM, asset: 'PV roof' })
      vi.stubGlobal('document', undefined)
      vi.advanceTimersByTime(5000)
      expect(vi.getTimerCount()).toBe(0)
    } finally { vi.unstubAllGlobals(); vi.useRealTimers() }
  })
})
