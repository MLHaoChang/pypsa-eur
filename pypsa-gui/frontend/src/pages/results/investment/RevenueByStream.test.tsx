// Revenue by stream (IPP ticket 05): the six stream columns with the
// backend's labels, the total and the replacements; a dash for a null; the
// "other" note; one line when the report does not establish the streams; the
// link to the curtailment tab.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useUIStore } from '../../../store/uiStore'
import type { InvestmentCaseReportPayload, RevenueStreams } from '../../../api/types'
import RevenueByStream from './RevenueByStream'

const STREAMS: RevenueStreams = {
  years: [2030, 2031],
  rows: [
    { id: 'ppa', label: 'PPA', values: [100, 110] },
    { id: 'merchant', label: 'Merchant', values: [50, null] },
    { id: 'cfd', label: 'CfD', values: [0, 0] },
    { id: 'grid_imports', label: 'Grid imports', values: [-20, -21] },
    { id: 'om', label: 'O&M', values: [-10, -10] },
    { id: 'other', label: 'Other', values: [5, 6] },
  ],
  total: { id: 'total', label: 'Operating cash', values: [125, null] },
  replacements: { id: 'replacements', label: 'Replacements', values: [0, -300] },
  other_keys: ['capacity_payment', 'ancillary'],
}

const report = (streams: RevenueStreams | null, note?: string): InvestmentCaseReportPayload => ({
  sections: { project: { status: 'ok', note, payload: {
    currency: 'EUR', basis_statement: 'costs in real 2025 EUR (2024 price data)', revenue_streams: streams } } },
})

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('RevenueByStream', () => {
  it('renders the streams, the total and the replacements, a dash for a null', () => {
    render(<RevenueByStream report={report(STREAMS)} />)
    const table = screen.getByTestId('ic-revenue-streams')
    const heads = within(table).getAllByRole('columnheader').map(h => h.textContent)
    expect(heads).toEqual(['Year', 'PPA', 'Merchant', 'CfD', 'Grid imports', 'O&M', 'Other',
      'Operating cash', 'Replacements'])
    expect(screen.getByTestId('ic-revenue-col-total').className).toMatch(/font-semibold/)
    expect(screen.getByTestId('ic-revenue-col-replacements').className).toMatch(/border-l/)
    const y31 = within(screen.getByTestId('ic-revenue-2031')).getAllByRole('cell').map(c => c.textContent)
    expect(y31[1]).toBe('—')
    expect(y31[3]).toBe((-21).toLocaleString(undefined, { minimumFractionDigits: 2 }))
    expect(y31[6]).toBe('—')
    expect(y31[7]).toBe((-300).toLocaleString(undefined, { minimumFractionDigits: 2 }))
    expect(screen.getByTestId('ic-revenue-units').textContent)
      .toBe('EUR per year; costs in real 2025 EUR (2024 price data)')
    expect(screen.getByTestId('ic-revenue-other-keys').textContent)
      .toBe('Other includes: capacity_payment, ancillary')
  })

  it('no other keys: no note', () => {
    render(<RevenueByStream report={report({ ...STREAMS, other_keys: [] })} />)
    expect(screen.queryByTestId('ic-revenue-other-keys')).toBeNull()
  })

  it('not established: one line with the project section note', () => {
    render(<RevenueByStream report={report(null, 'the dispatch is missing')} />)
    expect(screen.queryByTestId('ic-revenue-streams')).toBeNull()
    expect(screen.getByTestId('ic-revenue-unavailable').textContent)
      .toContain('Revenue by stream is not established: the dispatch is missing')
  })

  it('the link asks for the curtailment tab', () => {
    const spy = vi.fn()
    const real = useUIStore.getState().requestResultsTab
    useUIStore.setState({ requestResultsTab: spy })
    try {
      render(<RevenueByStream report={report(STREAMS)} />)
      fireEvent.click(screen.getByRole('button', { name: 'Curtailment by cause → Operation › Curtailment' }))
      expect(spy).toHaveBeenCalledWith('curtailment')
    } finally { useUIStore.setState({ requestResultsTab: real }) }
  })
})
