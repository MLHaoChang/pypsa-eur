import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi, type HeadlineCard } from '../../../api/simulation'
import type { InvestmentCaseReportPayload } from '../../../api/types'
import { Headlines } from './InvestmentCaseView'

/** te-24 (LCOE-CAVEAT-1): Investment's "LCOE (real)" row carries the headline card's caveat in
 *  the headlines grid, under that row, when the card holds the same figure (UX rule 46). */
const CAVEAT = 'Above € 300/MWh, high for a hospital: the backup kept for the critical load is costed but rarely runs.'
const card = (value: number): HeadlineCard => ({
  id: 'lcoe', value, unit: 'EUR/MWh', state: 'ready', reason: null, formula_id: null,
  detail: null, delta: null, trend: null, caveat: CAVEAT,
})
const CAVEAT_REPORT = {
  case_id: 'investment_case', assumptions_hash: 'abc', packs: {}, completeness: {},
  sections: { project: { status: 'ok', payload: { years: [2030], lcoe_real_per_mwh: 413.2 } } },
  cashflow_lines: [],
} as unknown as InvestmentCaseReportPayload

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><Headlines report={CAVEAT_REPORT} /></QueryClientProvider>)
}
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Investment LCOE (real) caveat', () => {
  it('sits under the LCOE (real) row when the headline card holds the same figure', async () => {
    vi.spyOn(resultsApi, 'getHeadlineKpis').mockResolvedValue({ kind: 'energy_hub', cards: [card(413.2)] })
    mount()
    const line = await screen.findByTestId('ic-headline-caveat-lcoe_real')
    expect(line.textContent).toBe(CAVEAT)
    expect(line.previousElementSibling?.getAttribute('data-testid')).toBe('ic-headline-lcoe_real')
  })
  it('stays off when the card holds another figure', async () => {
    const spy = vi.spyOn(resultsApi, 'getHeadlineKpis').mockResolvedValue({ kind: 'energy_hub', cards: [card(500)] })
    mount()
    await screen.findByTestId('ic-headline-lcoe_real')
    await vi.waitFor(() => expect(spy).toHaveBeenCalled())
    expect(screen.queryByTestId('ic-headline-caveat-lcoe_real')).toBeNull()
  })
})
