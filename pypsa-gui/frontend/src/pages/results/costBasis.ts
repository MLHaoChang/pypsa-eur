// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

// Template-e2e 18: every cost figure says what it is. Three figures name a run's cost:
//  - the LP objective (solve message, status bar): what the optimiser minimised. On a multi-period solve
//    with the auto period discount it is discounted to the first period, and it carries no fixed cost of
//    assets it could not size (PyPSA counts those only in a single-period solve's constant);
//  - the total system cost (headline strip, Summary): every asset's annualised fixed cost plus operating
//    cost, summed over the periods' years, not discounted;
//  - under myopic foresight and rolling horizon the run's figure is that total (each period or window is
//    its own LP, so no single objective prices the horizon).
// The basis comes from the solve's own record (`/simulation/status`, `/results/objective_decomposition`);
// nothing here derives a rate or a weight.
import type { ObjectiveDecomposition } from '../../api/results'
import type { FlowSection } from '../../api/flow'
import { money } from '../../utils/money'
// te 20's one-decimal figure, so the note and the grid-connection row agree to the digit (te 18 / te 20).
import { fmt1 } from './notCounted'
import { signFirstEuro } from '../../utils/format'
import type { SimulationStatus } from '../../api/types'

/** The run's figure in the status bar's notation (`money()`); signed, the minus before the symbol
 *  ("-€ 16.3 m", ruling (ad), as `unitPrice`), and none on a figure that rounds to zero. null when there is
 *  none. The solve toast shows the same figure, so it reads it here too. */
export function objectiveFigure(v: number | null | undefined): string | null {
  if (v == null || !Number.isFinite(v)) return null
  const unsigned = money(v, 'EUR')
  return v < 0 && /[1-9]/.test(unsigned) ? `-${unsigned}` : unsigned
}

type Basis = Pick<SimulationStatus, 'objective_basis' | 'objective_discounted_to'>

/** The name of the run's figure: "LP objective"; "System cost" where the run reports the total; "Objective"
 *  for a result solved before its basis was recorded. Expert words: Guided never shows them. */
export function objectiveName(status: Basis | null | undefined): string {
  if (status?.objective_basis === 'system_cost') return 'System cost'
  if (status?.objective_basis === 'unknown') return 'Objective'
  return 'LP objective'
}

/** What the run's figure is, in a few words ("discounted to 2027", "not discounted"). */
export function objectiveBasis(status: Basis | null | undefined): string {
  if (status?.objective_basis === 'system_cost') return 'all assets, each period its own LP, not discounted'
  if (status?.objective_basis === 'unknown') return 'its basis was not recorded; run the solve again to see it'
  if (status?.objective_discounted_to != null) return `discounted to ${status.objective_discounted_to}`
  return 'not discounted'
}

/** A euro formatter fixed to the scale `fmtCurrency` gives `ref` (B, M, k), so the parts of one line add up
 *  on sight (UX 34 on #2140): "€8,631.20 M … €212.06 M off … €419.52 M". Grouped, two decimals. */
export function currencyInScaleOf(ref: number | null | undefined): (eur: number | null | undefined) => string {
  const a = ref != null && Number.isFinite(ref) ? Math.abs(ref) : 0
  const [div, unit] = a >= 1e9 ? [1e9, ' B'] : a >= 1e6 ? [1e6, ' M'] : a >= 1e3 ? [1e3, ' k'] : [1, '']
  return eur => {
    if (eur == null || !Number.isFinite(eur)) return '—'
    const body = (Math.abs(eur) / div).toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    return signFirstEuro(eur, `${body}${unit}`)
  }
}

/** Guided's name for the run's figure, and its basis line (UX 34 on #2140): Guided's own cost words, never
 *  "solve", "LP" or "objective". A discounted multi-period figure is the plan's cost (its weights are present
 *  value × years); a single period's is a year's. */
export function guidedCostWords(status: Basis | null | undefined, multiPeriod: boolean): { label: string; basis?: string } {
  // A run recorded before its basis was: no basis is claimed (GK B3 on #2140).
  if (status?.objective_basis === 'unknown') {
    return { label: multiPeriod ? 'Cost over the periods' : 'Yearly cost',
      basis: 'how it was counted was not recorded: run the optimisation again to see it' }
  }
  if (status?.objective_discounted_to != null) {
    return { label: 'Cost over the plan (discounted)', basis: `discounted to ${status.objective_discounted_to}` }
  }
  // Not discounted, the periods count with whatever weights the network carries (one year each unless set),
  // so the figure is not claimed to be the plan's cost (GK B3 on #2140).
  if (multiPeriod) return { label: 'Cost over the periods', basis: 'summed over the periods, not discounted' }
  return { label: 'Yearly cost' }
}

