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
import {
  fmtYear, kpiText, PNL_SECTIONS, REASON_CODE_WORDS, reasonCodeWords, reasonWords, ROW_NEEDS, rowReason,
  streamGroups,
} from './businessCaseReport'
import PNL_ROWS from './businessCasePnlRows.json'
import REASON_CODES from './businessCaseRowReasonCodes.json'
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

describe('the served row reason (BC 17)', () => {
  const item = (section: string | null, code: string) => ({ section, code })
  it('words the served items by section, and ROW_NEEDS only for a report stored before', () => {
    const served = { ...PNL, row_reason: {
      tax: [item('tax', 'tax_pack_missing')],
      net_income: [item('operating', 'line_not_established:nh3'),
        item('operating', 'linked_output_not_evaluated:served_product.nh3:stale'),
        item('inputs', 'input_missing:receivable_days'), item('tax', 'tax_pack_missing')],
    } }
    expect(rowReason('tax', served)).toBe('Not established: the tax section needs a tax pack.')
    expect(rowReason('net_income', served)).toBe('Not established: the operating section needs '
      + 'the line “nh3” and its linked output, which is stale; '
      + 'the business case needs receivable days; the tax section needs a tax pack.')
    // Served wins; absent falls back; an empty map from a new report never falls back.
    expect(rowReason('tax', PNL)).toBe('Not established. It needs the tax section.')
    expect(rowReason('tax', { ...PNL, row_reason: {} })).toBe('Not established.')
    expect(rowReason('tax', { ...PNL, not_finite: ['tax'], row_reason: served.row_reason }))
      .toBe('Not established. The figure is out of range.')
    expect(rowReason('ebit', { ...PNL, row_reason: { ebit: [item(null, 'not_finite:ebitda')] } }))
      .toBe('Not established: the EBITDA figure is out of range.')
    // Two codes with the same words read once (two lines missing one escalation class).
    expect(rowReason('revenue', { ...PNL, row_reason: { revenue: [
      item('operating', 'escalation_missing:tariff:bill'), item('operating', 'escalation_missing:tariff:fee')] } }))
      .toBe('Not established: the operating section needs an escalation rate for tariff.')
    expect(rowReason('x', { ...PNL, row_reason: { x: [item(null, 'not_established')] } }))
      .toBe('Not established.')
    mount({ ...DONE, report: { ...REPORT, pnl: served } })
    expect(screen.getByTestId('bc-row-tax').textContent ?? '')
      .toContain('Not established: the tax section needs a tax pack.')
    // An established row shows no reason.
    expect(screen.getByTestId('bc-row-revenue').textContent ?? '').not.toContain('Not established')
    cleanup()
    mount({ ...DONE, report: { ...REPORT, pnl: { ...PNL, row_reason: {} } } })
    expect(screen.getByTestId('bc-row-tax').textContent ?? '').not.toContain('It needs')
    expect(screen.getByTestId('bc-row-revenue').textContent ?? '').not.toContain('Not established')
  })

  it('has words for every code family the report can serve, never a raw code (GK B C3)', () => {
    expect(Object.keys(REASON_CODE_WORDS).sort()).toEqual(REASON_CODES)
    for (const fam of REASON_CODES) {
      const w = reasonCodeWords(`${fam}:some_asset:2`)
      // The line's own name is the user's words, shown as typed (UX 1).
      expect(w.replace(/“[^”]*”/g, ''), fam).not.toMatch(/[_:]/)
      expect(w.length, fam).toBeGreaterThan(0)
    }
    expect(reasonCodeWords('a_future_code:x_y')).toBe('a future code x y')
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
  it('says "not run yet" only when no other reason is showing', async () => {
    mount(null)
    expect(screen.getByTestId('bc-not-run')).toBeTruthy()
    cleanup()
    mount(null, { disabledReason: 'Fix the 2 refusals above to run.' })
    expect(screen.queryByTestId('bc-not-run')).toBeNull()
    cleanup()
    api.startRun.mockRejectedValue(new InvestmentCaseMissing('no_report'))
    mount(null)
    fireEvent.click(screen.getByTestId('bc-run-button'))
    await screen.findByTestId('bc-blocked')
    expect(screen.queryByTestId('bc-not-run')).toBeNull()
  })

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

// BC 15: the volume factor (lease-up) in the report.
describe('the volume factor in the report', () => {
  const LEASED: BusinessCaseStudy = { ...DONE, report: { ...REPORT, pnl: {
    ...PNL, volume_factor: { 2030: 0.6 },
    volume_factor_note: 'volume-tied costs and revenue scaled pro rata by the factor; the dispatch was not re-solved at reduced load',
  } } }

  it('at default adds no row, note, mark or ignored line', () => {
    mount(DONE)
    for (const id of ['bc-row-volume_factor', 'bc-volume-factor-note', 'bc-kpi-volume-factor', 'bc-volume-factor-ignored']) {
      expect(screen.queryByTestId(id)).toBeNull()
    }
  })
  it('a first P&L row reads every year as a percent, with the note under it', () => {
    mount(LEASED)
    const row = screen.getByTestId('bc-row-volume_factor')
    expect(row.querySelector('th')!.textContent).toMatch(/^Volume factor \(lease-up\), %/)
    expect(within(row).getAllByRole('cell').map(c => c.textContent)).toEqual(['60 %', '100 %'])
    expect(screen.getByTestId('bc-volume-factor-note').textContent).toMatch(/^Volume-tied costs .* reduced load\.$/)
    // First in "Profit and loss": before the revenue row.
    expect(row.compareDocumentPosition(screen.getByTestId('bc-row-revenue')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
  it('marks the KPI row', () => {
    mount(LEASED)
    expect(screen.getByTestId('bc-kpi-volume-factor').textContent).toBe(
      'With volume factor (lease-up) These figures differ from the Investment case because of the volume factor.')
  })
  it('names the ignored years, and Remove hands back the ones the draft still states', () => {
    const onRemove = vi.fn()
    const study = { ...LEASED, report: { ...LEASED.report!, pnl: { ...LEASED.report!.pnl!, volume_factor_ignored: [2028, 2029] } } }
    mount(study, { volumeFactorYears: ['2029', '2030'], onRemoveVolumeFactorYears: onRemove })
    const line = screen.getByTestId('bc-volume-factor-ignored')
    expect(line.textContent).toMatch(/^Ignored, outside the operating years: 2028, 2029/)
    fireEvent.click(within(line).getByRole('button'))
    expect(onRemove).toHaveBeenCalledWith([2029])
  })
  it('a run refused on the volume factor reads plain words, never the kind', () => {
    mount({ status: 'refused', error: 'business_case_refused:volume_factor_unclassified: energy_export',
            error_code: 'business_case_refused',
            refusal: { code: 'business_case_refused', refusal: 'volume_factor_unclassified', field: 'energy_export' } as unknown as string })
    const text = screen.getByTestId('bc-failure').textContent ?? ''
    expect(text).toMatch(/^Refused: Volume factor \(lease-up\), %: The energy-model row “energy export”/)
    expect(text).not.toMatch(/volume_factor/)
  })
})
