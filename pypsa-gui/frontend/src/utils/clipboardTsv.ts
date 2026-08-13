// ── Clipboard TSV ────────────────────────────────────────────────────────────
// Pure: no React, no DOM (spec D2). Owns the wire format the grid copies and
// pastes, and the shape detection that resolves a clipboard matrix against the
// paste target (D6, D7).
//
// No quote grammar. A cell is the literal text between tabs. No PyPSA
// attribute in any of the nine tabs holds a tab or a newline — names, carriers,
// booleans and numbers are the whole domain — so an escaping surface would add
// a failure mode for no measured need. The same fact is why the injection guard
// below omits the house rule's tab and CR triggers: those arms are unreachable,
// and an unreachable branch reads as protection that is not there.

const CELL_SEP = '\t'
/** CRLF on the way out: matches CLAUDE.md:575-576 and is what Excel expects. */
const ROW_SEP_OUT = '\r\n'
/** CRLF, LF or CR on the way in — Excel, Numbers and hand-typed all differ. */
const ROW_SEP_IN = /\r\n|\n|\r/

export function serialiseTsv(matrix: string[][]): string {
  return matrix.map(row => row.join(CELL_SEP)).join(ROW_SEP_OUT)
}

export function parseTsv(text: string): string[][] {
  if (text === '') return []
  const rows = text.split(ROW_SEP_IN)
  // Excel appends exactly one trailing separator; a second empty row is real
  // data and is kept.
  if (rows.length > 1 && rows[rows.length - 1] === '') rows.pop()
  return rows.map(r => r.split(CELL_SEP))
}

/** Leading characters a spreadsheet would execute as a formula. */
const INJECTION_PREFIXES = ['=', '+', '-', '@']

/**
 * CSV-injection guard, scoped to string columns (D6).
 *
 * Numeric and boolean columns are never prefixed, so a negative number
 * round-trips byte-exactly (success criterion 12) — guarding them would turn
 * -3.5 into '-3.5 and break the paste back.
 */
export function guardCell(text: string, isStringColumn: boolean): string {
  if (!isStringColumn) return text
  return INJECTION_PREFIXES.some(p => text.startsWith(p)) ? `'${text}` : text
}

/** Strip exactly one leading single quote, string columns only. */
export function unguardCell(text: string, isStringColumn: boolean): string {
  if (!isStringColumn) return text
  return text.startsWith("'") ? text.slice(1) : text
}

export type PasteShape =
  | { kind: 'fill'; value: string }
  | { kind: 'rowwise'; values: string[] }
  | { kind: 'block'; matrix: string[][] }
  | { kind: 'reject'; message: string }

/**
 * Resolve a clipboard matrix against the paste target (D7).
 *
 * `targetRows` is the checkbox selection if non-empty, otherwise 1 (the active
 * cell's row). `columnsAvailable` is the count of visible columns at or right
 * of the active column.
 *
 * Rule order is load-bearing only as a tie-break: a 1x1 clipboard onto a
 * 1-row target matches both rule 1 and rule 2 and they do the same thing.
 */
export function resolvePasteShape(
  matrix: string[][], targetRows: number, columnsAvailable: number,
): PasteShape {
  const n = matrix.length
  if (n === 0) {
    return { kind: 'reject', message: 'The clipboard is empty.' }
  }
  const m = matrix[0].length
  if (matrix.some(r => r.length !== m)) {
    return {
      kind: 'reject',
      message: 'The clipboard rows do not all have the same number of columns.',
    }
  }

  if (n === 1 && m === 1) return { kind: 'fill', value: matrix[0][0] }

  const shapes = `Clipboard is ${n} row(s) × ${m} column(s); `
    + `the paste target is ${targetRows} row(s) × ${columnsAvailable} column(s).`

  if (n !== targetRows) return { kind: 'reject', message: shapes }
  if (m === 1) return { kind: 'rowwise', values: matrix.map(r => r[0]) }
  if (m > columnsAvailable) return { kind: 'reject', message: shapes }
  return { kind: 'block', matrix }
}
