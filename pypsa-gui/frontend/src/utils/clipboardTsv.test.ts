// clipboardTsv — the TSV wire format (D6) and the three paste shapes (D7).
// Pure: no React, no DOM.
import { describe, expect, it } from 'vitest'
import {
  guardCell, parseTsv, resolvePasteShape, serialiseTsv, unguardCell,
} from './clipboardTsv'

describe('serialiseTsv (D6)', () => {
  it('joins cells with tabs and rows with CRLF', () => {
    expect(serialiseTsv([['a', 'b'], ['c', 'd']])).toBe('a\tb\r\nc\td')
  })

  it('emits no trailing terminator', () => {
    expect(serialiseTsv([['a']]).endsWith('\r\n')).toBe(false)
  })

  it('serialises a single cell as itself', () => {
    expect(serialiseTsv([['42']])).toBe('42')
  })

  it('serialises an empty matrix as an empty string', () => {
    expect(serialiseTsv([])).toBe('')
  })
})

describe('parseTsv (D6)', () => {
  it('accepts CRLF', () => {
    expect(parseTsv('a\tb\r\nc\td')).toEqual([['a', 'b'], ['c', 'd']])
  })

  it('accepts LF', () => {
    expect(parseTsv('a\tb\nc\td')).toEqual([['a', 'b'], ['c', 'd']])
  })

  it('accepts CR', () => {
    expect(parseTsv('a\tb\rc\td')).toEqual([['a', 'b'], ['c', 'd']])
  })

  it('CRLF and LF payloads produce identical results (criterion 13)', () => {
    expect(parseTsv('1\r\n2\r\n3')).toEqual(parseTsv('1\n2\n3'))
  })

  it('drops exactly one trailing empty row', () => {
    expect(parseTsv('a\r\nb\r\n')).toEqual([['a'], ['b']])
  })

  it('keeps a second trailing empty row, which is real data', () => {
    expect(parseTsv('a\r\nb\r\n\r\n')).toEqual([['a'], ['b'], ['']])
  })

  it('preserves an empty cell in the middle of a row', () => {
    expect(parseTsv('a\t\tc')).toEqual([['a', '', 'c']])
  })

  it('round-trips through serialise', () => {
    const m = [['1', 'x'], ['2', 'y'], ['3', 'z']]
    expect(parseTsv(serialiseTsv(m))).toEqual(m)
  })

  it('parses an empty string as no rows', () => {
    expect(parseTsv('')).toEqual([])
  })
})

describe('the CSV-injection guard, scoped to string columns (D6)', () => {
  it.each(['=SUM(A1)', '+1', '-1', '@x'])('prefixes %s in a string column', (s) => {
    expect(guardCell(s, true)).toBe(`'${s}`)
  })

  it('leaves an ordinary string alone', () => {
    expect(guardCell('CCGT', true)).toBe('CCGT')
  })

  it('never prefixes a numeric column, so -3.5 round-trips byte-exactly', () => {
    // Criterion 12. A guard here would turn -3.5 into '-3.5 and the paste back
    // would either fail or write a string into a numeric column.
    expect(guardCell('-3.5', false)).toBe('-3.5')
    expect(unguardCell(guardCell('-3.5', false), false)).toBe('-3.5')
  })

  it('strips exactly one leading quote on paste into a string column', () => {
    expect(unguardCell("'=SUM(A1)", true)).toBe('=SUM(A1)')
    expect(unguardCell("''x", true)).toBe("'x")
  })

  it('does not strip a quote for a numeric column', () => {
    expect(unguardCell("'5", false)).toBe("'5")
  })

  it('round-trips a formula-looking carrier name', () => {
    expect(unguardCell(guardCell('-odd_carrier', true), true)).toBe('-odd_carrier')
  })
})

describe('resolvePasteShape (D7)', () => {
  it('1x1 is a fill, whatever the target size', () => {
    expect(resolvePasteShape([['7']], 300, 5)).toEqual({ kind: 'fill', value: '7' })
    expect(resolvePasteShape([['7']], 1, 5)).toEqual({ kind: 'fill', value: '7' })
  })

  it('Nx1 matching the target row count is row-wise', () => {
    expect(resolvePasteShape([['1'], ['2'], ['3']], 3, 5))
      .toEqual({ kind: 'rowwise', values: ['1', '2', '3'] })
  })

  it('NxM matching the target row count is a block', () => {
    const m = [['1', 'a'], ['2', 'b']]
    expect(resolvePasteShape(m, 2, 4)).toEqual({ kind: 'block', matrix: m })
  })

  it('rejects a block wider than the columns available', () => {
    const r = resolvePasteShape([['1', 'a', 'x'], ['2', 'b', 'y']], 2, 2)
    expect(r.kind).toBe('reject')
    if (r.kind === 'reject') expect(r.message).toContain('column')
  })

  it('rejects a row-count mismatch, naming both counts (criterion 7)', () => {
    const r = resolvePasteShape([['1'], ['2']], 5, 3)
    expect(r.kind).toBe('reject')
    if (r.kind === 'reject') {
      expect(r.message).toContain('2')
      expect(r.message).toContain('5')
    }
  })

  it('rejects an empty clipboard', () => {
    expect(resolvePasteShape([], 3, 3).kind).toBe('reject')
  })

  it('rejects a ragged matrix', () => {
    expect(resolvePasteShape([['1', '2'], ['3']], 2, 3).kind).toBe('reject')
  })
})
