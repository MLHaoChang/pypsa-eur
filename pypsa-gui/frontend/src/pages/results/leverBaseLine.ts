// The plain-word lines under the Levers table (template-e2e 22 S3): what the
// base design costs at the same target, and which rows cost the same. A read
// of the study's lever table; nothing is computed beyond grouping.
import type { EhLeverOption, EhLeverTable } from '../../api/simulation'

/** "Same cost" sentences per lever kind: `every` when every solved row of the
 *  kind shares one cost, `some` (with `{values}`) when only some rows do. A kind
 *  without an entry gets no line. */
export const LEVER_SAME_COST: Record<string, { every: string; some: string }> = {
  storage_duration: {
    every: 'Same cost at every duration: the study does not use the extra hours.',
    some: 'Same cost at {values}: the study does not use the extra hours.',
  },
  import_cap: {
    every: 'Same cost at every import limit: the study does not use the extra import capacity.',
    some: 'Same cost at {values}: the study does not use the extra import capacity.',
  },
  import_energy: {
    every: 'Same cost at every import energy limit: the study does not use the extra import energy.',
    some: 'Same cost at {values}: the study does not use the extra import energy.',
  },
}

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/** "Base design: €34.7m at the same target. …", or null without a base cost. */
export function leverBaseLine(
  table: EhLeverTable | null | undefined, fmt: (v: number) => string,
): string | null {
  const base = table?.base_cost_eur
  if (!isNum(base)) return null
  return `Base design: ${fmt(base)} at the same target. Each row changes one lever from the base, `
    + 'so its cost differs by what that change saves or adds.'
}

const valueWords = (o: EhLeverOption) => `${o.value}${o.unit ? ` ${o.unit}` : ''}`
const listWords = (xs: string[]) =>
  xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`

/** One sentence per group of two or more solved, effective rows of one kind
 *  that share a cost exactly; "every" only when the group is the whole kind. */
export function leverSameCostLines(table: EhLeverTable | null | undefined): string[] {
  const byKind = new Map<string, EhLeverOption[]>()
  for (const o of table?.options ?? []) {
    if (!(o.status === 'ok' || o.status === 'optimal') || o.ineffective) continue
    if (!isNum(o.cost_at_target_eur) || !LEVER_SAME_COST[o.kind]) continue
    byKind.set(o.kind, [...(byKind.get(o.kind) ?? []), o])
  }
  const lines: string[] = []
  for (const [kind, rows] of byKind) {
    const words = LEVER_SAME_COST[kind]
    const groups: EhLeverOption[][] = []
    for (const o of rows) {
      const g = groups.find(x => x[0].cost_at_target_eur === o.cost_at_target_eur)
      if (g) g.push(o)
      else groups.push([o])
    }
    for (const g of groups) {
      if (g.length < 2) continue
      lines.push(g.length === rows.length
        ? words.every
        : words.some.replace('{values}', listWords(g.map(valueWords))))
    }
  }
  return lines
}
