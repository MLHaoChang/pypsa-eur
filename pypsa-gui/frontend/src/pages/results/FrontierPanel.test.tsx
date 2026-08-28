import { describe, expect, it } from 'vitest'
import { kneeMessage, type FrontierRow } from './FrontierPanel'

const row = (target: number, ens: number, cost: number): FrontierRow => ({
  target_permyriad: target, status: 'ok',
  point: {
    cap_mwh: ens, achieved_ens_mwh: ens, achieved_shed_hours: 24,
    total_system_cost_eur: cost, engine: 'lp_proxy',
    fidelity: 'deterministic_scenario',
  },
})

describe('kneeMessage', () => {
  // Steps: 30->20 MWh costs 100 (10 EUR/MWh avoided); 20->10 costs 900 (90).
  const rows = [row(300, 30, 1000), row(150, 20, 1100), row(80, 10, 2000)]

  it('names the target where tightening stops paying for itself', () => {
    const m = kneeMessage(1, rows, 50)
    expect(m).toMatch(/Economic knee at 150‱/)
    expect(m).toMatch(/90/)          // EUR/MWh avoided on that step
  })

  it('distinguishes "already past the optimum" from a knee in the middle', () => {
    // knee at index 0 means even the LOOSEST target swept is uneconomic —
    // reading that as "tighten to here" would be exactly backwards.
    const m = kneeMessage(0, rows, 5)
    expect(m).toMatch(/already past the economic optimum/i)
    expect(m).toMatch(/sweep looser targets/i)
    expect(m).not.toMatch(/Economic knee at/)
  })

  it('says the knee is outside the range rather than inventing one', () => {
    const m = kneeMessage(null, rows, 5000)
    expect(m).toMatch(/No economic knee inside the swept range/i)
    expect(m).toMatch(/sweep tighter/i)
  })

  it('stays silent when there is not enough of a curve to read', () => {
    expect(kneeMessage(null, [row(300, 30, 1000)], 50)).toBeNull()
    expect(kneeMessage(null, [], 50)).toBeNull()
  })

  it('ignores unreachable points when counting usable ones', () => {
    const withFailure: FrontierRow[] = [
      row(300, 30, 1000),
      { target_permyriad: 1, status: 'infeasible', point: null },
    ]
    // only one usable point -> nothing to say
    expect(kneeMessage(null, withFailure, 50)).toBeNull()
  })
})
