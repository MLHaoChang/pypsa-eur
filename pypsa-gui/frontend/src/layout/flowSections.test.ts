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
