// The words and formats of the Results headline strip (ui-modern 03). The
// backend decides which cards a project type gets and reads their figures
// (`GET /results/headline_kpis`); this file only names and formats them.
// A label is the word its own tab already uses (parity: headlineWords.test.ts).
import type { HeadlineCard, HeadlineComparison } from '../../api/simulation'
import { chartFigure } from '../../components/charts/chartTheme'
import { fmtCo2Tonnes, fmtCurrency, fmtEnergy } from '../../utils/format'
import { money } from '../../utils/money'
import { figureName } from '../../utils/figureWords'

/** The Curtailment tab's own tile label. */
export const CURTAILMENT_RATE = 'Curtailment rate'
/** The Summary's cost words, shared with its Cost card. */
export const COST_WORDS = {
  total: 'Total system cost',
  capital: 'Capital cost per year',
  newBuild: 'New-build capital cost',
} as const
export const UNSERVED_ENERGY = 'Unserved energy'
/** The CO₂ limit goal's card (DC review 02): the tile and Guided's figure. */
export const CO2_EMISSIONS = 'CO₂ emissions'
export const CO2_UNIT = 't CO₂/yr'

/** Template-e2e 14 (GK B2 (3) e, UX 2 (4)): a negative total system cost is revenue over cost.
 *  It keeps its cost label, says so beside the figure, and is never called a profit: the LP
 *  objective is not the business case. */
export const REVENUE_EXCEEDS_COST = '(revenue exceeds cost)'

/** A total system cost in words: the figure with its sign before the €, and the note a
 *  negative total carries (none otherwise). One helper for the strip, Summary and Capacity
 *  expansion. The minus is the one the KPI tiles write (`kpiFigure`), so a view never mixes
 *  two (UX 2 (5) on #1783). */
export function totalCostWords(total: number | null | undefined): { value: string; note?: string } {
  if (total == null || !Number.isFinite(total) || total >= 0) return { value: fmtCurrency(total) }
  return { value: `-${fmtCurrency(-total)}`, note: REVENUE_EXCEEDS_COST }
}

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
  co2_annual: { label: CO2_EMISSIONS, tab: 'emissions', tabLabel: 'Emissions' },
  lcoe: {
    label: figureName('lcoe_real'),
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
    case 'currency': {
      if (card.id !== 'system_cost') return { value: fmtCurrency(v), basis: 'in this solve' }
      const words = totalCostWords(v)
      return { value: words.value, basis: words.note ? `in this solve ${words.note}` : 'in this solve' }
    }
    case 'MWh': return { value: fmtEnergy(v), basis: 'in this solve' }
    case 'tCO2/yr': return { value: `${co2Number(v)} ${CO2_UNIT}`, basis: co2LimitWords(card) }
    case 'months': return { value: `${v} ${v === 1 ? 'month' : 'months'}`, basis: card.detail ?? 'from project start' }
    case '%': return { value: `${v.toFixed(1)} %`, basis: card.detail ?? undefined }
    default: {
      const unit = card.unit === 'EUR/MWh' ? '€/MWh' : card.unit ?? ''
      return { value: `${v.toFixed(2)} ${unit}`.trim(), basis: card.id === 'lcoe' ? card.basis ?? 'investment case' : undefined }
    }
  }
}

const co2Number = fmtCo2Tonnes

/** The line under the CO₂ figure: "Limit 1,000 t CO₂/yr · reached · cost of the
 *  limit € 85 /t", or "· not reached"; a multi-period run says which period the
 *  figure is (UX GO on DC 02 (3)). */
export function co2LimitWords(card: HeadlineCard): string | undefined {
  const limit = card.limit_t_per_yr
  if (limit == null) return undefined
  const parts = [`Limit ${co2Number(limit)} ${CO2_UNIT}`]
  if (card.reached) {
    parts.push('reached')
    if (card.shadow_price_eur_per_t != null) parts.push(`cost of the limit ${money(card.shadow_price_eur_per_t, 'EUR')}/t`)
  } else parts.push('not reached')
  if (card.period != null) parts.push(`per year, highest period ${card.period}`)
  return parts.join(' · ')
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
