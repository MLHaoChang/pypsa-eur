// Assistant-assurance 06 S5 (G8a): which result tabs the panel shows, per mode
// and network kind, pinned against hand-written lists (never against TABS), and
// every one of them reachable by the assistant's `results_tab`.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { tabsFor } from './resultTabs'

const EXPERT_SINGLE = [
  'summary', 'capex', 'dispatch', 'loadflow', 'grid_code', 'electrical_design', 'island',
  'grid_forming', 'flows', 'loadshape', 'prices', 'economics', 'emissions', 'curtailment',
  'lostload', 'adequacy', 'storage', 'fmea', 'investment', 'asset',
]
const EXPERT_MULTI = [
  'summary', 'overview', 'capex', 'dispatch', 'loadflow', 'grid_code', 'electrical_design', 'island',
  'grid_forming', 'flows', 'loadshape', 'prices', 'economics', 'emissions', 'curtailment',
  'lostload', 'adequacy', 'storage', 'fmea', 'investment', 'asset',
]
const GUIDED = ['summary', 'flows', 'loadshape', 'adequacy', 'fmea']

describe('tabsFor', () => {
  it('Expert shows every result view but Overview on a single-period network', () => {
    expect(tabsFor('expert', 'single')).toEqual(EXPERT_SINGLE)
  })
  it('Expert adds Overview on a multi-period network', () => {
    expect(tabsFor('expert', 'multi')).toEqual(EXPERT_MULTI)
  })
  it('Guided shows the same five views for both network kinds', () => {
    expect(tabsFor('guided', 'single')).toEqual(GUIDED)
    expect(tabsFor('guided', 'multi')).toEqual(GUIDED)
  })
  it('Grid risk needs process units and Products needs a product bus, in both modes', () => {
    const all = { processUnits: true, products: true }
    expect(tabsFor('expert', 'single', all)).toEqual([
      'summary', 'capex', 'dispatch', 'loadflow', 'grid_code', 'electrical_design', 'island',
      'grid_forming', 'flows', 'loadshape', 'prices', 'economics', 'emissions', 'curtailment',
      'lostload', 'adequacy', 'storage', 'grid_risk', 'products', 'fmea', 'investment', 'asset',
    ])
    expect(tabsFor('guided', 'multi', all)).toEqual(
      ['summary', 'flows', 'loadshape', 'adequacy', 'grid_risk', 'products', 'fmea'])
    expect(tabsFor('guided', 'single', { processUnits: true })).toEqual(
      ['summary', 'flows', 'loadshape', 'adequacy', 'grid_risk', 'fmea'])
    expect(tabsFor('guided', 'single', { products: true })).toEqual(
      ['summary', 'flows', 'loadshape', 'adequacy', 'products', 'fmea'])
  })
  it('never lists a Hand-off tab', () => {
    const tabs: string[] = tabsFor('expert', 'multi', { processUnits: true, products: true })
    for (const id of ['reports', 'exports', 'equipment_schedule', 'specialist_packet']) expect(tabs).not.toContain(id)
  })
})

describe('tabsFor against the backend tab enum', () => {
  // The backend schema's own list: ui_open_panel's and discuss_project_run's results_tab enum.
  const src = readFileSync(resolve(process.cwd(), '..', 'backend', 'harness', 'catalogue.py'), 'utf8')
  const body = /^RESULTS_TAB_ENUM = \[([\s\S]*?)^\]/m.exec(src)?.[1] ?? ''
  const enumTabs = [...body.replace(/#.*$/gm, '').matchAll(/"([a-z_]+)"/g)].map(m => m[1])

  it('reads the enum', () => {
    expect(enumTabs).toEqual(expect.arrayContaining(['summary', 'loadflow', 'products']))
  })
  it.each([
    ['expert', 'single'], ['expert', 'multi'], ['guided', 'single'], ['guided', 'multi'],
  ] as const)('every %s/%s tab is in RESULTS_TAB_ENUM', (mode, kind) => {
    for (const id of tabsFor(mode, kind, { processUnits: true, products: true })) expect(enumTabs).toContain(id)
  })
})
