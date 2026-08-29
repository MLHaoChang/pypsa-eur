// attributeCatalog — editability (D13), series shadow (D14), header labels
// (D15) and editor resolution (D4). Pure module: no React, no DOM.
import { describe, expect, it } from 'vitest'
import {
  CLOSED_SETS, EDITABILITY_OVERRIDES, buildSeriesIndex, columnHeaderLabel,
  columnHeaderTooltip, isBooleanDtype, isNumericDtype, resolveEditability,
  resolveEditor, toCatalogMap,
} from './attributeCatalog'
import type { CatalogAttribute } from '../api/types'

function attr(over: Partial<CatalogAttribute> & { name: string }): CatalogAttribute {
  return {
    status: 'Input (optional)', varying: false, dtype: 'float64', unit: null,
    description: null, type: 'float', default: 0, default_text: '0.0', ...over,
  }
}

const GEN = toCatalogMap([
  attr({ name: 'name', dtype: 'object', status: 'Input (required)' }),
  attr({ name: 'bus', dtype: 'object', status: 'Input (required)', type: 'string' }),
  attr({ name: 'bus0', dtype: 'object' }),
  attr({ name: 'carrier', dtype: 'object' }),
  attr({ name: 'p_nom', unit: 'MW' }),
  attr({ name: 'p_nom_max', unit: 'MW', default: null, default_text: 'inf' }),
  attr({ name: 'p_nom_extendable', dtype: 'bool', type: 'boolean' }),
  attr({ name: 'committable', dtype: 'bool', type: 'boolean' }),
  attr({ name: 'marginal_cost', varying: true, unit: 'currency/MWh' }),
  attr({ name: 'p_nom_opt', status: 'Output', unit: 'MW' }),
  attr({ name: 'control', dtype: 'object', status: 'Output' }),
])

const NO_SERIES: Map<string, Set<string>> = new Map()

describe('dtype classification', () => {
  it('classifies the dtype names the backend actually emits', () => {
    expect(isBooleanDtype('bool')).toBe(true)
    expect(isNumericDtype('float64')).toBe(true)
    expect(isNumericDtype('int64')).toBe(true)
    expect(isNumericDtype('object')).toBe(false)
    expect(isBooleanDtype('object')).toBe(false)
    expect(isNumericDtype('bool')).toBe(false)
  })
})

