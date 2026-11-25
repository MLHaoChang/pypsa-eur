// Front records for the unit and parity tests: the engine's point shape
// (services/adequacy/front.py), a reserve-margin lever, three levels × one
// uncapped and one capped CO2 column. Generic toy values only.
import type { FrontPoint, FrontRecord, FrontResult } from '../../../api/front'

export function point(i: number, o: Partial<{
  level: number; cap: number | null; cost: number; eue: number; lole: number; co2: number
  onFront: boolean; by: number[]; status: string
}> = {}): FrontPoint {
  const cost = o.cost ?? 1_000_000 + i * 50_000
  const eue = o.eue ?? 4 - i
  const lole = o.lole ?? 3 - i * 0.5
  const status = o.status ?? 'ok'
  return {
    index: i, lever: 'reserve_margin', level: o.level ?? 0.1 + 0.05 * i,
    co2_cap_t_per_yr: o.cap ?? null, status,
    point: status !== 'ok' ? null : {
      capex_eur_per_yr: cost * 0.7, opex_eur_per_yr: cost * 0.3, total_eur_per_yr: cost,
      lp_ens_mwh: 0, reliability_binding: true,
      co2_t_per_yr: o.co2 ?? 2000 - i * 100, co2_binding: o.cap != null,
      capacities: { 'generators:gas_turbine': 10 + i, 'storage_units:bess': 4 + i, 'stores:h2_tank': 20 },
      capacity_units: { 'generators:gas_turbine': 'MW', 'storage_units:bess': 'MW', 'stores:h2_tank': 'MWh' },
    },
    mc: status !== 'ok' ? null : {
      lole_hours: lole, lole_ci: [lole * 0.8, lole * 1.2], eue_mwh: eue, eue_ci: [eue * 0.75, eue * 1.25],
      n_samples: 500, converged: true, below_floor: false, time_basis: 'hours_per_year',
    },
    dominated_by: o.by ?? [], on_front: status !== 'ok' ? null : (o.onFront ?? true),
    reliability_indistinct: false,
  }
}

export function result(points: FrontPoint[], o: Partial<FrontResult> = {}): FrontResult {
  return {
    lever: 'reserve_margin', levels: [0.1, 0.15, 0.2], co2_caps_t_per_yr: [null, 1500],
    points, currency: 'EUR', reliability_basis: 'mc_eue', reliability_basis_note: null,
    degenerate: { axes: {}, all: false, text: null }, warning: null, aborted: false, ...o,
  }
}

export const NORMAL_POINTS: FrontPoint[] = [
  point(0),
  point(1),
  point(2, { cost: 1_200_000, eue: 1.5, co2: 1500, cap: 1500 }),
  point(3, { cost: 1_250_000, eue: 3.5, co2: 1900, onFront: false, by: [1] }),
  point(4, { status: 'infeasible' }),
]

export function record(o: Partial<FrontRecord> = {}, res: FrontResult = result(NORMAL_POINTS)): FrontRecord {
  return { status: 'done', result: res, error: null, edited_since_study: false, source: 'front', applicable: true, ...o }
}

export const DEGENERATE_TEXT =
  'Every one of the 3 points is the same design (same capacities, same cost): there is no trade-off in this range, so the front is one point.'

export function degenerateRecord(o: Partial<FrontRecord> = {}): FrontRecord {
  const same = [0, 1, 2].map(i => point(i, { cost: 1_000_000, eue: 2, co2: 1800, lole: 1 }))
  for (const p of same) p.point!.capacities = { 'generators:gas_turbine': 10, 'storage_units:bess': 4, 'stores:h2_tank': 20 }
  return record(o, result(same, { degenerate: { axes: {}, all: true, text: DEGENERATE_TEXT } }))
}
