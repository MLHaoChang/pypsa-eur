// Study harness issue 08: the plan card's words are the panels' own (UX plan
// verdict 3). The backend side of this parity (every profile envelope's
// component, field and run tool has words here) is
// `backend/tests/test_harness_run_monitor.py`.
import { describe, expect, it } from 'vitest'
import { COMPONENT_LABEL } from '../../utils/componentLabels'
import { FIELD_LABELS } from '../../utils/fieldLabels'
import { ENVELOPE } from './runFixtures'
import { COMPONENT_WORDS, FACT_UNITS, FACT_WORDS, FIELD_WORDS, RUN_WORDS, STAGE_WORDS, UNATTENDED_BLOCK_WORDS, editSubject } from './runWords'

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
      .toBe('Storage “BESS a” · Storage hours')
  })

  it('a counted gate fact has a plain name and a unit', () => {
    for (const fact of Object.keys(FACT_UNITS)) expect(FACT_WORDS[fact], fact).toBeTruthy()
  })

  it('stage names are exactly the nine of design §10', () => {
    expect(Object.values(STAGE_WORDS).join(' · ')).toBe(
      'Brief · Set up · Inputs · Check inputs · Solve · Check results · Explore options · Report · Hand-off')
  })

  it('words only the runs an envelope may list (GK B 8: no sweep or site study)', () => {
    expect(Object.keys(RUN_WORDS)).not.toContain('run_sensitivity_sweep')
    expect(Object.keys(RUN_WORDS)).not.toContain('run_eh_study')
  })

  it('words every reason unattended cannot work, including no notice surface', () => {
    expect(UNATTENDED_BLOCK_WORDS.no_notice).toBe('Unattended needs a way to tell you when the run stops: email is not set up on this server, and in-app notices cannot be saved right now.')
  })
})
