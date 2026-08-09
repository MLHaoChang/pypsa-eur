import { coerceForColumn } from './coerce'
import {
  CLOSED_SETS, isNumericDtype, resolveEditor, type CatalogMap,
} from './attributeCatalog'

// ── Grid cell validation and coercion ────────────────────────────────────────
// Pure: no React, no DOM (spec D2). This is the SINGLE commit path — a typed
// editor and a paste both arrive here, so a pasted bus name is checked against
// the real bus list exactly as a dropdown selection is (D4, D7). The widget is
// an input affordance; it is never the thing that enforces correctness.
//
// This WRAPS utils/coerce.ts rather than replacing it, so today's blank
// semantics are preserved by construction and coerce.ts's ten existing tests
// stay green unmodified (D2, success criterion 41).

export interface GridEditContext {
  componentClass: string
  catalog: CatalogMap
  /** Every bus name in the network. Membership is exact and case-sensitive. */
  busNames: Set<string>
}

export type CellResult =
  | { ok: true; value: unknown }
  | { ok: false; error: string }

const INFINITY_WORDS = new Set(['inf', 'infinity', '∞'])

/**
 * Recognise the infinity grammar (D12): inf, +inf, -inf, infinity, ∞, -∞,
 * case-insensitive.
 *
 * Returns the STRING 'inf' / '-inf', never a JS number: JSON.stringify(Infinity)
 * is "null", which the backend would read as a blank and turn into its own
 * sentinel instead of the user's infinity. The endpoint's float(value) parses
 * these strings directly (pinned by tests/test_bulk_update.py).
 */
export function parseInfinityToken(raw: string): 'inf' | '-inf' | null {
  const t = raw.trim().toLowerCase()
  if (!t) return null
  const negative = t.startsWith('-')
  const body = negative || t.startsWith('+') ? t.slice(1) : t
  if (!INFINITY_WORDS.has(body)) return null
  return negative ? '-inf' : 'inf'
}

const BOOLEAN_TRUE = new Set(['true', '1', 'yes'])
const BOOLEAN_FALSE = new Set(['false', '0', 'no'])

/**
 * Validate a raw string against its column and coerce it to what the request
 * body should carry. Called identically by a typed commit, a fill and a block
 * paste.
 */
export function validateAndCoerce(
  column: string, raw: string, ctx: GridEditContext,
): CellResult {
  // Blank first, before any type dispatch — coerce.ts owns this rule and the
  // backend re-interprets the resulting null per column name (D12).
  if (raw.trim() === '') {
    return { ok: true, value: coerceForColumn('', undefined) }
  }

  const attr = ctx.catalog.get(column)
  if (!attr) {
    return {
      ok: false,
      error: `Column '${column}' is not a PyPSA attribute of ${ctx.componentClass}.`,
    }
  }

  const kind = resolveEditor(ctx.componentClass, column, ctx.catalog)

  if (kind === 'bus') {
    // Exact and case-sensitive, deliberately stricter than BusAutocomplete's
    // own lower-cased exactMatch: PyPSA's index lookup is case-sensitive, so a
    // case-mismatched name is a dangling reference no layer below would catch.
    if (!ctx.busNames.has(raw)) {
      return {
        ok: false,
        error: `'${raw}' is not an existing bus name (names are case-sensitive).`,
      }
    }
    return { ok: true, value: raw }
  }

  if (kind === 'carrier') {
    // An unknown carrier is accepted: bulk_update calls ensure_carrier and
    // creates the row with catalog metadata. A paste can introduce a carrier;
    // the dropdown cannot, and does not need to.
    return { ok: true, value: raw }
  }

  if (kind === 'closedSet') {
    const options = CLOSED_SETS[`${ctx.componentClass}.${column}`] ?? []
    if (!options.includes(raw)) {
      return { ok: false, error: `'${raw}' is not one of ${options.join(', ')}.` }
    }
    return { ok: true, value: raw }
  }

  if (kind === 'boolean') {
    const t = raw.trim().toLowerCase()
    if (!BOOLEAN_TRUE.has(t) && !BOOLEAN_FALSE.has(t)) {
      return { ok: false, error: `'${raw}' is not a boolean (try true/false, 1/0, yes/no).` }
    }
    // Lower-cased before delegating so coerce.ts's case-sensitive test still
    // matches and that file stays unmodified (D2).
    return { ok: true, value: coerceForColumn(t, true) }
  }

  if (kind === 'numeric' || isNumericDtype(attr.dtype)) {
    const infinite = parseInfinityToken(raw)
    if (infinite) return { ok: true, value: infinite }
    const n = Number(raw)
    if (!Number.isFinite(n)) {
      // A named behaviour change from coerce.ts:19, which returns null here and
      // so silently CLEARS the field on a typo (D12).
      return { ok: false, error: `'${raw}' is not a number.` }
    }
    return { ok: true, value: n }
  }

  // color and text: the literal string.
  return { ok: true, value: raw }
}
