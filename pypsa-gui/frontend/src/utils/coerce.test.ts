import { describe, expect, it } from 'vitest'
import { coerceForColumn } from './coerce'

describe('coerceForColumn', () => {
  it('maps an empty input to null (clear the cell)', () => {
    expect(coerceForColumn('', 42)).toBeNull()
    expect(coerceForColumn('', undefined)).toBeNull()
  })

  describe('numeric column (sample is a number)', () => {
    it('parses numbers', () => {
      expect(coerceForColumn('1500', 0)).toBe(1500)
      expect(coerceForColumn('-3.5', 0)).toBe(-3.5)
      expect(coerceForColumn('1e3', 0)).toBe(1000)
    })

    it('rejects non-numeric input as null rather than letting a string through', () => {
      // A string reaching a numeric pandas column upcasts it to `object` and
      // crashes n.export_to_netcdf() at the next save.
      expect(coerceForColumn('abc', 0)).toBeNull()
      expect(coerceForColumn('12abc', 0)).toBeNull()
    })
  })

  describe('boolean column (sample is a boolean)', () => {
    it('recognises the truthy and falsey spellings', () => {
      for (const t of ['true', '1', 'yes']) expect(coerceForColumn(t, true)).toBe(true)
      for (const f of ['false', '0', 'no']) expect(coerceForColumn(f, true)).toBe(false)
    })

    it('passes anything else through for the backend to reject', () => {
      expect(coerceForColumn('maybe', true)).toBe('maybe')
    })
  })

  describe('all-null column (sample is undefined/null) — the documented footgun', () => {
    it('still parses numbers instead of falling through to string', () => {
      // CLAUDE.md: "typing 1500 into an all-null column lands as the string
      // '1500' in the DataFrame and explodes netcdf export at next save".
      expect(coerceForColumn('1500', undefined)).toBe(1500)
      expect(coerceForColumn('1500', null)).toBe(1500)
      expect(coerceForColumn('-0.25', undefined)).toBe(-0.25)
    })

    it('still parses booleans, case-insensitively', () => {
      expect(coerceForColumn('true', undefined)).toBe(true)
      expect(coerceForColumn('TRUE', undefined)).toBe(true)
      expect(coerceForColumn('False', null)).toBe(false)
    })

    it('does not treat whitespace as the number zero', () => {
      // Number('   ') === 0, so without the raw.trim() guard a stray space
      // would silently write 0 into every selected row.
      expect(coerceForColumn('   ', undefined)).toBe('   ')
    })

    it('leaves genuine text alone', () => {
      expect(coerceForColumn('AC', undefined)).toBe('AC')
      expect(coerceForColumn('bus_1', null)).toBe('bus_1')
    })
  })

  it('passes text through for a string column', () => {
    expect(coerceForColumn('bus_2', 'bus_1')).toBe('bus_2')
    expect(coerceForColumn('42', 'bus_1')).toBe('42')
  })
})
