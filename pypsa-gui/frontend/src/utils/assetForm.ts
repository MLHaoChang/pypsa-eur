// S1b: the form logic of the schema-rendered asset cards
// (plan 2026-10-13-s1-expert-asset-cards.md). The card holds display strings
// (`FS`); these functions turn stored values into them and back, one rule per
// descriptor: `display = stored × scale`, the blank policy, never a derived
// field. Pure, so the cards stay thin and the rules are tested once.
import type { AssetFieldSpec } from '../api/assetSchema'

type FS = Record<string, string>

/** Twelve significant digits: enough for any money or rate, none of the float noise a scale adds. */
function tidy(v: number): string {
  return String(Number(v.toPrecision(12)))
}

function finite(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

/** A stored value as the person reads it; null when not established. */
export function displayValue(spec: AssetFieldSpec, stored: unknown): number | null {
  return finite(stored) ? Number((stored * spec.scale).toPrecision(12)) : null
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
