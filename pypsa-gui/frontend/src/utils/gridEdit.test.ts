// gridEdit — the one validate-then-coerce entry point every commit goes
// through, whether it came from a typed editor or a paste (spec D4, D7, D12).
import { describe, expect, it } from 'vitest'
import { validateAndCoerce, parseInfinityToken } from './gridEdit'
import { toCatalogMap } from './attributeCatalog'
import { coerceForColumn } from './coerce'
import type { CatalogAttribute } from '../api/types'

function attr(over: Partial<CatalogAttribute> & { name: string }): CatalogAttribute {
  return {
    status: 'Input (optional)', varying: false, dtype: 'float64', unit: null,
    description: null, type: 'float', default: 0, default_text: '0.0', ...over,
  }
}

const CTX = {
  componentClass: 'Generator',
  catalog: toCatalogMap([
    attr({ name: 'p_nom', unit: 'MW' }),
    attr({ name: 'p_nom_max', unit: 'MW' }),
    attr({ name: 'p_nom_extendable', dtype: 'bool', type: 'boolean' }),
    attr({ name: 'bus', dtype: 'object', type: 'string' }),
    attr({ name: 'carrier', dtype: 'object', type: 'string' }),
    attr({ name: 'control', dtype: 'object', status: 'Output' }),
    attr({ name: 'nice_name', dtype: 'object', type: 'string' }),
  ]),
  busNames: new Set(['North', 'South', 'Bus A']),
}

describe('the blank rule — delegated to coerce.ts, unmodified (D2, D12)', () => {
  it('an empty cell commits null', () => {
    expect(validateAndCoerce('p_nom', '', CTX)).toEqual({ ok: true, value: null })
  })

  it('a whitespace-only cell is also blank', () => {
    expect(validateAndCoerce('p_nom', '   ', CTX)).toEqual({ ok: true, value: null })
  })

  it('blank returns exactly what coerce.ts returns for a blank', () => {
    // Not a tautology: this asserts the DELEGATION, i.e. that gridEdit did not
    // reimplement the blank rule with its own literal.
    const r = validateAndCoerce('p_nom', '', CTX)
    expect(r).toEqual({ ok: true, value: coerceForColumn('', undefined) })
  })

  it('blanks a string column to null too', () => {
    expect(validateAndCoerce('nice_name', '', CTX)).toEqual({ ok: true, value: null })
  })
})

describe('the infinity grammar (D12)', () => {
  it.each([
    ['inf', 'inf'], ['INF', 'inf'], ['+inf', 'inf'], ['infinity', 'inf'],
    ['Infinity', 'inf'], ['∞', 'inf'],
    ['-inf', '-inf'], ['-INFINITY', '-inf'], ['-∞', '-inf'],
  ])('parses %s as %s', (raw, expected) => {
    expect(parseInfinityToken(raw)).toBe(expected)
  })

  it('is not fooled by a number or a word', () => {
    expect(parseInfinityToken('12')).toBe(null)
    expect(parseInfinityToken('information')).toBe(null)
    expect(parseInfinityToken('')).toBe(null)
  })

  it('commits infinity as the STRING "inf", never the JS number', () => {
    // JSON.stringify(Infinity) is "null", which would silently become the
    // backend's blank rule instead of the user's inf.
    const r = validateAndCoerce('p_nom_max', 'inf', CTX)
    expect(r).toEqual({ ok: true, value: 'inf' })
    expect(JSON.stringify({ v: 'inf' })).toBe('{"v":"inf"}')
  })

  it('commits negative infinity as "-inf"', () => {
    expect(validateAndCoerce('p_nom_max', '-inf', CTX)).toEqual({ ok: true, value: '-inf' })
  })
})

describe('numeric columns (D12)', () => {
  it('accepts a plain number', () => {
    expect(validateAndCoerce('p_nom', '120', CTX)).toEqual({ ok: true, value: 120 })
  })

  it('accepts a negative number', () => {
    expect(validateAndCoerce('p_nom', '-3.5', CTX)).toEqual({ ok: true, value: -3.5 })
  })

  it('rejects a non-numeric string instead of silently clearing it', () => {
    // coerce.ts:19 returns null here, which clears the field. That is the
    // deliberate, named behaviour change (D12) — coerce.ts itself is unchanged.
    const r = validateAndCoerce('p_nom', '12o0', CTX)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toContain('12o0')
  })

  it('rejects NaN as a typed token', () => {
    expect(validateAndCoerce('p_nom', 'nan', CTX).ok).toBe(false)
  })
})

