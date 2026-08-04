// The category is a REAL FIELD now (`scenario_type`, backed by a column since
// backend migration 0004). What still needs pinning is the PRECEDENCE between
// that field and the retired `[type]` prefix, because both can arrive in the
// same payload: a bundle exported by an older install carries the tag inline,
// and a user can type a bracket into a description at any time.
import { describe, expect, it } from 'vitest'
import {
  parseScenType, resolveScenType, scenDescriptionText, SCEN_TYPES, SCEN_TYPE_LABEL,
} from './scenarioType'

describe('resolveScenType', () => {
  it('uses the real field when it is set', () => {
    expect(resolveScenType({ scenario_type: 'stress', scenario_description: 'cold winter' }))
      .toEqual({ type: 'stress', text: 'cold winter' })
  })

  it('does not let a bracketed description override the real field', () => {
    // The precedence that matters. A user editing their description to start
    // with "[baseline]" must not silently re-categorise the project — the
    // field is authoritative, the tag is only a fallback.
    expect(resolveScenType({ scenario_type: 'stress', scenario_description: '[baseline] confusing' }))
      .toEqual({ type: 'stress', text: '[baseline] confusing' })
  })

  it('falls back to decoding a legacy tag when the field is empty', () => {
    // An older bundle the backend has not split. Without this the project
    // imports as uncategorised AND shows the marker as prose.
    expect(resolveScenType({ scenario_description: '[baseline] reference run' }))
      .toEqual({ type: 'baseline', text: 'reference run' })
  })

  it('shows no badge for a category this build does not know', () => {
    // The set can grow server-side without a frontend release. An unknown
    // value must degrade to "no badge", never render raw or throw.
    expect(resolveScenType({ scenario_type: 'sensitivity', scenario_description: 'x' }))
      .toEqual({ type: null, text: 'x' })
  })

  it('handles a project with neither field', () => {
    expect(resolveScenType({})).toEqual({ type: null, text: '' })
    expect(resolveScenType({ scenario_type: null, scenario_description: null }))
      .toEqual({ type: null, text: '' })
  })

  it('keeps a plain description when there is no category', () => {
    expect(resolveScenType({ scenario_description: 'just notes' }))
      .toEqual({ type: null, text: 'just notes' })
  })
})

describe('parseScenType (legacy decoder)', () => {
  it('splits every known tag', () => {
    for (const type of SCEN_TYPES) {
      expect(parseScenType(`[${type}] cut the gas fleet`))
        .toEqual({ type, text: 'cut the gas fleet' })
    }
  })

  it('treats a tag-only description as having no text', () => {
    // By far the most common stored value: the old dialog wrote the tag even
    // when the user typed nothing.
    for (const type of SCEN_TYPES) {
      expect(parseScenType(`[${type}]`)).toEqual({ type, text: '' })
    }
  })

  it('leaves an unrecognised bracketed word alone', () => {
    // A looser pattern would eat "[draft]" and silently drop the first word.
    expect(parseScenType('[draft] cut the gas fleet'))
      .toEqual({ type: null, text: '[draft] cut the gas fleet' })
  })

  it('only matches at the start', () => {
    expect(parseScenType('see [stress] for the comparison'))
      .toEqual({ type: null, text: 'see [stress] for the comparison' })
  })

  it('handles null, undefined and empty', () => {
    for (const v of [null, undefined, '']) {
      expect(parseScenType(v)).toEqual({ type: null, text: '' })
    }
  })

  it('keeps a multi-line description intact after the tag', () => {
    expect(parseScenType('[stress] line one\nline two').text).toBe('line one\nline two')
  })
})

describe('scenDescriptionText', () => {
  it('is falsy exactly when there is nothing to render', () => {
    // Callers gate their <p> on truthiness, so a tag-only description must
    // come back '' rather than the raw marker.
    expect(scenDescriptionText('[baseline]')).toBeFalsy()
    expect(scenDescriptionText(null)).toBeFalsy()
    expect(scenDescriptionText('[baseline] reference run')).toBeTruthy()
    expect(scenDescriptionText('plain notes')).toBeTruthy()
  })

  it('never returns a string still carrying a type marker', () => {
    for (const type of SCEN_TYPES) {
      expect(scenDescriptionText(`[${type}] whatever`)).not.toContain(`[${type}]`)
    }
  })
})

describe('SCEN_TYPE_LABEL', () => {
  it('labels every category the picker offers', () => {
    // The picker maps over SCEN_TYPES and indexes this — a missing entry
    // renders an empty <option> rather than failing loudly.
    for (const type of SCEN_TYPES) {
      expect(SCEN_TYPE_LABEL[type]).toBeTruthy()
    }
  })
})
