import type { CatalogAttribute } from '../api/types'

// ── Attribute catalog helpers ────────────────────────────────────────────────
// Pure: no React, no DOM (spec D2). Answers four questions about a grid cell —
// may it be edited (D13/D14), which editor does it open (D4), what does its
// column header read (D15), and what does that header's tooltip say.
//
// Plan B appends D22's six reveal rules to this file. They are deliberately
// absent here: Scope A has no consumer for them.

export type CatalogMap = Map<string, CatalogAttribute>

export function toCatalogMap(attributes: CatalogAttribute[]): CatalogMap {
  return new Map(attributes.map(a => [a.name, a]))
}

/** attribute name → the asset names that actually have a series for it. */
export type SeriesIndex = Map<string, Set<string>>

/**
 * Narrow `GET /network/timeseries` to one component and key it by attribute.
 * `componentAttr` is PyPSA's frame name ('generators', 'buses', …), which is
 * what that endpoint reports — not the class name.
 */
export function buildSeriesIndex(
  entries: { component: string; attribute: string; columns: string[] }[],
  componentAttr: string,
): SeriesIndex {
  const idx: SeriesIndex = new Map()
  for (const e of entries) {
    if (e.component !== componentAttr) continue
    const existing = idx.get(e.attribute)
    if (existing) for (const c of e.columns) existing.add(c)
    else idx.set(e.attribute, new Set(e.columns))
  }
  return idx
}

/**
 * Editability that differs from the catalog's `status` (spec D13, ruling 17).
 * Exactly two entries, each with its reason. Keyed `<ComponentClass>.<column>`.
 */
export const EDITABILITY_OVERRIDES: Record<string, 'editable' | 'readonly'> = {
  // Editable against status='Output': it selects the AC-PF slack, the app has
  // always exposed it (CreationForm.tsx:74, PropertiesPanel.tsx:1636), and
  // D22's rule 6 requires it settable.
  'Bus.control': 'editable',
  // Read-only against status='Input': PATCH /_bulk writes df.loc directly and
  // its own header comment names flipping `committable` as unsupported through
  // that path. The right panel's per-row PUT stays the way to change it.
  'Generator.committable': 'readonly',
}

/**
 * Columns whose valid values are a closed set the app already enumerates
 * elsewhere (D4 editor row 1). An entry may be added only when that is true —
 * the set here must match CreationForm.tsx:74 and PropertiesPanel.tsx:346-360.
 */
export const CLOSED_SETS: Record<string, string[]> = {
  'Bus.control': ['PQ', 'PV', 'Slack'],
  'Generator.control': ['PQ', 'PV', 'Slack'],
}

/** PyPSA's terminal columns: bus, bus0, bus1, bus2 … (recon §6). */
const BUS_COLUMN = /^bus\d*$/

export function isBooleanDtype(dtype: string): boolean {
  return dtype === 'bool'
}

export function isNumericDtype(dtype: string): boolean {
  return /^(float|int|uint)/.test(dtype)
}

export type EditabilityReason = 'name' | 'unknown' | 'series' | 'override' | 'output'
export type Editability =
  | { editable: true }
  | { editable: false; reason: EditabilityReason }

/**
 * May this cell be edited?
 *
 * Order is fixed and each step answers a different question:
 *   1. `name` — the backend refuses a bulk rename outright.
 *   2. no catalog entry — a custom column PyPSA does not define. With no dtype
 *      there is nothing to validate against, so read-only is the honest
 *      default rather than a guess.
 *   3. series-shadowed — D14 says "never editable", so it outranks an
 *      `editable` override. No override attribute is varying today, so this
 *      ordering is currently unobservable; it is fixed so it stays true.
 *   4. the override list (D13).
 *   5. the catalog default: Output is read-only.
 */
export function resolveEditability(args: {
  componentClass: string
  column: string
  rowName: string
  catalog: CatalogMap
  series: SeriesIndex
}): Editability {
  const { componentClass, column, rowName, catalog, series } = args

  if (column === 'name') return { editable: false, reason: 'name' }

  const attr = catalog.get(column)
  if (!attr) return { editable: false, reason: 'unknown' }

  // Only a `varying` attribute can be shadowed — the catalog's claim wins over
  // a stale entry in the series listing.
  if (attr.varying && series.get(column)?.has(rowName)) {
    return { editable: false, reason: 'series' }
  }

  const override = EDITABILITY_OVERRIDES[`${componentClass}.${column}`]
  if (override === 'readonly') return { editable: false, reason: 'override' }
  if (override === 'editable') return { editable: true }

  if (attr.status.startsWith('Output')) return { editable: false, reason: 'output' }
  return { editable: true }
}

export type EditorKind =
  | 'color' | 'closedSet' | 'bus' | 'carrier' | 'boolean' | 'numeric' | 'text'

/** D4's editor-resolution table, evaluated top to bottom. */
export function resolveEditor(
  componentClass: string, column: string, catalog: CatalogMap,
): EditorKind {
  const key = `${componentClass}.${column}`
  if (key === 'Carrier.color') return 'color'          // row 1a
  if (CLOSED_SETS[key]) return 'closedSet'             // row 1b
  if (BUS_COLUMN.test(column)) return 'bus'            // row 2
  if (column === 'carrier') return 'carrier'           // row 3

  const attr = catalog.get(column)
  if (!attr) return 'text'
  if (isBooleanDtype(attr.dtype)) return 'boolean'     // row 4
  if (isNumericDtype(attr.dtype)) return 'numeric'     // row 5
  return 'text'                                        // row 6
}

/**
 * Column header text (D15). A curated COL_LABELS entry wins — it is
 * hand-written and already carries its unit ('V nom (kV)'). Otherwise the
 * catalog's unit is appended, so Lines read `r (Ohm)`, `x (Ohm)`, `b (S)`.
 *
 * `colLabels` is passed in rather than imported: this module must not depend
 * on layout/BottomPanel.tsx (D2).
 */
export function columnHeaderLabel(
  column: string, catalog: CatalogMap, colLabels: Record<string, string>,
): string {
  const curated = colLabels[column]
  if (curated) return curated
  const unit = catalog.get(column)?.unit
  return unit ? `${column} (${unit})` : column
}

/**
 * Header tooltip (D15). Lines' r/x/b additionally state the convention split:
 * the grid is the raw-attribute surface and shows PyPSA's absolute values,
 * while the properties panel curates them per km (ruling 19). Without this the
 * same attribute reads as two different numbers in two places with no
 * explanation.
 */
const PER_KM_LINE_COLUMNS = new Set(['r', 'x', 'b'])

export function columnHeaderTooltip(
  componentClass: string, column: string, catalog: CatalogMap,
): string | null {
  const attr = catalog.get(column)
  const description = attr?.description ?? null
  if (componentClass === 'Line' && PER_KM_LINE_COLUMNS.has(column)) {
    const note = 'The properties panel shows this attribute per km; the grid '
      + 'shows the absolute value PyPSA stores.'
    return description ? `${description} — ${note}` : note
  }
  return description
}