describe('bus columns — exact and case-sensitive (D4)', () => {
  it('accepts an existing bus name', () => {
    expect(validateAndCoerce('bus', 'North', CTX)).toEqual({ ok: true, value: 'North' })
  })

  it('rejects a name differing only in case', () => {
    // BusAutocomplete's own exactMatch lower-cases both sides, which is right
    // for the creation form and wrong here: PyPSA's index lookup is
    // case-sensitive, so "NORTH" is a dangling reference (criterion 19).
    const r = validateAndCoerce('bus', 'NORTH', CTX)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toContain('NORTH')
  })

  it('rejects a bus that does not exist', () => {
    expect(validateAndCoerce('bus', 'Nrth', CTX).ok).toBe(false)
  })

  it('accepts a bus name containing spaces', () => {
    expect(validateAndCoerce('bus', 'Bus A', CTX)).toEqual({ ok: true, value: 'Bus A' })
  })
})

describe('the carrier column accepts the unknown (D4)', () => {
  it('accepts a carrier the network does not have yet', () => {
    // bulk_update calls ensure_carrier and creates the row (criterion 20).
    expect(validateAndCoerce('carrier', 'brand_new', CTX))
      .toEqual({ ok: true, value: 'brand_new' })
  })
})

describe('closed-set columns (D4)', () => {
  it('accepts each option', () => {
    for (const v of ['PQ', 'PV', 'Slack']) {
      expect(validateAndCoerce('control', v, CTX)).toEqual({ ok: true, value: v })
    }
  })

  it('rejects anything else, naming the allowed set', () => {
    const r = validateAndCoerce('control', 'Swing', CTX)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toContain('Slack')
  })
})

describe('boolean columns — case-insensitive (D4)', () => {
  it.each([
    ['true', true], ['TRUE', true], ['True', true], ['1', true], ['YES', true],
    ['false', false], ['FALSE', false], ['0', false], ['No', false],
  ])('accepts %s', (raw, expected) => {
    expect(validateAndCoerce('p_nom_extendable', raw, CTX))
      .toEqual({ ok: true, value: expected })
  })

  it('rejects a token that is neither', () => {
    expect(validateAndCoerce('p_nom_extendable', 'maybe', CTX).ok).toBe(false)
  })
})

describe('text columns', () => {
  it('passes the text through unchanged', () => {
    expect(validateAndCoerce('nice_name', 'Combined Cycle', CTX))
      .toEqual({ ok: true, value: 'Combined Cycle' })
  })

  it('does not trim interior spacing', () => {
    expect(validateAndCoerce('nice_name', 'a  b', CTX))
      .toEqual({ ok: true, value: 'a  b' })
  })
})

describe('an unknown column', () => {
  // Was "is rejected rather than guessed at". Reversed on 2026-08-13: the
  // grid's columns come from the DATA, so an undeclared column is a real
  // column carrying real user values (`country` on a PyPSA-Eur network), and
  // rejecting it made every one of them uneditable. Nothing is guessed —
  // there is no dtype to coerce against, so the raw string is committed and
  // the backend enforces `col in df.columns`.
  it('commits as free text rather than being rejected', () => {
    expect(validateAndCoerce('mystery', '5', CTX)).toEqual({ ok: true, value: '5' })
  })
})

describe('a column PyPSA does not declare — found in the app, 2026-08-13', () => {
  // The editability rule was fixed first, and this gate was missed: the grid
  // let you open an editor on `country` and then refused the commit with
  // "Paste rejected — cannot write B0 / country". Two gates, one rule.
  it('accepts a value for an undeclared column as free text', () => {
    expect(validateAndCoerce('country', 'ES', CTX)).toEqual({ ok: true, value: 'ES' })
  })

  it('still refuses everything while the catalog is empty', () => {
    expect(validateAndCoerce('country', 'ES', { ...CTX, catalog: new Map() }))
      .toEqual({ ok: false, error: expect.stringContaining('still loading') })
  })

  it('a blank into an undeclared column clears it, as everywhere else', () => {
    expect(validateAndCoerce('country', '', CTX)).toEqual({ ok: true, value: null })
  })
})
