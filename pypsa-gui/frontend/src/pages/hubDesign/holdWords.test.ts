// ui-modern 05 slice 2: the words of a Guided section's list are the owning panel's.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { FlowHeld } from '../../api/flow'
import { PEAK_LOAD, SERIES_WORDS, heldFigure, heldGroups, heldHome, heldKind } from './holdWords'

const src = (rel: string) => readFileSync(resolve(__dirname, rel), 'utf8')
const held = (over: Partial<FlowHeld>): FlowHeld => ({
  component: 'Generator', entity: 'pv', carrier: null, field: 'p_nom', value: 5, unit: 'MW',
  extendable: false, ...over,
})

describe('the list words match the panels that own them', () => {
  it('a series word is the Time Series panel slot label', () => {
    const panel = src('../TimeSeriesManager.tsx')
    for (const [field, word] of Object.entries(SERIES_WORDS)) {
      if (field === 'p_set') expect(panel).toMatch(/load: 'Profile'/)
      else expect(panel).toMatch(new RegExp(`id: '${field}',\\s+label: '${word}'`))
    }
  })
  it('a load figure is the Properties card row', () => {
    expect(src('../../layout/PropertiesPanel.tsx')).toContain(`label="${PEAK_LOAD}"`)
  })
})

describe('a row in words', () => {
  it('kind: the class, with the carrier when it has one', () => {
    expect(heldKind(held({}))).toBe('Generator')
    expect(heldKind(held({ component: 'StorageUnit', carrier: 'battery' }))).toBe('Storage, battery')
  })
  it('figure: label, value and unit; never a raw field', () => {
    expect(heldFigure('assets', held({}))).toBe('Capacity: 5 MW')
    expect(heldFigure('assets', held({ component: 'Store', field: 'e_nom', value: 1234.5, unit: 'MWh' })))
      .toBe('Energy capacity: 1,234.5 MWh')
    expect(heldFigure('loads', held({ component: 'Load', field: 'p_set', value: 40 }))).toBe('Peak load: 40 MW')
  })
  it('an extendable asset is sized by the study, whatever was entered', () => {
    expect(heldFigure('assets', held({ extendable: true, value: 2 }))).toBe('Capacity: sized by the study')
  })
  it('a missing figure says why', () => {
    expect(heldFigure('assets', held({ value: null }))).toBe('Capacity: not set')
    expect(heldFigure('loads', held({ component: 'Load', field: 'p_set', value: null })))
      .toBe('Peak load: its profile has no values yet')
  })
  it('a time series names its slot; a carrier has no figure', () => {
    expect(heldFigure('profiles', held({ field: 'p_max_pu', value: null, unit: null })))
      .toBe('Availability time series')
    expect(heldFigure('profiles', held({ field: 'inflow', value: null, unit: null }))).toBe('Time series')
    expect(heldFigure('carriers', held({ component: 'Carrier', field: null, value: null, unit: null }))).toBeNull()
  })
})

describe('where "Edit" goes, by kind', () => {
  it.each(['Load', 'Generator', 'StorageUnit', 'Store', 'Link'])('%s: its editor in the drawer over the card', component => {
    const section = component === 'Load' ? 'loads' : 'assets'
    expect(heldHome(section, held({ component, entity: 'x' }))).toEqual({
      kind: 'drawer', target: { section, panel: null, component, entity: 'x', field: null } })
  })
  it.each(['Load', 'Generator', 'Link'] as const)('a %s time series: the Time Series drawer on that row', component => {
    expect(heldHome('profiles', held({ component, entity: 'x', field: 'p_max_pu' })))
      .toEqual({ kind: 'series', type: component, name: 'x', field: 'p_max_pu' })
  })
  it('a storage time series has no slot in the panel: its own editor', () => {
    expect(heldHome('profiles', held({ component: 'StorageUnit', entity: 'hydro', field: 'inflow' })).kind).toBe('drawer')
  })
  it('a carrier has no Guided editor: Expert', () => {
    expect(heldHome('carriers', held({ component: 'Carrier' }))).toEqual({ kind: 'expert' })
  })
})

it('groups keep the server order, by class', () => {
  const rows = [held({ entity: 'a' }), held({ component: 'Store', entity: 't' }), held({ entity: 'b' })]
  expect(heldGroups(rows).map(g => [g.component, g.rows.map(r => r.entity)]))
    .toEqual([['Generator', ['a', 'b']], ['Store', ['t']]])
})
