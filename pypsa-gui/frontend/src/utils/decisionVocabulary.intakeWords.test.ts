import { expect, it } from 'vitest'
import { BASIS_SENTENCE, INTAKE_STEP_LABELS, INTAKE_WORDS_MIRROR as W, VOCAB } from './decisionVocabulary'

// The chat edit card's words (backend `intake_words.py`) are the panel's own.
it('the intake words are the vocabulary’s', () => {
  for (const [step, label] of Object.entries(W.steps)) expect(INTAKE_STEP_LABELS[step as keyof typeof INTAKE_STEP_LABELS]).toBe(label)
  const byField: Record<string, keyof typeof VOCAB> = {
    'site.zone': 'zone', 'site.year': 'year', 'site.connection_mw': 'connection_limit',
    'site.latitude': 'latitude', 'load.annual_mwh': 'annual_mwh',
  }
  for (const [field, key] of Object.entries(byField)) {
    const word = (W.fields as Record<string, { label: string; unit?: string }>)[field]
    expect(word.label).toBe(VOCAB[key].label)
    expect(word.unit).toBe(VOCAB[key].unit === 'MWh/yr' ? 'MWh/yr' : VOCAB[key].unit)
  }
  expect(W.settings.basis.default).toBe(BASIS_SENTENCE)
})