/** The total system cost's basis line: what the solve covered, and on a multi-period solve that it is a
 *  sum not discounted (the LP objective is). */
export function totalCostBasis(multiPeriod: boolean): string {
  return multiPeriod ? 'in this solve, summed over the periods, not discounted' : 'in this solve'
}

/** A difference below this share of the total is not a difference a reader acts on. */
const SAME = 0.005

const big = (v: number | null | undefined, total: number): v is number =>
  v != null && Number.isFinite(v) && Math.abs(v) > SAME * Math.max(Math.abs(total), 1)

/** One reason the LP objective is not the total system cost: its words, and the amount it moves the
 *  total by on the way to the objective (signed). */
export interface GapReason { words: string; amount: number }

/**
 * Why the LP objective is not the total system cost, one plain clause per reason saying which way it
 * moves the figure: the discounting, the existing assets' fixed cost, reserve revenue, and anything else
 * the LP counts. `total + Σ amount ≈ lp_total` (the decomposition's identity, up to the reasons too small
 * to name). Empty when they agree, or when the decomposition cannot say (myopic, rolling, missing
 * figures). `fmt` is the section's notation.
 */
export function objectiveGapReasons(
  dec: ObjectiveDecomposition | null | undefined, fmt: (v: number) => string,
): GapReason[] {
  const total = dec?.cost_breakdown_total
  const lp = dec?.lp_total
  if (dec == null || total == null || lp == null || (dec.lp_total_scope ?? 'horizon') !== 'horizon') return []
  if (!big(lp - total, total)) return []
  const out: GapReason[] = []
  const pct = (r: number) => `${(r * 100).toFixed(1)} %`
  const moves = (v: number) => (v < 0 ? `takes ${fmt(-v)} off` : `adds ${fmt(v)}`)
  const w = dec.period_weighting_adjustment_eur
  if (big(w, total)) {
    const disc = dec.discount
    out.push({
      words: disc != null && disc !== 'unknown'
        ? `discounting to ${disc.ref_year} at ${pct(disc.real_rate)} real ${moves(w)}`
        : `weighting the periods by the objective's weights, not their years, ${moves(w)}`,
      amount: w,
    })
  }
  const nonext = dec.nonextendable_fixed_cost_eur
  if (big(nonext, total)) out.push({ words: `existing assets' fixed cost (${fmt(nonext)}) is not in it`, amount: -nonext })
  const reserve = dec.reserve_revenue_eur
  if (big(reserve, total)) out.push({ words: `reserve revenue ${moves(-reserve)}`, amount: -reserve })
  const rest = dec.residual_gap_eur
  if (big(rest, total)) {
    out.push({ words: `other terms the optimiser counts ${rest < 0 ? `take ${fmt(-rest)} off` : `add ${fmt(rest)}`}`, amount: rest })
  }
  return out
}

/** The existing assets' fixed cost a single-period LP objective carries as its constant: the part of
 *  Expert's figure Guided's yearly cost leaves out. Only the server's existing-asset part, never the whole
 *  constant, which a curtailment cost also adds to (GK B3 on #2140). Null when there is none to name. */
export function objectiveConstant(dec: ObjectiveDecomposition | null | undefined): number | null {
  const c = dec?.existing_asset_constant
  const total = dec?.cost_breakdown_total
  return total != null && big(c, total) ? c : null
}

type ConnectionSection = Pick<FlowSection, 'state' | 'connection_limit'>

/**
 * Key capacities' line for the grid supply it does not list (template-e2e 18), read from the flow's connection
 * section: the limit is the server's one reading (`flow_service.import_capacity`, the figure the study and
 * te 20's grid-connection line take), never re-summed here. An off-grid template's connection is
 * `not_needed`: it imports nothing. null until the flow is read.
 */
export function gridSupplyWords(conn: ConnectionSection | null | undefined): string | null {
  if (conn == null) return null
  if (conn.state === 'not_needed') return 'Grid supply: none, the site runs off the grid.'
  const limit = conn.connection_limit
  if (limit?.mw != null) return `Grid supply: up to ${fmt1(limit.mw, 'MW')}, through the site's connection.`
  return `Grid supply is not listed: ${limit?.refusal ?? "what the site can import is its connection's rating."}`
}
