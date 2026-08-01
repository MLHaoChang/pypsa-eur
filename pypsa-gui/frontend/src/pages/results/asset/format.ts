/**
 * Number formatting for the Asset Detail tab.
 *
 * One rule, applied everywhere a number reaches the user — the table, the
 * scalar rows, the chart tooltips and the CSV header row: two decimals, with
 * thousands separators. Previously the table used three decimals and the KPI
 * cards used `String(value)`, so the same number read as `1234.568` in one
 * place and `1234.5678901` two centimetres away.
 */

/** Small enough that `toFixed(2)` would render it as a misleading "0.00". */
const TINY = 0.005

export interface NumFormatOptions {
  /** Decimal places. Two everywhere by default. */
  digits?: number
  /** What to render for null / undefined / non-finite. */
  blank?: string
}

/**
 * Format one value for display.
 *
 * Non-numbers pass through as `String(v)` — the payload legitimately carries
 * strings (snapshot stamps, month keys, period labels) in the same rows as
 * numbers, and a table cell should not have to know which it is holding.
 */
export function fmtNum(v: unknown, opts: NumFormatOptions = {}): string {
  const { digits = 2, blank = '' } = opts
  if (v === null || v === undefined) return blank
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (typeof v !== 'number') return String(v)
  // The backend already nulls non-finite values, but this is the component
  // users read actual numbers from — "NaN" must never reach the DOM.
  if (!Number.isFinite(v)) return blank
  if (v === 0) return (0).toFixed(digits)
  // A real value that rounds to zero must not be shown as zero: a capacity
  // factor of 0.0008 pu and a genuine 0 are different results, and rendering
  // both as "0.00" hides a non-running asset behind a running one.
  if (Math.abs(v) < TINY) return v.toExponential(2)
  return v.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/**
 * Format a scalar metric's value, including the dict-valued ones (capacity by
 * vintage, capacity by carrier) which arrive as `{key: number}`.
 */
export function fmtScalar(v: unknown, opts: NumFormatOptions = {}): string {
  if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
    const entries = Object.entries(v as Record<string, unknown>)
    if (entries.length === 0) return opts.blank ?? ''
    return entries.map(([k, val]) => `${k}: ${fmtNum(val, opts)}`).join('   ')
  }
  return fmtNum(v, opts)
}

/** `Label (unit)`, or just `Label` when the metric is unitless. */
export const withUnit = (label: string, unit: string) =>
  unit ? `${label} (${unit})` : label
