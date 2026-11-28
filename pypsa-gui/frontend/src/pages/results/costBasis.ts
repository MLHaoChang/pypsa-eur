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
import type { Generator, Link, SimulationStatus } from '../../api/types'

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
 *  Expert's figure Guided's yearly cost leaves out. Null when there is none to name. */
export function objectiveConstant(dec: ObjectiveDecomposition | null | undefined): number | null {
  const c = dec?.n_objective_constant
  const total = dec?.cost_breakdown_total
  return total != null && big(c, total) ? c : null
}

type SupplyGen = Pick<Generator, 'bus' | 'carrier'>
type SupplyLink = Pick<Link, 'bus0' | 'p_nom' | 'p_max_pu'> & { p_nom_opt?: number; eh_role?: string | null }

/**
 * The grid supply's limit, which Key capacities names instead of the supply generator's placeholder p_nom
 * (template-e2e 18): the summed rating of the Links that carry power from a grid-supply bus into the site
 * (or are tagged `grid_import`), each at its static availability. `{ mw }`, 0 when every such Link is
 * switched off (the island microgrid's open tie); null when no such Link is found.
 */
export function gridSupplyLimit(
  generators: SupplyGen[] | null | undefined, links: SupplyLink[] | null | undefined,
): { mw: number } | null {
  const buses = new Set((generators ?? []).filter(g => /^grid$/i.test(String(g.carrier ?? '').trim())).map(g => g.bus))
  const into = (links ?? []).filter(l => buses.has(l.bus0) || l.eh_role === 'grid_import')
  if (!into.length) return null
  const finite = (v: unknown, fallback: number) => (typeof v === 'number' && Number.isFinite(v) ? v : fallback)
  const mw = into.reduce((sum, l) => sum + Math.max(0, finite(l.p_nom_opt, finite(l.p_nom, 0)) * finite(l.p_max_pu, 1)), 0)
  return { mw }
}

/** Key capacities' line for the grid supply it does not list. */
export function gridSupplyWords(limit: { mw: number } | null, fmtMw: (mw: number) => string): string {
  if (limit == null) return "Grid supply is not listed: what the site can import is its connection's rating."
  if (limit.mw <= 0) return 'Grid supply: none, the tie to the grid is switched off.'
  return `Grid supply: up to ${fmtMw(limit.mw)}, through the site's connection.`
}
