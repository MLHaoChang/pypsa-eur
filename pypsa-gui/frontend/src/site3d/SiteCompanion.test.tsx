import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import type { FlowScene } from '../api/flow'
import type { SceneBuilding } from '../api/siteScene'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import SiteCompanion, { partBlocker } from './SiteCompanion'

const loadsBlocker = { code: 'no_model_targets', reason: 'Buildings only: configure model elements to map them', link: '›' as const,
  home: { guided: { section: 'loads' as const, drawer: null, open: 'request_then_scroll' as const }, expert: { table: 'Loads' } } }
const scene: FlowScene = {
  entry: { label: 'Generate site', enabled: true, blocker: null }, anchor_moved: [],
  actions: [
    { action: 'generate_native', available: true, blocker: null, required_inputs: [], computes: '' },
    { action: 'assign_native', available: false, blocker: loadsBlocker, required_inputs: [], computes: '' },
  ],
}
const osm: SceneBuilding = { id: 'osm:7', source: { namespace: 'osm', source_id: '7' }, footprint: [], height_m: 6,
  height_evidence: 'fallback', levels: null }

const seed = () => useChatStore.getState().composerSeed
const visible = () => screen.getByTestId('site-companion').textContent ?? ''

beforeEach(() => {
  useChatStore.setState({ composerSeed: null })
  useUIStore.setState({ assistantDockOpen: false, activeSlidePanel: null, canvasView: 'site', sitePart: 'generate' })
})

describe.each(['guided', 'expert'] as const)('Site 3D companion row (%s)', mode => {
  beforeEach(() => useUIStore.setState({ uiMode: mode }))

  it('Explain this opens the assistant with the Site and part in words, sends nothing and does not navigate', () => {
    render(<SiteCompanion siteName="Campus" part="generate" scene={scene} selected={null} />)
    fireEvent.click(screen.getByTestId('site-companion-explain'))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(seed()).toBe('Explain what I am looking at: the Generate part of the site “Campus” in Site 3D.')
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    expect(useUIStore.getState().sitePart).toBe('generate')
    expect(useChatStore.getState().messages ?? []).toHaveLength(0)
  })

  it('Explain this names a selected building by its visible label', () => {
    render(<SiteCompanion siteName="Campus" part="generate" scene={scene} selected={osm} />)
    fireEvent.click(screen.getByTestId('site-companion-explain'))
    expect(seed()).toBe('Explain OSM building 7, selected in the Generate part of the site “Campus” in Site 3D.')
  })

  it('labels name their target: the selection, else the part tab', () => {
    const { rerender } = render(<SiteCompanion siteName="Campus" part="model" scene={scene} selected={null} />)
    expect(screen.getByTestId('site-companion-explain').textContent).toBe('Explain Model')
    rerender(<SiteCompanion siteName="Campus" part="model" scene={scene} selected={osm} />)
    expect(screen.getByTestId('site-companion-explain').textContent).toBe('Explain OSM building 7')
    expect(screen.getByTestId('site-companion-explain').title).toMatch(/^Explain OSM building 7\. Opens the assistant/)
  })

  it('Help with shows only while /flow reports a blocker for the open part, named by its reason', () => {
    const { rerender } = render(<SiteCompanion siteName="Campus" part="generate" scene={scene} selected={null} />)
    expect(screen.queryByTestId('site-companion-blocker')).toBeNull()
    rerender(<SiteCompanion siteName="Campus" part="model" scene={scene} selected={null} />)
    expect(screen.getByTestId('site-companion-blocker').textContent).toBe('Help with: Buildings only: configure model elements to map them')
    fireEvent.click(screen.getByTestId('site-companion-blocker'))
    expect(seed()).toBe('Help me with what blocks the Model part of the site “Campus” in Site 3D: '
      + 'Buildings only: configure model elements to map them.')
    rerender(<SiteCompanion siteName="Campus" part="model" scene={undefined} selected={null} />)
    expect(screen.queryByTestId('site-companion-blocker')).toBeNull()
  })

  it('Prepare a change leaves the request for the user to finish', () => {
    render(<SiteCompanion siteName="Campus" part="model" scene={scene} selected={null} />)
    fireEvent.click(screen.getByTestId('site-companion-prepare'))
    expect(seed()).toBe('Prepare a change to the site “Campus” in Site 3D: ')
  })

  it('shows no ids, field keys, em dashes or semicolons (U4)', () => {
    render(<SiteCompanion siteName="Campus" part="model" scene={scene} selected={osm} />)
    expect(visible()).not.toMatch(/[—;]|assign_native|no_model_targets|osm:7|campus/)
  })
})

describe('partBlocker', () => {
  it('Map stands on the creation entry; a part with an available action has none', () => {
    const entryBlocked: FlowScene = { ...scene, entry: { label: 'Generate site', enabled: false, blocker: { ...loadsBlocker, code: 'no_anchor' } } }
    expect(partBlocker(entryBlocked, 'map')?.code).toBe('no_anchor')
    expect(partBlocker(scene, 'map')).toBeNull()
    expect(partBlocker(scene, 'generate')).toBeNull()
    expect(partBlocker(scene, 'results')).toBeNull()
    expect(partBlocker(scene, 'model')?.code).toBe('no_model_targets')
  })
})
