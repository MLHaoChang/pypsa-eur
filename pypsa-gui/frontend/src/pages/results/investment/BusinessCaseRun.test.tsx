// Business case 10: the run, KPI row and P&L. Totals are `pnl.rows`, never a sum of
// streams (GK B F1); missing figures read their reason, never 0 (F4, UX 6); one
// out-of-date mark (UX 7); the status region holds only stage words (UX 8).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  businessCaseApi, InvestmentCaseMissing,
  type BusinessCasePnl, type BusinessCaseReport, type BusinessCaseStudy,
} from '../../../api/businessCase'
import { useUIStore } from '../../../store/uiStore'
import BusinessCaseRun, { type BusinessCaseRunProps } from './BusinessCaseRun'
import { fmtYear, kpiText, PNL_SECTIONS, reasonWords, ROW_NEEDS, streamGroups } from './businessCaseReport'
import PNL_ROWS from './businessCasePnlRows.json'
import { expectAllButtonsNamed } from '../../../test-utils/accessibleName'

vi.mock('../../../api/businessCase', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/businessCase')>()
  return { ...actual, businessCaseApi: { startRun: vi.fn(), abortRun: vi.fn() } }
})
const api = vi.mocked(businessCaseApi)

const PNL: BusinessCasePnl = {
  years: [2030, 2031],
  // A mixed-sign stream: a revenue line with a negative year. rows.revenue clips per
  // line, so the streams do not add up to it, and the view must show rows.revenue.
  streams: { product_revenue: [100, -40], bill: [-30, -30], bc_staff: [-10, -10] },
  stream_class: { product_revenue: 'contract', bill: 'tariff', bc_staff: 'opex' },
  stream_source: { product_revenue: 'line:revenue', bill: 'energy_model', bc_staff: 'line:opex' },
  rows: {
    revenue: [100, 0], opex: [40, 40], ebitda: [60, -40], pass_through: [0, 0],
    revenue_net_of_pass_through: [100, 0], ebitda_net_of_pass_through: [60, -40],
    ebitda_margin: [0.6, null], terminal: [0, 0], depreciation: [5, 5], ebit: [55, -45],
    tax: null, net_income: null, debt_established: false,
  },
  not_finite: [],
}
const REPORT: BusinessCaseReport = {
  currency: 'EUR', basis: { id: 'b', label: 'Nominal, project' }, locked: [], pnl: PNL,
  kpis: {
    project_npv: { value: 1234, status: 'ok', reason: null, unit: 'EUR' },
    project_irr: { value: null, status: 'n/a', reason: 'irr_not_established' },
    min_dscr: { value: null, status: 'n/a', reason: 'no_debt' },
    payback_years: { value: null, status: 'not_established', reason: 'tax_not_established: rate' },
  },
  break_even: { status: 'break_even_not_bracketed' },
}
const DONE: BusinessCaseStudy = {
  status: 'done', report: REPORT, staleness: { stale: false, changed: [], reason: null },
}

function mount(study: BusinessCaseStudy | null, props: Partial<BusinessCaseRunProps> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onBreakEvenLine = vi.fn()
  const view = render(
    <QueryClientProvider client={qc}>
      <BusinessCaseRun study={study} studyKey={['bc']} disabledReason={null}
                       revenueLines={['H2 sales']} breakEvenLine={null}
                       onBreakEvenLine={onBreakEvenLine} {...props} />
    </QueryClientProvider>)
  return { ...view, onBreakEvenLine }
}

beforeEach(() => { vi.clearAllMocks() })
afterEach(() => cleanup())

describe('the P&L (GK B F1–F4)', () => {
  it('shows rows.revenue as the revenue total, never a sum of the mixed-sign streams', () => {
    mount(DONE)
    const row = screen.getByTestId('bc-row-revenue')
    const cells = within(row).getAllByRole('cell').map(c => c.textContent)
    expect(cells).toEqual(['100.00', '0.00'])
    // The streams are there, signed, with no subtotal row for their group.
    expect(within(screen.getByTestId('bc-stream-product_revenue')).getAllByRole('cell')
      .map(c => c.textContent)).toEqual(['100.00', '-40.00'])
    expect(screen.queryByText(/subtotal/i)).toBeNull()
  })

  it('groups streams by where they come from', () => {
    mount(DONE)
    expect(screen.getByTestId('bc-group-line:revenue').textContent ?? '').toContain('Revenue lines')
    expect(screen.getByTestId('bc-group-energy_model').textContent ?? '').toContain('From the energy model')
    expect(streamGroups(PNL).map(g => g.id)).toEqual(['line:revenue', 'energy_model', 'line:opex'])
  })

  it('a report without stream_source lists every stream under Other, with a note', () => {
    const { stream_source: _s, stream_class: _c, ...old } = PNL
    mount({ ...DONE, report: { ...REPORT, pnl: old } })
    expect(screen.getByTestId('bc-group-other').textContent ?? '').toContain('Other')
    expect(screen.getByTestId('bc-streams-other').textContent ?? '').toMatch(/predates the stream groups/)
  })

  it('a None row shows its reason, never 0, and net income is before financing', () => {
    mount(DONE)
    expect(screen.getByTestId('bc-row-tax').textContent ?? '').toContain('Not established. It needs the tax section.')
    expect(screen.getByTestId('bc-row-net_income').textContent ?? '').toContain('Not established. It needs EBIT and tax.')
    expect(screen.getByTestId('bc-net-income-note').textContent ?? '').toContain('Net income is before financing (unlevered).')
    expect(screen.queryByTestId('bc-row-interest')).toBeNull()
    // A None year inside a series is "n/a" with its reason, not 0.
    const gap = within(screen.getByTestId('bc-row-ebitda_margin')).getAllByRole('cell')[1]
    expect(gap.getAttribute('title')).toBe('n/a: there is no revenue this year')
    expect(gap.textContent).toBe('n/an/a: there is no revenue this year')
  })

  it('terminal is hidden only when exactly 0; a None terminal shows as not established', () => {
    mount(DONE)
    expect(screen.queryByTestId('bc-row-terminal')).toBeNull()
    cleanup()
    mount({ ...DONE, report: { ...REPORT, pnl: { ...PNL, rows: { ...PNL.rows, terminal: [0, 7] } } } })
    expect(screen.getByTestId('bc-row-terminal')).toBeTruthy()
    cleanup()
    mount({ ...DONE, report: { ...REPORT, pnl: { ...PNL, rows: { ...PNL.rows, terminal: null } } } })
    expect(screen.getByTestId('bc-row-terminal').textContent ?? '').toContain('Not established')
  })
})

