// The words and formats of the Results headline strip (ui-modern 03). The
// backend decides which cards a project type gets and reads their figures
// (`GET /results/headline_kpis`); this file only names and formats them.
// A label is the word its own tab already uses (parity: headlineWords.test.ts).
import type { HeadlineCard } from '../../api/simulation'
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
    case '%': return { value: `${v.toFixed(1)} %`, basis: card.detail ?? undefined }
    default: {
      const unit = card.unit === 'EUR/MWh' ? '€/MWh' : card.unit ?? ''
      return { value: `${v.toFixed(2)} ${unit}`.trim(), basis: card.id === 'lcoe' ? 'investment case' : undefined }
    }
  }
}
