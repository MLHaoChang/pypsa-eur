// The words and formats of the Results headline strip (ui-modern 03). The
// backend decides which cards a project type gets and reads their figures
// (`GET /results/headline_kpis`); this file only names and formats them.
// A label is the word its own tab already uses (parity: headlineWords.test.ts).
import type { HeadlineCard, HeadlineComparison } from '../../api/simulation'
import { chartFigure } from '../../components/charts/chartTheme'
import { fmtCurrency, fmtEnergy } from '../../utils/format'
import { HEADLINES } from './investment/financeModel'

/** The Curtailment tab's own tile label. */
export const CURTAILMENT_RATE = 'Curtailment rate'
/** The Summary's cost words, shared with its Cost card. */
export const COST_WORDS = {
  total: 'Total system cost',
  capital: 'Capital cost per year',
  newBuild: 'New-build capital cost',
} as const
export const UNSERVED_ENERGY = 'Unserved energy'

interface Words {
  label: string
  /** The Results tab that owns the figure, and its name for the "›" link. */
  tab: string
  tabLabel: string
}

export const HEADLINE_WORDS: Record<string, Words> = {
  system_cost: { label: COST_WORDS.total, tab: 'capex', tabLabel: 'Capacity expansion' },
  new_build_capital: { label: COST_WORDS.newBuild, tab: 'capex', tabLabel: 'Capacity expansion' },
  curtailed_share: { label: CURTAILMENT_RATE, tab: 'curtailment', tabLabel: 'Curtailment' },
  unserved_energy: { label: UNSERVED_ENERGY, tab: 'lostload', tabLabel: 'Lost load' },
  lcoe: {
    label: HEADLINES.find(h => h.id === 'lcoe_real')?.label ?? 'LCOE (real)',
    tab: 'investment', tabLabel: 'Investment case',
  },
  peak_line_loading: { label: 'Peak line loading', tab: 'loadflow', tabLabel: 'Load flow' },
  // The data-centre fact (DC 27): the strip is its only Results home, so no tab link.
  time_to_power: { label: 'Time to power', tab: '', tabLabel: '' },
}

/** The card's figure with its unit, and the line under it saying what it covers. */
export function headlineFigure(card: HeadlineCard): { value: string; basis?: string } {
  const v = card.value
  if (v == null) return { value: '' }
  switch (card.unit) {
    case 'currency/yr': return { value: fmtCurrency(v), basis: 'per year' }
    // Annualised fixed cost plus the opex of the solved snapshots, or a sum over
    // the periods: what the solve covered, which is a year only by coincidence.
    case 'currency': return { value: fmtCurrency(v), basis: 'in this solve' }
    case 'MWh': return { value: fmtEnergy(v), basis: 'in this solve' }
    case 'months': return { value: `${v} ${v === 1 ? 'month' : 'months'}`, basis: card.detail ?? 'from project start' }
    case '%': return { value: `${v.toFixed(1)} %`, basis: card.detail ?? undefined }
    default: {
      const unit = card.unit === 'EUR/MWh' ? '€/MWh' : card.unit ?? ''
      return { value: `${v.toFixed(2)} ${unit}`.trim(), basis: card.id === 'lcoe' ? 'investment case' : undefined }
    }
  }
}

/** A figure in the card's unit, rounded as its tile rounds it. */
const inTileWords = (card: HeadlineCard, v: number) => chartFigure(headlineFigure({ ...card, value: v }).value)

export const BASE_CASE = 'Base case'

/**
 * The card's change against the base case, sign first: "−4.2 % vs Base case".
 * A share reads in percentage points, and a zero base, which has no
 * percentage, in the card's own unit. Where the base has no figure to
 * subtract, the server's sentence says so.
 */
export function headlineDelta(card: HeadlineCard): string | undefined {
  const d = card.delta
  if (!d || card.value == null) return undefined
  if (d.value == null) return d.note ?? undefined
  const sign = d.value > 0 ? '+' : '−'
  // A change that rounds to nothing (solver noise) has no sign to show.
  const shown = card.unit === '%' ? Math.abs(d.value).toFixed(1) : d.pct != null ? Math.abs(d.pct).toFixed(1) : null
  if (d.value === 0 || shown === '0.0') return `No change vs ${BASE_CASE}`
  if (card.unit === '%') return `${sign}${shown} percentage points vs ${BASE_CASE}`
  if (shown != null) return `${sign}${shown} % vs ${BASE_CASE}`
  return `${sign}${inTileWords(card, Math.abs(d.value))} vs ${BASE_CASE}`
}

/** What the source line adds: which project is the base case. */
export function comparisonBasis(c: HeadlineComparison | null | undefined): string | undefined {
  if (!c) return undefined
  if (c.role === 'base') return BASE_CASE
  return c.base_name ? `${BASE_CASE}: ${c.base_name}` : undefined
}

/** The card's trend over the investment periods: its ends in words, and the points. */
export function headlineTrend(card: HeadlineCard): { first: string; last: string; label: string; basis: string; data: number[] } | null {
  const t = card.trend?.points
  if (!t || t.length < 2 || card.value == null) return null
  const a = t[0], b = t[t.length - 1]
  const from = inTileWords(card, a.value), to = inTileWords(card, b.value)
  return {
    first: `${a.period} · ${from}`,
    last: `${b.period} · ${to}`,
    label: `${a.period} to ${b.period}: ${from} to ${to}`,
    basis: card.trend!.basis,
    data: t.map(p => p.value),
  }
}