describe('resolveEditability', () => {
  const base = { componentClass: 'Generator', rowName: 'gas', catalog: GEN, series: NO_SERIES }

  it('never allows editing `name`', () => {
    expect(resolveEditability({ ...base, column: 'name' }))
      .toEqual({ editable: false, reason: 'name' })
  })

  it('allows an Input attribute', () => {
    expect(resolveEditability({ ...base, column: 'p_nom' })).toEqual({ editable: true })
  })

  it('refuses an Output attribute', () => {
    expect(resolveEditability({ ...base, column: 'p_nom_opt' }))
      .toEqual({ editable: false, reason: 'output' })
  })

  // Reversed on 2026-08-13. Refusing an undeclared column made `country` —
  // a PyPSA-Eur column on n.buses that is absent from PyPSA's own schema —
  // permanently read-only, and the same for every other frame extra. PyPSA
  // declares everything it COMPUTES, so an undeclared column is user data,
  // and PATCH /_bulk already accepts it (`col in df.columns`).
  it('allows a column PyPSA does not define, once the catalog has loaded', () => {
    expect(resolveEditability({ ...base, column: 'my_custom_col' }))
      .toEqual({ editable: true })
  })

  it('refuses everything while the catalog is still empty', () => {
    // The loading state, where an empty map would otherwise read as "PyPSA
    // declares nothing" and make the whole grid editable.
    expect(resolveEditability({ ...base, column: 'my_custom_col', catalog: new Map() }))
      .toEqual({ editable: false, reason: 'unknown' })
  })

  it('refuses Generator.committable despite its Input status (override)', () => {
    expect(resolveEditability({ ...base, column: 'committable' }))
      .toEqual({ editable: false, reason: 'override' })
  })

  it('refuses Link.committable for the same reason as Generator.committable', () => {
    // _bulk writes df.loc directly, and its header names flipping
    // `committable` as unsupported through that path — for every class that
    // carries the flag, not just Generator. Links carry it too.
    const links = toCatalogMap([attr({ name: 'committable', dtype: 'bool', type: 'boolean' })])
    expect(resolveEditability({
      componentClass: 'Link', column: 'committable', rowName: 'L1',
      catalog: links, series: NO_SERIES,
    })).toEqual({ editable: false, reason: 'override' })
  })

  it('allows Bus.control despite its Output status (override)', () => {
    const buses = toCatalogMap([attr({ name: 'control', dtype: 'object', status: 'Output' })])
    expect(resolveEditability({
      componentClass: 'Bus', column: 'control', rowName: 'B1',
      catalog: buses, series: NO_SERIES,
    })).toEqual({ editable: true })
  })

  it('still refuses Generator.control, which has no override', () => {
    expect(resolveEditability({ ...base, column: 'control' }))
      .toEqual({ editable: false, reason: 'output' })
  })

  it('refuses a series-shadowed cell on the asset that has the series', () => {
    const series = new Map([['marginal_cost', new Set(['gas'])]])
    expect(resolveEditability({ ...base, column: 'marginal_cost', series }))
      .toEqual({ editable: false, reason: 'series' })
  })

  it('still allows the same column on an asset with no series', () => {
    const series = new Map([['marginal_cost', new Set(['gas'])]])
    expect(resolveEditability({
      ...base, column: 'marginal_cost', rowName: 'solar', series,
    })).toEqual({ editable: true })
  })

  it('does not shadow a non-varying attribute even if the map names it', () => {
    // Defence against a stale series entry: `varying` is the catalog's claim,
    // and only a varying attribute can be shadowed.
    const series = new Map([['p_nom', new Set(['gas'])]])
    expect(resolveEditability({ ...base, column: 'p_nom', series }))
      .toEqual({ editable: true })
  })

  it('documents every override entry with a reason', () => {
    expect(EDITABILITY_OVERRIDES['Bus.control']).toBe('editable')
    expect(EDITABILITY_OVERRIDES['Generator.committable']).toBe('readonly')
    expect(EDITABILITY_OVERRIDES['Link.committable']).toBe('readonly')
    expect(Object.keys(EDITABILITY_OVERRIDES).length).toBe(3)
  })
})

describe('buildSeriesIndex', () => {
  const LISTING = [
    { component: 'generators', attribute: 'p_max_pu', columns: ['solar', 'wind'] },
    { component: 'generators', attribute: 'marginal_cost', columns: ['gas'] },
    { component: 'loads', attribute: 'p_set', columns: ['L1'] },
  ]

  it('keeps only the requested component and keys by attribute', () => {
    const idx = buildSeriesIndex(LISTING, 'generators')
    expect(idx.get('p_max_pu')).toEqual(new Set(['solar', 'wind']))
    expect(idx.get('marginal_cost')).toEqual(new Set(['gas']))
    expect(idx.has('p_set')).toBe(false)
  })

  it('is empty for a component with no series', () => {
    expect(buildSeriesIndex(LISTING, 'stores').size).toBe(0)
  })
})

