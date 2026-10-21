import { beforeEach, describe, expect, it } from 'vitest'
import { FLOW_SECTIONS } from '../api/flow'
import { useUIStore } from '../store/uiStore'
import { BAND_SECTIONS, PANEL_SECTION, SECTION_META, sectionForComponent } from './flowSections'

describe('flow sections in the shell', () => {
  it('lists the ten sections of the flow, in order', () => {
    expect(SECTION_META.map(s => s.id)).toEqual([...FLOW_SECTIONS])
  })

  it('puts every section in exactly one band, except 10 below the bands', () => {
    const all = Object.values(BAND_SECTIONS).flat()
    expect(all).toEqual([...FLOW_SECTIONS])
  })

  it('maps a component class to the section it is edited in', () => {
    expect(sectionForComponent('Load')).toBe('loads')
    expect(sectionForComponent('Generator')).toBe('assets')
    expect(sectionForComponent('Bus')).toBe('connection')
    expect(sectionForComponent('Snapshot')).toBeNull()
  })
})

describe('activeSection', () => {
  beforeEach(() => useUIStore.setState({ activeSection: null, activeSlidePanel: null, selectedComponent: null }))

  it('follows the panel the user opens', () => {
    useUIStore.getState().setSlidePanel('economics')
    expect(useUIStore.getState().activeSection).toBe(PANEL_SECTION.economics)
    expect(useUIStore.getState().activeSection).toBe('commercial')
  })

  it('keeps the section when a panel with no section opens or closes', () => {
    useUIStore.getState().setSlidePanel('horizon')
    useUIStore.getState().setSlidePanel('snapshots')
    expect(useUIStore.getState().activeSection).toBe('project')
    useUIStore.getState().setSlidePanel(null)
    expect(useUIStore.getState().activeSection).toBe('project')
  })

  it('follows the selected component, and resets on a project switch', () => {
    useUIStore.getState().setSelectedComponent({ type: 'Load', name: 'l1' })
    expect(useUIStore.getState().activeSection).toBe('loads')
    useUIStore.getState().setCurrentProject('Other')
    expect(useUIStore.getState().activeSection).toBeNull()
  })
})

describe('the Guided ↔ Expert switch keeps the section (§7)', () => {
  beforeEach(() => useUIStore.setState({ uiMode: 'guided', activeSlidePanel: 'hubDesign', activeSection: null, currentProject: 'p' }))

  it('Guided → Expert opens the section the user was in, not a blank workbench', () => {
    useUIStore.setState({ activeSection: 'commercial' })
    useUIStore.getState().setUiMode('expert')
    expect(useUIStore.getState().activeSlidePanel).toBe('economics')
    expect(useUIStore.getState().activeSection).toBe('commercial')
  })

  it('a table section opens its bottom table', () => {
    useUIStore.setState({ activeSection: 'loads' })
    useUIStore.getState().setUiMode('expert')
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    expect(useUIStore.getState().bottomTabRequest).toBe('Loads')
  })

  it('with no section yet it closes the hub design as before', () => {
    useUIStore.getState().setUiMode('expert')
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
  })

  it('Expert → Guided lands on the card of the panel the user was in', () => {
    useUIStore.setState({ uiMode: 'expert', activeSlidePanel: null, activeSection: null })
    useUIStore.getState().setSlidePanel('simparams')
    useUIStore.getState().setUiMode('guided')
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
    expect(useUIStore.getState().activeSection).toBe('scenarios')
  })
})
