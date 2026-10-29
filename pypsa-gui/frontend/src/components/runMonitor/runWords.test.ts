// Study harness issue 08: the plan card's words are the panels' own (UX plan
// verdict 3). The backend side of this parity (every profile envelope's
// component, field and run tool has words here) is
// `backend/tests/test_harness_run_monitor.py`.
import { describe, expect, it } from 'vitest'
import { COMPONENT_LABEL } from '../../utils/componentLabels'
import { FIELD_LABELS } from '../../utils/fieldLabels'
import { ENVELOPE } from './runFixtures'
import { COMPONENT_WORDS, FACT_UNITS, FACT_WORDS, FIELD_WORDS, STAGE_WORDS, editSubject, euros } from './runWords'

describe('runWords', () => {
  it('names an envelope field by the panels’ FIELD_LABELS, with a unit', () => {
    for (const [key, { words, unit }] of Object.entries(FIELD_WORDS)) {
      expect(FIELD_LABELS[key], key).toBeTruthy()
      expect(words, key).toBe(FIELD_LABELS[key])
      expect(unit, key).not.toBe('')
    }
  })

  it('names a component class by the panels’ COMPONENT_LABEL', () => {
    for (const [key, { words }] of Object.entries(COMPONENT_WORDS)) {
      expect(COMPONENT_LABEL[key], key).toBeTruthy()
      expect(words, key).toBe(COMPONENT_LABEL[key])
    }
  })

  it('every fixture edit reads in words, never a raw key', () => {
    for (const edit of ENVELOPE.edits) {
      expect(editSubject(edit)).not.toMatch(/p_nom|max_hours|_new_|Generator|StorageUnit/)
    }
    expect(editSubject({ tool: 'update_component', target: { component: 'StorageUnit', name: 'bess_a', attr: 'max_hours' } }))
      .toBe("Storage 'bess_a' · Storage hours")
  })

  it('a counted gate fact has a plain name and a unit', () => {
    for (const fact of Object.keys(FACT_UNITS)) expect(FACT_WORDS[fact], fact).toBeTruthy()
  })

  it('stage names are exactly the nine of design §10', () => {
    expect(Object.values(STAGE_WORDS).join(' · ')).toBe(
      'Brief · Set up · Inputs · Check inputs · Solve · Check results · Explore options · Report · Hand-off')
  })

  it('money uses the KPI currency format', () => {
    expect(euros(2)).toBe('€2.00')
  })
})
