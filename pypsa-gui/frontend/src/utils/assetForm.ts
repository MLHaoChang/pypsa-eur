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

/** A unit as a label shows it, or undefined when it only restates the label ("Build year (year)"). */
export function visibleUnit(u: string | null | undefined): string | undefined {
  return u && !SELF_EVIDENT_UNITS.has(u) ? unitLabel(u) : undefined
}

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
  if (spec.blank === 'empty') return ''
  return current[spec.key]
}

/**
 * Leave a composite asset's derived cost columns out of its PUT (TCU 09). `updateAsset` spreads the cached row
 * under the patch, and the backend refuses a derived cost that differs from the stored one while the asset's
 * investment parts own it; an undefined key drops out of the JSON body, so the stored value stands.
 */
export function omitDerived(specs: AssetFieldSpec[], patch: object): void {
  const out = patch as Record<string, unknown>
  for (const s of specs) if (s.derived) out[s.key] = undefined
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

/**
 * Whether a field shows, given the form's current strings (`reveal_when`: every named field must match; a list
 * of values matches any of them).
 */
export function isRevealed(spec: AssetFieldSpec, fs: FS): boolean {
  if (!spec.reveal_when) return true
  return Object.entries(spec.reveal_when).every(([k, want]) => {
    const raw = fs[k] ?? ''
    if (typeof want === 'boolean') return (raw === 'true') === want
    if (Array.isArray(want)) return want.map(String).includes(raw)
    return raw === String(want)
  })
}


/** The cost fields a bus unit changes the scale of (05d): the typed costs of an investment, O&M and operation. */
const COST_KEYS = ['overnight_cost', 'capital_cost', 'fom_cost', 'marginal_cost', 'inv_power_overnight', 'inv_energy_overnight']

/**
 * What the costs on screen are per: `tonne` when the schema's cost units say so (EUR/t, EUR/(t/h)), `energy`
 * otherwise, undefined without a cost field. Sent with a write as `X-Cost-Basis`; the server refuses it when the
 * asset's bus, as it will be stored, is the other kind (a stale unit must never store a cost x1000 off).
 */
export function costBasisOf(schema: AssetSchema | undefined): 'tonne' | 'energy' | undefined {
  const f = schema?.groups.flatMap(g => g.fields).find(x => COST_KEYS.includes(x.key) && x.unit_display)
  if (!f) return undefined
  return /^EUR\/(\(t\/h\)|t)(\/|$)/.test(f.unit_display.replace('€', 'EUR')) ? 'tonne' : 'energy'
}

/** A cost whose unit follows the bus (EUR/kW, EUR/kWh, EUR/MWh on an energy bus; EUR/(t/h), EUR/t on a tonne bus). */
const BUS_SCALED_COST = /^(EUR|€)\/(\(t\/h\)|t|kW|kWh|MWh)(\/|$)/

/**
 * The claim an edit card sends with its save (05d, GK B2 U1). A save that moves the asset to another bus and leaves
 * every bus-scaled cost as seeded sends those costs as stored and no claim, so a bus move alone always saves; the
 * costs are then entered in the new bus's unit. A save that moves the bus and changes a cost keeps the claim, and
 * the server refuses it, saying to save the bus change first. `busKey` is the bus the size is measured on.
 */
export function cardCostClaim(
  schema: AssetSchema | undefined, specs: AssetFieldSpec[], form: FS, asset: Record<string, unknown>,
  busKey: 'bus' | 'bus0',
): { costBasis?: 'tonne' | 'energy'; dropCosts: string[] } {
  const formBus = String(form[busKey] ?? '').trim()
  const moved = formBus !== '' && formBus !== String(asset[busKey] ?? '')
  const seed = seedForm(specs, asset)
  const costs = specs.filter(s => !s.derived && BUS_SCALED_COST.test(s.unit_display)).map(s => s.key)
  const touched = costs.some(k => (form[k] ?? '') !== (seed[k] ?? ''))
  if (moved && !touched) return { costBasis: undefined, dropCosts: costs }
  return { costBasis: costBasisOf(schema), dropCosts: [] }
}
