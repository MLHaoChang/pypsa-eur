// Curtailment by cause (IPP ticket 05): one row per plant with the four
// causes and the total, in MWh and as a share; the rule line; one sentence per
// not-established reason with the totals still shown; the return link.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useUIStore } from '../../store/uiStore'
import type { CurtailmentByCausePayload } from '../../api/simulation'
import { CAUSE_RULE, CurtailmentByCauseTable, NOT_ESTABLISHED_REASON } from './CurtailmentByCause'

const OK: CurtailmentByCausePayload = {
  status: 'ok', export_link: 'export', export_capacity_mw: 50, negative_price_hours: 12,
  plants: { Solar: { total_mwh: 200, available_mwh: 1000,
    by_cause_mwh: { export_limit: 100, negative_price: 50, both: 30, other: 20 } } },
}

afterEach(() => cleanup())

describe('CurtailmentByCauseTable', () => {
  it('renders the causes, the total and their shares, and the rule', () => {
    render(<CurtailmentByCauseTable payload={OK} />)
    const row = screen.getByTestId('curtailment-cause-Solar')
    const cells = within(row).getAllByRole('cell').map(c => c.textContent)
    expect(cells).toEqual(['Solar', '100.050.0 %', '50.025.0 %', '30.015.0 %', '20.010.0 %', '200.020.0 %'])
    expect(screen.getByTestId('curtailment-cause-rule').textContent).toBe(CAUSE_RULE)
    expect(CAUSE_RULE).toBe('An hour counts as “both” when the export limit binds and the price is negative; '
      + '“other” is curtailment when neither applies.')
  })

  it.each(Object.entries({
    export_link_missing: 'No export link is set in the commercial settings, so curtailment is not split by cause.',
    export_price_missing: 'No export price is set, so curtailment is not split by cause.',
    no_commercial_solve_record: 'The last run did not price the export, so curtailment is not split by cause.',
    export_price_changed_since_solve: 'The export price changed since the last run; run again to split curtailment by cause.',
  }))('not established (%s): the totals and one sentence', (reason, sentence) => {
    expect(NOT_ESTABLISHED_REASON[reason]).toBe(sentence)
    render(<CurtailmentByCauseTable payload={{ status: 'not_established', reason,
      plants: { Solar: { total_mwh: 200, available_mwh: 1000 } } }} />)
    expect(screen.getByTestId('curtailment-by-cause-reason').textContent).toBe(sentence)
    const cells = within(screen.getByTestId('curtailment-cause-Solar')).getAllByRole('cell').map(c => c.textContent)
    expect(cells).toEqual(['Solar', '200.020.0 %'])
    expect(screen.queryByTestId('curtailment-cause-rule')).toBeNull()
  })

  it('the return link asks for the investment tab', () => {
    const spy = vi.fn()
    const real = useUIStore.getState().requestResultsTab
    useUIStore.setState({ requestResultsTab: spy })
    try {
      render(<CurtailmentByCauseTable payload={OK} />)
      fireEvent.click(screen.getByRole('button', { name: '← Revenue by stream' }))
      expect(spy).toHaveBeenCalledWith('investment')
    } finally { useUIStore.setState({ requestResultsTab: real }) }
  })
})
