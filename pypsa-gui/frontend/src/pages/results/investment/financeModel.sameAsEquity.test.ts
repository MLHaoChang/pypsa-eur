// Template-e2e issue 22, S2b (UX 2, F4 on #1761): with no counterfactual the
// lifecycle-cost NPV is the equity NPV, and the row says so in plain words.
import { describe, expect, it } from 'vitest'
import type { InvestmentCaseReportPayload } from '../../../api/types'
import { hasNoCounterfactual, headlines, SAME_AS_EQUITY_NPV } from './financeModel'

const report = (payload: Record<string, unknown>) =>
  ({ sections: { project: { status: 'ok', payload } } }) as unknown as InvestmentCaseReportPayload
const lifecycle = (r: InvestmentCaseReportPayload) => headlines(r).find(h => h.id === 'lifecycle_npv')!

describe('the lifecycle-cost NPV note', () => {
  it('no counterfactual and the same figure: the row says it is the equity NPV', () => {
    const h = lifecycle(report({ has_counterfactual: false, lifecycle_npv: 1_250_000, equity_post_tax_npv: 1_250_000 }))
    expect(h.value).toBe(1_250_000)
    expect(h.aside).toBe(SAME_AS_EQUITY_NPV)
    expect(SAME_AS_EQUITY_NPV).not.toMatch(/=|cf_net|counterfactual_/)
  })

  it('a counterfactual that is not present counts as none', () => {
    const r = report({ counterfactual: { present: false }, lifecycle_npv: 5, equity_post_tax_npv: 5 })
    expect(hasNoCounterfactual(r)).toBe(true)
    expect(lifecycle(r).aside).toBe(SAME_AS_EQUITY_NPV)
  })

  it('a counterfactual, different figures or a missing figure: no note', () => {
    expect(lifecycle(report({ has_counterfactual: true, lifecycle_npv: 5, equity_post_tax_npv: 5 })).aside)
      .toBeUndefined()
    expect(lifecycle(report({ has_counterfactual: false, lifecycle_npv: 5, equity_post_tax_npv: 9 })).aside)
      .toBeUndefined()
    expect(lifecycle(report({ has_counterfactual: false, lifecycle_npv: 5 })).aside).toBeUndefined()
  })
})