describe('resolveEditor — D4 rows 1 to 6, in order', () => {
  it('row 1a: Carrier.color is the colour picker', () => {
    const carriers = toCatalogMap([attr({ name: 'color', dtype: 'object' })])
    expect(resolveEditor('Carrier', 'color', carriers)).toBe('color')
  })

  it('row 1b: Bus.control and Generator.control are closed sets', () => {
    const buses = toCatalogMap([attr({ name: 'control', dtype: 'object', status: 'Output' })])
    expect(resolveEditor('Bus', 'control', buses)).toBe('closedSet')
    expect(resolveEditor('Generator', 'control', GEN)).toBe('closedSet')
    expect(CLOSED_SETS['Bus.control']).toEqual(['PQ', 'PV', 'Slack'])
    expect(CLOSED_SETS['Generator.control']).toEqual(['PQ', 'PV', 'Slack'])
  })

  it('row 1b: StorageUnit.control is the same closed set', () => {
    // PyPSA declares control on StorageUnit too (Input, same AC-PF vocabulary).
    // Without this entry the grid fell through to the free-text editor and a
    // paste wrote e.g. "Swing" where only PQ/PV/Slack are meaningful.
    const sus = toCatalogMap([attr({ name: 'control', dtype: 'object', type: 'string' })])
    expect(resolveEditor('StorageUnit', 'control', sus)).toBe('closedSet')
    expect(CLOSED_SETS['StorageUnit.control']).toEqual(['PQ', 'PV', 'Slack'])
  })

  it('row 2: every bus terminal column gets the bus picker', () => {
    expect(resolveEditor('Generator', 'bus', GEN)).toBe('bus')
    expect(resolveEditor('Generator', 'bus0', GEN)).toBe('bus')
  })

  it('row 2 does not capture a column that merely starts with "bus"', () => {
    const m = toCatalogMap([attr({ name: 'bus_carrier', dtype: 'object' })])
    expect(resolveEditor('Generator', 'bus_carrier', m)).toBe('text')
  })

  it('row 3: carrier gets the carrier dropdown', () => {
    expect(resolveEditor('Generator', 'carrier', GEN)).toBe('carrier')
  })

  it('row 4: a boolean dtype gets the checkbox', () => {
    expect(resolveEditor('Generator', 'p_nom_extendable', GEN)).toBe('boolean')
  })

  it('row 5: a numeric dtype gets the inf-aware numeric input', () => {
    expect(resolveEditor('Generator', 'p_nom', GEN)).toBe('numeric')
  })

  it('row 6: anything else is plain text', () => {
    const m = toCatalogMap([attr({ name: 'nice_name', dtype: 'object' })])
    expect(resolveEditor('Carrier', 'nice_name', m)).toBe('text')
  })

  it('falls back to text for a column with no catalog entry', () => {
    expect(resolveEditor('Generator', 'my_custom_col', GEN)).toBe('text')
  })
})

describe('columnHeaderLabel — D15', () => {
  const COL_LABELS = { v_nom: 'V nom (kV)', p_nom: 'P nom (MW)' }

  it('prefers a curated COL_LABELS entry', () => {
    expect(columnHeaderLabel('p_nom', GEN, COL_LABELS)).toBe('P nom (MW)')
  })

  it('falls back to the catalog unit', () => {
    const lines = toCatalogMap([attr({ name: 'r', unit: 'Ohm' })])
    expect(columnHeaderLabel('r', lines, {})).toBe('r (Ohm)')
  })

  it('uses the bare column name when there is no unit', () => {
    expect(columnHeaderLabel('carrier', GEN, {})).toBe('carrier')
  })

  it('uses the bare column name for a column with no catalog entry', () => {
    expect(columnHeaderLabel('my_custom_col', GEN, {})).toBe('my_custom_col')
  })
})

describe('columnHeaderTooltip — D15', () => {
  const lines = toCatalogMap([
    attr({ name: 'r', unit: 'Ohm', description: 'Series resistance' }),
    attr({ name: 'x', unit: 'Ohm', description: 'Series reactance' }),
    attr({ name: 'b', unit: 'S', description: 'Shunt susceptance' }),
    attr({ name: 's_nom', unit: 'MVA', description: 'Rating' }),
  ])

  it('tells the user the properties panel shows r/x/b per km', () => {
    for (const col of ['r', 'x', 'b']) {
      const tip = columnHeaderTooltip('Line', col, lines)
      expect(tip).toContain('per km')
    }
  })

  it('leaves other Line columns with just their description', () => {
    expect(columnHeaderTooltip('Line', 's_nom', lines)).toBe('Rating')
  })

  it('does not add the per-km note on a non-Line component', () => {
    const gens = toCatalogMap([attr({ name: 'r', unit: 'Ohm', description: 'Resistance' })])
    expect(columnHeaderTooltip('Generator', 'r', gens)).toBe('Resistance')
  })

  it('returns null when there is nothing to say', () => {
    expect(columnHeaderTooltip('Generator', 'my_custom_col', GEN)).toBe(null)
  })
})
