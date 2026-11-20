/**
 * A process's recipe in words, and back to its Link's efficiencies (template-e2e gap 03).
 *
 * The server reads the recipe (`services/recipes.py`, the flow's section 2 rows): electricity and
 * each input per unit of product. Saving turns the rates back into the Link's efficiencies, the
 * inverse of that reader and the same arithmetic as `processUnit.ts`'s creation form:
 *
 *   efficiency  = product_per / electricity        (electricity in MWh per recipe unit)
 *   efficiencyN = -(input N) / electricity
 */
import type { FlowRecipe } from '../api/flow'

const num = (v: number): string => v.toLocaleString('en-GB', { maximumFractionDigits: 2 })

/** "tonne of NH3" or "MWh of H2": what every rate is per. */
export const recipePer = (r: FlowRecipe): string =>
  `${r.per === 't' ? 'tonne' : 'MWh'} of ${r.product.label}`

/** A field's label, the words its refusal opens with. */
export const electricityLabel = (r: FlowRecipe): string => `Electricity per ${recipePer(r)}`
export const inputLabel = (r: FlowRecipe, i: number): string => `${r.inputs[i].label} per ${recipePer(r)}`

/** The row's one figure: "5.93 MWh H2 + 1,277.55 kWh electricity per t NH3". */
export function recipeFigure(r: FlowRecipe): string {
  const parts = r.inputs.map(x => `${num(x.value)} ${x.unit} ${x.label}`)
  parts.push(`${num(r.electricity.value)} ${r.electricity.unit} electricity`)
  return `${parts.join(' + ')} per ${r.per} ${r.product.label}`
}

/** A typed value: a finite number above 0, or the refusal sentence opening with its label. */
function positive(label: string, raw: string): number | string {
  const text = raw.trim()
  const v = text === '' ? NaN : Number(text)
  if (!Number.isFinite(v)) return `${label}: enter a number`
  if (!(v > 0)) return `${label}: must be above 0`
  return v
}

export type RecipePatch = Record<string, number> & { efficiency: number }

/**
 * The Link patch for the typed rates, or the first refusal. `electricity` is in the recipe's own
 * unit (kWh per tonne, MWh per MWh); `inputs` in the order of `r.inputs`.
 */
export function recipePatch(r: FlowRecipe, electricity: string, inputs: string[]): RecipePatch | string {
  const el = positive(electricityLabel(r), electricity)
  if (typeof el === 'string') return el
  const elMwh = r.electricity.unit === 'kWh' ? el / 1000 : el
  const patch: RecipePatch = { efficiency: r.product_per / elMwh }
  for (let i = 0; i < r.inputs.length; i++) {
    const x = positive(inputLabel(r, i), inputs[i] ?? '')
    if (typeof x === 'string') return x
    patch[`efficiency${r.inputs[i].port}`] = -(x / elMwh)
  }
  return patch
}
