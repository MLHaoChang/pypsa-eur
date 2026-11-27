// AA 28, UX (32): the chat's checked investment-case/2 answer prints each figure under the
// Investment tab's own label. The contract snapshot is the server's one source of those labels.
import { describe, expect, it } from 'vitest'
import { HEADLINES } from './financeModel'
import snapshot from '../../../../../backend/harness/assurance_contracts.v1.json'

const tab = (id: string) => HEADLINES.find(h => h.id === id)!.label
const contract = (metric: string) =>
  (snapshot.profiles['investment-case/2'].definitions as Record<string, { label: string }>)[metric].label

describe('investment-case/2 contract labels equal the Investment tab headlines', () => {
  it.each([
    ['project_irr', 'project_irr_post_tax'],
    ['equity_irr', 'equity_irr_post_tax'],
    ['equity_npv', 'equity_npv_post_tax'],
    ['project_npv', 'npv_at_wacc'],
  ])('%s reads as the tab\'s %s', (metric, headline) => {
    expect(contract(metric)).toBe(tab(headline))
  })

  it('covers every metric of the profile', () => {
    expect([...snapshot.profiles['investment-case/2'].metrics].sort())
      .toEqual(['equity_irr', 'equity_npv', 'project_irr', 'project_npv'])
  })
})
