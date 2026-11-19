// ui-modern 05 slice 2: the words of a Guided section's list are the owning panel's.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { FlowHeld, FlowRecipe } from '../../api/flow'
import { fieldWords } from '../../utils/flowItems'
import { PEAK_LOAD, SERIES_WORDS, groupTitle, heldFigure, heldGroups, heldHome, heldKind, heldRest, RECIPE_FIELD } from './holdWords'

const src = (rel: string) => readFileSync(resolve(__dirname, rel), 'utf8')
const HB: FlowRecipe = {
  label: 'Haber-Bosch', product: { carrier: 'NH3', label: 'NH₃' }, per: 't', product_per: 5.166,
  electricity: { value: 1277.5518, unit: 'kWh' },
  inputs: [{ port: 2, carrier: 'H2', label: 'Hydrogen', value: 5.93, unit: 'MWh' }],
}

const held = (over: Partial<FlowHeld>): FlowHeld => ({
  component: 'Generator', entity: 'pv', carrier: null, field: 'p_nom', value: 5, unit: 'MW',
  extendable: false, series: false, ...over,
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
    expect(heldFigure('loads', held({ component: 'Load', field: 'p_set', value: null, series: true })))
      .toBe('Peak load: its profile has no values yet')
    // A fixed load with no value has no profile to blame.
    expect(heldFigure('loads', held({ component: 'Load', field: 'p_set', value: null })))
      .toBe('Peak load: not set')
  })
  it('a figure shows the unit the server sent, or none', () => {
    expect(heldFigure('assets', held({ value: 1200, unit: 't/h' }))).toBe('Capacity: 1,200 t/h')
    expect(heldFigure('assets', held({ value: 3, unit: null }))).toBe('Capacity: 3')
  })
  it('a group is titled as the Expert tables name it', () => {
    expect(['Generator', 'StorageUnit', 'Store', 'Load', 'Link', 'Carrier'].map(groupTitle))
      .toEqual(['Generators', 'Storage', 'Stores', 'Loads', 'Links', 'Carriers'])
    expect(groupTitle('Bus')).toBe('Bus')
  })
  it('a list the server cut short says how many it shows', () => {
    expect(heldRest(200, 4000)).toBe('Showing the first 200 of 4,000. The rest are in Expert.')
    expect(heldRest(12, 12)).toBeNull()
    expect(heldRest(12, undefined)).toBeNull()
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
  it('a load time series: the load\'s own drawer with its series editor, not the library', () => {
    expect(heldHome('profiles', held({ component: 'Load', entity: 'it_load', field: 'p_set' })))
      .toEqual({ kind: 'drawer', target: { section: 'profiles', panel: null, component: 'Load', entity: 'it_load', field: null } })
  })
  it.each(['Generator', 'Link'] as const)('a %s time series: the Time Series drawer on that row', component => {
    expect(heldHome('profiles', held({ component, entity: 'x', field: 'p_max_pu' })))
      .toEqual({ kind: 'series', type: component, name: 'x', field: 'p_max_pu' })
  })
  it('a storage time series has no slot in the panel: its own editor', () => {
    expect(heldHome('profiles', held({ component: 'StorageUnit', entity: 'hydro', field: 'inflow' })).kind).toBe('drawer')
  })
  it('a carrier opens its own card in the drawer (gap 03)', () => {
    expect(heldHome('carriers', held({ component: 'Carrier', entity: 'H2' }))).toEqual({ kind: 'drawer',
      target: { section: 'carriers', panel: null, component: 'Carrier', entity: 'H2', field: null } })
  })

  it('a recipe row opens the recipe card in the drawer (gap 03)', () => {
    expect(heldHome('carriers', held({ entity: 'haber_bosch', recipe: HB }))).toEqual({ kind: 'drawer',
      target: { section: 'carriers', panel: null, component: 'Link', entity: 'haber_bosch', field: RECIPE_FIELD,
        entityLabel: `${HB.label} recipe` } })
  })

  it("the recipe drawer's title names the recipe as the list does, never its Link id", () => {
    const home = heldHome('carriers', held({ entity: 'haber_bosch', recipe: HB }))
    const title = home.kind === 'drawer' ? fieldWords(home.target) : null
    expect(title).toBe(`Carriers & processes › ${HB.label} recipe`)
    expect(title).not.toContain('haber_bosch')
  })
})

describe('section 2 recipe rows (gap 03)', () => {
  it('group under "Recipes", after the carriers, in server order', () => {
    const rows = [held({ component: 'Carrier', entity: 'H2' }), held({ entity: 'haber_bosch', recipe: HB }),
      held({ entity: 'electrolyser', recipe: { ...HB, label: 'Electrolyser' } })]
    const groups = heldGroups(rows)
    expect(groups.map(g => [groupTitle(g.component), g.rows.map(r => r.entity)])).toEqual([
      ['Carriers', ['H2']], ['Recipes', ['haber_bosch', 'electrolyser']]])
  })

  it('show the recipe in words and no class or id', () => {
    const h = held({ entity: 'haber_bosch', recipe: HB })
    expect(heldFigure('carriers', h)).toBe('5.93 MWh Hydrogen + 1,277.55 kWh electricity per t NH₃')
    expect(heldKind(h)).toBe('')
  })
})

it('groups keep the server order, by class', () => {
  const rows = [held({ entity: 'a' }), held({ component: 'Store', entity: 't' }), held({ entity: 'b' })]
  expect(heldGroups(rows).map(g => [g.component, g.rows.map(r => r.entity)]))
    .toEqual([['Generator', ['a', 'b']], ['Store', ['t']]])
})