describe('the row list (GK B W1)', () => {
  it('ROW_NEEDS names every row the report serves, and the view shows only those', () => {
    expect(Object.keys(ROW_NEEDS).sort()).toEqual(PNL_ROWS)
    for (const sec of PNL_SECTIONS) for (const r of sec.rows) expect(PNL_ROWS).toContain(r.key)
  })
})

describe('the KPI row (UX 6)', () => {
  it('reads ok values, and n/a or not established with the reason in words, never 0', () => {
    mount(DONE)
    expect(screen.getByTestId('bc-kpi-project_npv').textContent ?? '').toContain('EUR')
    expect(screen.getByTestId('bc-kpi-project_irr').textContent ?? '').toContain('n/a: the cash flows have no IRR')
    expect(screen.getByTestId('bc-kpi-min_dscr').textContent ?? '').toContain('n/a: there is no debt')
    expect(screen.getByTestId('bc-kpi-payback_years').textContent)
      .toContain('Not established: the tax section is not established (rate)')
    expect(screen.getByTestId('bc-kpi-equity_npv').textContent ?? '').toContain('Not established: not in this report')
    for (const el of screen.getAllByTestId(/^bc-kpi-/)) {
      if (el.getAttribute('data-status') !== 'ok') expect(el.textContent).not.toMatch(/(^|\s)0(\.0+)?(\s|$)/)
    }
    expect(kpiText({ value: 0, status: 'ok', reason: null }, 'pct')).toBe('0.00 %')
    expect(reasonWords(null)).toBe('no reason given')
    // Millions read as whole numbers; small amounts keep their cents.
    expect(fmtYear(4.2e6)).toBe((4.2e6).toLocaleString(undefined, { maximumFractionDigits: 0 }))
    expect(fmtYear(12.5)).toBe((12.5).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
    expect(fmtYear(null)).toBeNull()
  })

  it('break-even shows its reason when not established', () => {
    mount(DONE)
    expect(screen.getByTestId('bc-break-even').textContent ?? '').toContain('Not established: no price in the searched range breaks even')
  })
})

describe('the run', () => {
  it('Run says why it is off', () => {
    mount(null, { disabledReason: 'Save the lines to run.' })
    expect((screen.getByTestId('bc-run-button') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('bc-run-off').textContent ?? '').toContain('Save the lines to run.')
    expect(screen.getByTestId('bc-run-button').getAttribute('aria-describedby')).toBe('bc-run-off')
  })

  it('while running: stage words in the status region, the clock outside it, Abort', async () => {
    api.abortRun.mockResolvedValue({ status: 'running', aborting: true })
    mount({ status: 'running', stage: 'run_engine', stages: ['build', 'run_engine', 'store'], report: REPORT })
    const status = screen.getByRole('status')
    expect(status.textContent ?? '').toContain('Running the finance engine (step 2 of 3)')
    expect(status.textContent).not.toMatch(/\d:\d\d/)
    expect(screen.getByTestId('bc-clock').textContent ?? '').toMatch(/^\d:\d\d$/)
    expect(screen.queryByTestId('bc-report')).toBeNull()
    fireEvent.click(screen.getByTestId('bc-abort'))
    await waitFor(() => expect(api.abortRun).toHaveBeenCalled())
    expectAllButtonsNamed(document.body)
  })

  it('a 409 investment_case_missing offers the investment case', async () => {
    api.startRun.mockRejectedValue(new InvestmentCaseMissing('no_report'))
    const req = vi.spyOn(useUIStore.getState(), 'requestResultsTab')
    mount(null)
    fireEvent.click(screen.getByTestId('bc-run-button'))
    await screen.findByTestId('bc-blocked')
    fireEvent.click(screen.getByTestId('bc-open-ic'))
    expect(req).toHaveBeenCalledWith('investment', 'case')
  })

  it('one out-of-date mark naming what changed', () => {
    mount({ ...DONE, staleness: { stale: true, changed: ['business_case_inputs', 'investment_case'] } })
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByTestId('bc-out-of-date').textContent)
      .toContain('Out of date: the business-case lines and the investment case changed')
  })

  it('the break-even select stores through the caller', () => {
    const { onBreakEvenLine } = mount(DONE)
    fireEvent.change(screen.getByLabelText('Break-even on'), { target: { value: 'H2 sales' } })
    expect(onBreakEvenLine).toHaveBeenCalledWith('H2 sales')
  })
})
