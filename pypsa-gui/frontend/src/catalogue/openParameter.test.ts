import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useUIStore } from '../store/uiStore'
import { findEntry, openParameter } from './openParameter'
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

  it('Guided: opens the field drawer on that asset and field, and never switches mode', () => {
    useUIStore.setState({ uiMode: 'guided', activeSlidePanel: 'hubDesign' })
    expect(openParameter({ entry: P_NOM, asset: 'PV roof' })).toBe(true)
    const s = useUIStore.getState()
    expect(s.uiMode).toBe('guided')
    expect(s.fieldDrawer).toMatchObject({ section: 'assets', component: 'Generator', entity: 'PV roof', field: 'p_nom' })
    expect(s.propertiesEditRequest).toEqual({ type: 'Generator', name: 'PV roof', field: 'p_nom' })
    expect(s.flowReturn).toMatchObject({ mode: 'guided' })
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
