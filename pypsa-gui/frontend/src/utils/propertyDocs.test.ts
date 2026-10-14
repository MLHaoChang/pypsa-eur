// S1d-3: the tooltip of a field the asset schema describes is its
// descriptor's `help` (services/asset_schema/fields.py), the text the Expert
// card, the creation form and the chat tool's attribute docs all read. So
// `propertyDocs` keeps only what the schema does not describe (the Bus, a
// line's per-km impedances), and a described field never comes back here as
// a second, drifting copy.
//
// The old test here pinned the capital- and overnight-cost tooltips' units;
// those tooltips are the descriptors now, and the descriptors' units are
// pinned by the backend's test_asset_schema_fields.py and below.
import { describe, expect, it } from 'vitest'
import { PROPERTY_DOCS } from './propertyDocs'
import type { AssetSchema } from '../api/assetSchema'
import fixture from '../test/fixtures/assetSchema.json'

const SCHEMAS = fixture as unknown as Record<string, AssetSchema>
const PREFIX: Record<string, string> = {
  generator: 'Generator', storage_unit: 'StorageUnit', store: 'Store', load: 'Load',
  link: 'Link', line: 'Line', transformer: 'Transformer',
  // M5: the non-asset components.
  bus: 'Bus', carrier: 'Carrier', global_constraint: 'GlobalConstraint', shunt_impedance: 'ShuntImpedance',
}

function described(cls: string): Set<string> {
  return new Set(SCHEMAS[cls].groups.flatMap(g => g.fields.map(f => f.key)))
}

describe('propertyDocs keeps only what the asset schema does not describe', () => {
  it('names a prefix for every described class', () => {
    expect(Object.values(PREFIX).sort()).toEqual(Object.keys(SCHEMAS).sort())
  })

  it.each(Object.keys(PROPERTY_DOCS))('%s is not a described field', (key) => {
    const [prefix, field] = key.split('.')
    const cls = PREFIX[prefix]
    if (cls) expect(described(cls).has(field), `${key} is described by the ${cls} schema`).toBe(false)
  })

  it('every described field has its own help', () => {
    for (const [cls, schema] of Object.entries(SCHEMAS)) {
      for (const f of schema.groups.flatMap(g => g.fields)) expect(f.help.trim(), `${cls}.${f.key}`).not.toBe('')
    }
  })

  it('an annualised cost reads per year, an overnight cost does not', () => {
    for (const schema of Object.values(SCHEMAS)) {
      for (const f of schema.groups.flatMap(g => g.fields)) {
        if (f.key === 'capital_cost') expect(f.unit_display, schema.component).toMatch(/\/yr$/)
        if (f.key.endsWith('overnight_cost') || f.key.endsWith('_overnight')) {
          expect(f.unit_display, `${schema.component}.${f.key}`).not.toMatch(/\/yr$/)
        }
      }
    }
  })
})
