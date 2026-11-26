// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/** The one money notation (UX-RULES-1107): "€ 1.2 m", "€ 350 k", "€ 900".
 *  The report's `chapters.fmt_eur_short` writes the same strings (a backend test pins it). */
const NBSP = '\u00a0'

export const SYMBOL: Record<string, string> = { EUR: '€', USD: '$', GBP: '£' }

const sig = (v: number, digits: number) =>
  Number.isFinite(v) ? v.toLocaleString('en', { maximumSignificantDigits: digits }) : '–'

/** Money in short form, unsigned (callers write the minus): "€ 1.2 m", "€ 350 k", "€ 900". */
export function money(v: number, currency?: string | null): string {
  const cur = currency ? (SYMBOL[currency] ?? currency) : ''
  const a = Math.abs(v)
  const body = a >= 1e6 ? `${sig(a / 1e6, 2)}${NBSP}m` : a >= 1e3 ? `${sig(a / 1e3, 3)}${NBSP}k` : sig(a, 3)
  return cur ? `${cur}${NBSP}${body}` : body
}

/** A price per unit ("€ 1.73", "€ 439", "€ 1,150"), unsigned: never abbreviated to k/m, since
 *  "€ 1.2 k/t" misreads (UX 30, issue 28a). Below 1,000 it is `money`'s three figures; from
 *  1,000 a whole number with thousands separators. The caller appends the unit ("/kg", "/t"). */
export function unitPrice(v: number, currency?: string | null): string {
  const a = Math.abs(v)
  if (a < 1e3 || !Number.isFinite(a)) return money(v, currency)
  const cur = currency ? (SYMBOL[currency] ?? currency) : ''
  const body = Math.round(a).toLocaleString('en')
  return cur ? `${cur}${NBSP}${body}` : body
}
