// SC-00 N UI contract (.scratch/site-scene-generation/issues/01-prototype-and-contracts.md,
// "SC-00 N UI contract", mirrored in optional-site-workflow.md): pins the source
// anchors those rules name, so a rename or move fails here instead of leaving
// the frozen contract pointing at nothing. SC-01 builds the UI; this file
// changes no behaviour.
import { describe, expect, it } from 'vitest'
import { SECTION_OPEN, sectionForComponent } from '../layout/flowSections'
import * as flowNavigation from '../layout/flowNavigation'
import switcherSource from '../components/MapModeSwitcher.tsx?raw'
import navigationSource from '../layout/flowNavigation.ts?raw'
import returnButtonSource from '../layout/FlowReturnButton.tsx?raw'
import emptyStateSource from '../components/SiteEmptyState.tsx?raw'

describe('SC-00 N UI contract anchors', () => {
  it('rule 1: the view is "Site 3D", the label of the site map mode', () => {
    expect(switcherSource).toMatch(/\{ id: 'site',\s+label: 'Site 3D',/)
  })

  it('rule 4: each part home is the section and Expert target the contract names', () => {
    expect(SECTION_OPEN.project).toEqual({ panel: 'horizon' })        // coordinate field
    expect(sectionForComponent('Load')).toBe('loads')
    expect(SECTION_OPEN.loads).toEqual({ table: 'Loads' })
    for (const cls of ['Generator', 'StorageUnit', 'Store']) expect(sectionForComponent(cls)).toBe('assets')
    expect(SECTION_OPEN.assets).toEqual({ panel: 'capacityBounds' })
    expect(SECTION_OPEN.carriers).toEqual({ table: 'Carriers' })      // process units
    expect(sectionForComponent('Transformer')).toBe('connection')
    expect(SECTION_OPEN.connection).toEqual({ panel: 'campusElectrical' })
    expect(SECTION_OPEN.results).toEqual({ panel: 'results' })
  })

  it('rule 4: jumps go through the return-recording navigation, Guided in place', () => {
    expect(typeof flowNavigation.openFlowTarget).toBe('function')
    expect(typeof flowNavigation.openFlowTargetInPlace).toBe('function')
    expect(typeof flowNavigation.showResultsTab).toBe('function')
    expect(navigationSource).toMatch(/function withReturn\(target: FlowTarget \| null, jump: \(\) => void, fromSite3D = false\)/)
    expect(typeof flowNavigation.withReturnFromSite3D).toBe('function')
  })

  it('rule 4: FlowReturn carries the frozen SC-00 N extension (map mode, Site, part, selection)', () => {
    const body = navigationSource.match(/export interface FlowReturn \{([\s\S]*?)\n\}/)?.[1] ?? ''
    const fields = [...body.matchAll(/^\s+(\w+):/gm)].map(m => m[1])
    expect(fields).toEqual(['label', 'mode', 'section', 'panel', 'drawer', 'selected', 'scroll',
      'mapMode', 'siteId', 'part', 'partSelection'])
    expect(navigationSource).toContain("SITE3D_RETURN_LABEL = 'Site 3D'")
    // The return label renders as "Back to <label>"; the frozen label is "Site 3D".
    expect(returnButtonSource).toContain('Back to {back.label}')
  })

  it('rule 2: today\'s empty state still has its own Generate button; SC-01 folds it into "Create 3D site"', () => {
    expect(emptyStateSource).toContain('Generate 3D site')
    expect(emptyStateSource).toContain('New site')
  })
})
