import { describe, expect, it } from 'vitest'
import { fieldWords } from './flowItems'
import { ledgerFieldLabels } from './fieldLabels'

const link = { section: 'connection' as const, component: 'Link', entity: 'grid_import' }

describe('fieldWords', () => {
  it('adds the field label when there is one', () => {
    expect(fieldWords({ ...link, field: 'efficiency' })).toBe("Connection & grid › Link 'grid_import' › Efficiency")
  })
  it('shows the entity alone for a field with no label', () => {
    expect(fieldWords({ ...link, field: 'connection_ramp' })).toBe("Connection & grid › Link 'grid_import'")
    expect(fieldWords(link)).toBe("Connection & grid › Link 'grid_import'")
  })
  it("takes the template ledger's labels over the shell's", () => {
    const labels = ledgerFieldLabels({ ledger: [
      { item: 'ramp', value: 4, source: 'assumed', label: 'Connection ramp limit', field: 'connection_ramp' },
      { item: 'x', value: 1, source: 'assumed', label: 'No field here' },
    ] })
    expect(fieldWords({ ...link, field: 'connection_ramp' }, labels))
      .toBe("Connection & grid › Link 'grid_import' › Connection ramp limit")
    expect(labels.efficiency).toBe('Efficiency')
  })
  it('names a labelled field that has no entity, and nothing otherwise', () => {
    expect(fieldWords({ section: 'scenarios', component: null, entity: null, field: 'target_lole_h' }))
      .toBe('Scenarios & optimisation › LOLE target')
    expect(fieldWords({ section: 'scenarios', component: null, entity: null, field: 'no_label' })).toBeNull()
  })
})
