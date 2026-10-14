// S1b: the form logic of the schema-rendered asset cards
// (plan 2026-10-13-s1-expert-asset-cards.md). The card holds display strings
// (`FS`); these functions turn stored values into them and back, one rule per
// descriptor: `display = stored × scale`, the blank policy, never a derived
// field. Pure, so the cards stay thin and the rules are tested once.
import type { AssetFieldSpec, AssetSchema } from '../api/assetSchema'

/** Units as the app writes them: the descriptors say EUR, the cards say €. */
export function unitLabel(u: string): string {
  return u.replace(/\bEUR\b/g, '€')
}

// Stored units that only restate their label ("Build year (year)").
const SELF_EVIDENT_UNITS = new Set(['year'])

/**
 * A raw-table column header from the asset schema (S1d): the descriptor's
 * label with its STORED unit, since the bottom grid and the generation stack
 * show the column as stored (€/MW, a fraction), not as a card displays it
 * (€/kW, %). Null when the schema does not describe the column.
 */
export function storedHeader(schema: AssetSchema | undefined, key: string): string | null {
  const f = schema?.groups.flatMap(g => g.fields).find(x => x.key === key)
  if (!f) return null
  const unit = f.unit_storage && !SELF_EVIDENT_UNITS.has(f.unit_storage) ? ` (${unitLabel(f.unit_storage)})` : ''
  return `${f.label}${unit}`
}

type FS = Record<string, string>

/** Twelve significant digits: enough for any money or rate, none of the float noise a scale adds. */
export function tidy(v: number): string {
  return String(Number(v.toPrecision(12)))
}

function finite(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

/** A stored value as the person reads it; null when not established. */
export function displayValue(spec: AssetFieldSpec, stored: unknown): number | null {
  return finite(stored) ? Number((stored * spec.scale).toPrecision(12)) : null
}

/** The annuity (capital recovery) factor, as `periodized_costs._annuity`: r(1+r)^L / ((1+r)^L − 1). */
export function annuity(rate: number, lifetime: number): number {
  if (lifetime <= 0) return 0
  if (rate === 0) return 1 / lifetime
  const f = (1 + rate) ** lifetime
  return rate * f / (f - 1)
}

/**
 * The stored value a derived row shows. A single-part asset priced by an
 * overnight cost is charged `overnight × annuity(rate, lifetime)` by PyPSA,
 * whatever its `capital_cost` column holds (0 for a pack-priced template, or
 * stale after the overnight cost is edited), so that is its capital cost. A
 * battery's parts keep `overnight_cost` empty and its `capital_cost` derived
 * on every write, so it reads the column. Without a rate the column stands:
 * the solve fills the global rate in.
 */
export function derivedStored(key: string, asset: Record<string, unknown>): unknown {
  if (key !== 'capital_cost') return asset[key]
  const { overnight_cost: overnight, lifetime, discount_rate: rate } = asset
  if (finite(overnight) && overnight > 0 && finite(lifetime) && lifetime > 0 && finite(rate)) {
    return overnight * annuity(rate, lifetime)
  }
  return asset.capital_cost
}

/** The edit form's starting strings, for the typed (non-derived) fields only. */
export function seedForm(specs: AssetFieldSpec[], asset: Record<string, unknown>): FS {
  const out: FS = {}
  for (const s of specs) {
    if (s.derived) continue
    const v = asset[s.key]
    if (s.kind === 'bool') out[s.key] = v === true ? 'true' : v === false ? 'false' : ''
    else if (s.kind === 'number' || s.kind === 'int') out[s.key] = finite(v) ? tidy(v * s.scale) : ''
    else out[s.key] = v === null || v === undefined ? '' : String(v)
  }
  return out
}

function blankValue(spec: AssetFieldSpec, current: Record<string, unknown>): unknown {
  if (spec.blank === 'clear') return null
  if (spec.blank === 'zero') return 0
  return current[spec.key]
}

/**
 * The PUT patch for the typed fields present in the form. A blank follows the
 * field's policy (null, 0, or the current value); a number that does not
 * parse keeps the current value; an enum outside its options is a blank.
 */
export function buildPatch(specs: AssetFieldSpec[], fs: FS, current: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const s of specs) {
    if (s.derived || !(s.key in fs)) continue
    const raw = (fs[s.key] ?? '').trim()
    if (s.kind === 'bool') {
      out[s.key] = raw === '' ? blankValue(s, current) : raw === 'true'
      continue
    }
    if (s.kind === 'number' || s.kind === 'int') {
      if (raw === '') { out[s.key] = blankValue(s, current); continue }
      const n = s.kind === 'int' ? parseInt(raw, 10) : parseFloat(raw)
      out[s.key] = Number.isFinite(n) ? Number((n / s.scale).toPrecision(15)) : current[s.key]
      continue
    }
    if (s.kind === 'enum') {
      out[s.key] = raw === '' ? blankValue(s, current) : s.options.includes(raw) ? raw : null
      continue
    }
    out[s.key] = raw === '' ? blankValue(s, current) : raw
  }
  return out
}

/** Whether a field shows, given the form's current strings (`reveal_when`: every named field must match). */
export function isRevealed(spec: AssetFieldSpec, fs: FS): boolean {
  if (!spec.reveal_when) return true
  return Object.entries(spec.reveal_when).every(([k, want]) => {
    const raw = fs[k] ?? ''
    if (typeof want === 'boolean') return (raw === 'true') === want
    return raw === String(want)
  })
}
