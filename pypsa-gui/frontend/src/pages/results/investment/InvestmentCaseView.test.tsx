// The investment case run and results (IC P4 WP4.7b): run → progress → abort
// with a mocked API; a fixture report with not-established headlines and a
// stale record renders the markers (never 0 or blank); the WACC gate chip's
// three states; the cashflow, debt and tax tables; the xlsx link; every
// button named.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { financeApi, StudyBusyError } from '../../../api/finance'
import type { InvestmentCaseReportPayload, InvestmentCaseStudy } from '../../../api/types'
import InvestmentCaseView from './InvestmentCaseView'
import { expectAllButtonsNamed } from '../../../test-utils/accessibleName'

vi.mock('../../../api/finance', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/finance')>()
  return { ...actual, financeApi: { ...actual.financeApi, getInvestmentCase: vi.fn(),
    startInvestmentCase: vi.fn(), abortInvestmentCase: vi.fn(), getReport: vi.fn() } }
})
const api = vi.mocked(financeApi)

const YEARS = [2030, 2031, 2032]
const COMPLETENESS = { design: 'skipped', commercial: 'skipped', dispatch_modes: 'skipped',
  participants: 'ok', project: 'ok', debt: 'ok', tax: 'not_established', tax_equity: 'skipped',
  uncertainty: 'skipped', gates: 'not_established' } as const

/** The export view plus the sections, as the report route may serve it. */
const REPORT: InvestmentCaseReportPayload = {
  case_id: 'investment_case', assumptions_hash: 'abcdef0123456789', packs: { us_federal: 'h1' },
  project_irr_pre_tax: 0.085,
  project_irr_post_tax: null,
  npv_at_wacc: 1234567.8,
  lcoe_finance_consistent_eur_per_mwh: 55.5,
  ppa_price_for_target_irr_eur_per_mwh: null,
  min_dscr: 1.3, avg_dscr: 1.45, llcr: null, plcr: null,
  wacc_vs_discount_rate_consistent: null,
  completeness: { ...COMPLETENESS },
  sections: {
    project: { status: 'ok', payload: {
      years: YEARS, equity_post_tax_irr: 0.12, equity_pre_tax_irr: 0.14, equity_post_tax_npv: null,
      lifecycle_npv: -5000000, payback_years: 7.25, solved_ppa_price: null, solve_ppa_status: null,
      has_counterfactual: true, incremental_net: [0, 100, 110], counterfactual_net: [0, -900, -910],
      flags: ['load_shed_excluded:1.5', 'template_annualised:1.2'] } },
    debt: { status: 'ok', payload: {
      amount: 6000000, cfads: [null, 900000, 910000], service: [null, 600000, 610000],
      dscr: [null, 1.5, 1.49], tranches: [{ index: 0, kind: 'term_loan', amount: 6000000 }],
      flags: [] } },
    tax: { status: 'not_established', note: 'tax_pack_missing', payload: {
      tax_pack_id: null, layers: { federal: { liability: [0, 10, 12], taxable: [0, 50, 60],
                                              remaining_basis: null } } } },
    gates: { status: 'not_established', note: 'the WACC gate needs wacc_nominal',
             payload: { wacc_vs_discount_rate_consistent: null,
                        legs: { discount_rate: null, asset_rates: null, inflation: 'n/a' } } },
  },
  cashflow_lines: [
    { year: 2030, participant: 'owner', counterparty: 'external', value_stream: 'capex', amount: -1000,
      provenance: { source: 'capex', mode: 'pf' } },
    { year: 2031, participant: 'owner', counterparty: 'grid', value_stream: 'energy_export', amount: 300,
      provenance: { source: 'ledger', mode: 'pf' } },
    { year: 2031, participant: 'owner', counterparty: 'grid', value_stream: 'energy_export', amount: 20,
      provenance: { source: 'ledger', mode: 'pf' } },
    { year: 2031, participant: 'owner', counterparty: 'lender', value_stream: 'interest', amount: -50,
      provenance: { source: 'debt', mode: 'pf' } },
  ],
}

function renderView() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><InvestmentCaseView /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  api.getInvestmentCase.mockResolvedValue(null)
  api.getReport.mockResolvedValue(null)
  api.startInvestmentCase.mockResolvedValue({ status: 'running' })
  api.abortInvestmentCase.mockResolvedValue({ status: 'running', aborting: true })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('InvestmentCaseView', () => {
  it('runs, shows the progress, and aborts', async () => {
    const { container } = renderView()
    expect((await screen.findByTestId('ic-not-run')).textContent).toMatch(/No investment case/)
    const running: InvestmentCaseStudy = { status: 'running', stage: 'run_engine',
      stages: ['build_case', 'load_pack', 'run_engine', 'assemble', 'store'],
      stages_done: ['build_case', 'load_pack'] }
    api.getInvestmentCase.mockResolvedValue(running)
    fireEvent.click(screen.getByRole('button', { name: 'Run the investment case' }))
    await waitFor(() => expect(api.startInvestmentCase).toHaveBeenCalledTimes(1))
    const progress = await screen.findByTestId('ic-progress')
    expect(progress.textContent).toMatch(/run engine · 2 of 5 · 40 %/)
    expect(screen.getByLabelText('Investment case progress')).toBeTruthy()
    expect((screen.getByTestId('ic-run') as HTMLButtonElement).disabled).toBe(true)
    // While it runs, the previous report is not shown as current.
    expect(screen.queryByTestId('ic-report')).toBeNull()
    expectAllButtonsNamed(container)
    api.getInvestmentCase.mockResolvedValue({ status: 'aborted' })
    fireEvent.click(screen.getByRole('button', { name: 'Abort' }))
    await waitFor(() => expect(api.abortInvestmentCase).toHaveBeenCalledTimes(1))
    expect((await screen.findByTestId('ic-aborted')).textContent).toMatch(/aborted/)
    expect(screen.queryByTestId('ic-abort')).toBeNull()
    expect((screen.getByTestId('ic-run') as HTMLButtonElement).disabled).toBe(false)
  })

  it('reads a fraction progress too', async () => {
    api.getInvestmentCase.mockResolvedValue({ status: 'running', progress: 0.25 })
    renderView()
    expect((await screen.findByTestId('ic-progress')).textContent).toMatch(/25 %/)
  })

  it('a blocked run names what blocks it', async () => {
    api.startInvestmentCase.mockRejectedValue(new StudyBusyError('a frontier study is running'))
    renderView()
    fireEvent.click(await screen.findByRole('button', { name: 'Run the investment case' }))
    expect((await screen.findByTestId('ic-blocked')).textContent).toMatch(/frontier study is running/)
  })

  it('a refused or failed run says why', async () => {
    api.getInvestmentCase.mockResolvedValue({ status: 'refused', error: 'no owner assets',
                                              error_code: 'no_owner' })
    renderView()
    expect((await screen.findByTestId('ic-error')).textContent)
      .toBe('The case was refused: no owner assets (no_owner)')
  })

  it('renders a report with not-established headlines and the stale marker', async () => {
    api.getInvestmentCase.mockResolvedValue({ status: 'done',
      report: { present: true, stale: true, changed: ['finance', 'value_flows'] } })
    api.getReport.mockResolvedValue(structuredClone(REPORT))
    const { container } = renderView()
    await screen.findByTestId('ic-report')
    expect(screen.getByTestId('ic-stale').textContent).toMatch(/Stale: finance, value flows changed/)

    const h = (id: string) => screen.getByTestId(`ic-headline-${id}`).textContent ?? ''
    expect(h('project_irr_pre_tax')).toBe('8.50 %')
    expect(h('project_irr_post_tax')).toBe('not established')
    expect(h('npv_at_wacc')).toMatch(/1,234,567\.80/)
    expect(h('equity_irr_post_tax')).toBe('12.00 %')
    expect(h('equity_irr_pre_tax')).toBe('14.00 %')
    expect(h('equity_npv_post_tax')).toBe('not established')
    expect(h('lifecycle_npv')).toMatch(/-5,000,000\.00|−5,000,000\.00/)
    expect(h('payback_years')).toBe('7.3 years')
    expect(h('min_dscr')).toBe('1.30×')
    expect(h('avg_dscr')).toBe('1.45×')
    expect(h('llcr')).toBe('not established')
    expect(h('plcr')).toBe('not established')
    expect(h('lcoe')).toBe('55.50 per MWh')
    expect(h('ppa_price')).toBe('not established')
    // No storage in the case: no LCOS row; no basis stated: said so.
    expect(screen.queryByTestId('ic-headline-lcos')).toBeNull()
    expect(screen.getByTestId('ic-basis').textContent).toBe('Price basis: not stated')
    // Never a zero or a blank where the report establishes nothing.
    for (const el of screen.getByTestId('ic-headlines').querySelectorAll('dd')) {
      expect(el.textContent?.trim()).not.toBe('')
      if (el.getAttribute('data-established') === 'false') expect(el.textContent).toMatch(/^not established/)
    }

    const gate = screen.getByTestId('ic-wacc-gate')
    expect(gate.getAttribute('data-state')).toBe('not_established')
    expect(gate.textContent).toMatch(/WACC gate: not established/)
    expect(gate.textContent).toMatch(/discount rate not established/)

    expect(screen.getByTestId('ic-counterfactual-statement').textContent).toMatch(/has a counterfactual/)
    expect(screen.getByTestId('ic-project-flags').textContent).toMatch(/load_shed_excluded/)

    expect(screen.getByTestId('ic-case-section-tax').getAttribute('data-status')).toBe('not_established')
    expect(screen.getByTestId('ic-case-section-project').getAttribute('data-status')).toBe('ok')

    // The cashflow table: years × streams, summed, with a total.
    const cf = screen.getByTestId('ic-cashflows')
    const y2031 = within(cf).getByTestId('ic-cashflow-2031')
    expect(y2031.textContent).toMatch(/320\.00/)
    expect(y2031.textContent).toMatch(/-50\.00|−50\.00/)
    // No tax pack → no post-tax equity cash: the Total is not established (the
    // lines without tax would read as established; WP4.7 review round 2, R2-1).
    expect(y2031.lastElementChild?.textContent).toBe('not established')
    expect(within(cf).getByTestId('ic-cashflows-incomplete').textContent).toMatch(/tax_pack_missing/)
    expect(within(cf).getByTestId('ic-cashflow-2030').textContent).toMatch(/–/)

    // The debt schedule: a row per year with the DSCR; unknown years are
    // "not established", not 0.
    const debt = screen.getByTestId('ic-debt')
    const table = within(debt).getAllByRole('table')[0]
    expect(within(table).getByRole('columnheader', { name: 'dscr' })).toBeTruthy()
    const rows = within(table).getAllByRole('row')
    expect(rows[1].textContent).toMatch(/2030/)
    expect(rows[1].textContent).toMatch(/not established/)
    expect(rows[2].textContent).toMatch(/1\.50/)

    // The tax by layer, with the section's reason.
    const tax = screen.getByTestId('ic-tax')
    expect(tax.textContent).toMatch(/Not established: tax_pack_missing/)
    expect(within(tax).getByText('federal')).toBeTruthy()
    expect(tax.textContent).toMatch(/remaining basis/)

    const link = screen.getByTestId('ic-export-xlsx') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/api/results/investment_case/export.xlsx')
    expect(link.hasAttribute('download')).toBe(true)
    expect(link.textContent).toBe('Export (xlsx)')
    expectAllButtonsNamed(container)
  })

  it('shows the WACC gate as consistent or differs from either shape', async () => {
    api.getReport.mockResolvedValue({ ...structuredClone(REPORT), gates: { wacc_vs_discount_rate_consistent: true } })
    renderView()
    expect((await screen.findByTestId('ic-wacc-gate')).getAttribute('data-state')).toBe('consistent')
    cleanup()
    api.getReport.mockResolvedValue({ ...structuredClone(REPORT), wacc_vs_discount_rate_consistent: false })
    renderView()
    const gate = await screen.findByTestId('ic-wacc-gate')
    expect(gate.getAttribute('data-state')).toBe('differs')
    expect(gate.textContent).toMatch(/differs/)
  })

  it('a project section not established marks the cash table incomplete (review B2)', async () => {
    const rep = structuredClone(REPORT)
    rep.completeness = { ...COMPLETENESS, project: 'not_established' }
    api.getReport.mockResolvedValue(rep)
    renderView()
    await screen.findByTestId('ic-report')
    const cf = screen.getByTestId('ic-cashflows')
    expect(within(cf).getByTestId('ic-cashflows-incomplete').textContent).toMatch(/incomplete/)
    const row = within(cf).getByTestId('ic-cashflow-2031')
    expect(row.lastElementChild?.textContent).toBe('not established')
  })

  it('totals are the engine\'s equity cash; counterfactual lines get their own columns (review r2)', async () => {
    const rep = structuredClone(REPORT)
    rep.completeness = { ...COMPLETENESS, tax: 'ok' }
    const sections = rep.sections as Record<string, { status: string; note?: string | null;
      payload: Record<string, unknown> }>
    sections.tax = { status: 'ok', payload: {} }
    // A real-shaped counterfactual case (an engine dump): the operating status
    // says terminal not established — the cash stage defers a book value — but
    // the engine resolved it and each year's lines sum to the equity cash.
    sections.project.payload = { ...sections.project.payload,
      operating_status: { operating: 'ok', capex: 'ok', terminal: 'not_established' },
      cash: { equity_post_tax: [-1000, 330, 250] } }
    rep.cashflow_lines = [
      { year: 2030, participant: 'owner', counterparty: 'external', value_stream: 'capex', amount: -1000,
        provenance: { source: 'capex', mode: 'pf' } },
      { year: 2031, participant: 'owner', counterparty: 'grid', value_stream: 'energy_import', amount: -60,
        provenance: { source: 'ledger', mode: 'pf' } },
      { year: 2031, participant: 'owner', counterparty: 'grid', value_stream: 'energy_import', amount: 100,
        provenance: { source: 'counterfactual:bill', mode: 'pf', source_id: 'bill' } },
      { year: 2031, participant: 'owner', counterparty: 'offtaker', value_stream: 'ppa_settlement',
        amount: 290, provenance: { source: 'ledger', mode: 'pf' } },
      { year: 2032, participant: 'owner', counterparty: 'external', value_stream: 'terminal_value',
        amount: 200, provenance: { source: 'terminal_value', mode: 'pf' } },
      { year: 2032, participant: 'owner', counterparty: 'offtaker', value_stream: 'ppa_settlement',
        amount: 40, provenance: { source: 'ledger', mode: 'pf' } },
    ]
    api.getReport.mockResolvedValue(rep)
    renderView()
    await screen.findByTestId('ic-report')
    const cf = screen.getByTestId('ic-cashflows')
    expect(within(cf).queryByTestId('ic-cashflows-incomplete')).toBeNull()
    expect(within(cf).getByRole('columnheader', { name: 'avoided vs counterfactual · energy import' }))
      .toBeTruthy()
    expect(within(cf).getByRole('columnheader', { name: 'energy import' })).toBeTruthy()
    expect(within(cf).getByTestId('ic-cashflows-avoided').textContent).toMatch(/not money received/)
    const y2031 = within(cf).getByTestId('ic-cashflow-2031')
    expect(y2031.textContent).toMatch(/100\.00/)        // the avoided cost, apart
    expect(y2031.lastElementChild?.textContent).toBe('330.00')
    // 2032: the lines (240) do not sum to the equity cash (250) — shown, not hidden.
    expect(within(cf).getByTestId('ic-cashflow-2032').lastElementChild?.textContent).toBe('250.00')
    expect(within(cf).getByTestId('ic-cashflows-mismatch').textContent).toMatch(/2032/)
  })

  it('a not-established line reads "not established" in its cells (P4 gate B2)', async () => {
    const rep = structuredClone(REPORT)
    const sections = rep.sections as Record<string, { payload: Record<string, unknown> }>
    sections.project.payload = { ...sections.project.payload, owner: 'owner',
      lines_not_established: [{ key: 'bill:energy', value_stream: 'energy_import',
                                counterparty: 'retailer', source: 'bill', years: [2031, 2032] }] }
    api.getReport.mockResolvedValue(rep)
    renderView()
    await screen.findByTestId('ic-report')
    const cf = screen.getByTestId('ic-cashflows')
    const header = within(cf).getAllByRole('columnheader').map(h => h.textContent)
    const col = header.indexOf('energy import')
    expect(col).toBeGreaterThan(0)
    for (const y of [2031, 2032]) {
      const cells = within(cf).getByTestId(`ic-cashflow-${y}`).children
      expect(cells[col].textContent).toBe('not established')
      expect(cells[col].getAttribute('data-established')).toBe('false')
    }
  })

  it('an unconfirmable currency says why, not "changed" (review B4)', async () => {
    api.getInvestmentCase.mockResolvedValue({ status: 'done',
      report: { present: true, stale: true, changed: [], reason: 'solve_in_flight' } })
    api.getReport.mockResolvedValue(structuredClone(REPORT))
    renderView()
    await screen.findByTestId('ic-report')
    expect(screen.getByTestId('ic-stale').textContent).toMatch(/a solve is running/)
    expect(screen.getByTestId('ic-stale').textContent).not.toMatch(/changed/)
  })

  it('an export-only report (no sections) still says what is not established', async () => {
    api.getReport.mockResolvedValue({ case_id: 'c', assumptions_hash: 'abcdef012345',
      project_irr_pre_tax: null, project_irr_post_tax: 0.06, npv_at_wacc: null,
      lcoe_finance_consistent_eur_per_mwh: null, ppa_price_for_target_irr_eur_per_mwh: null,
      min_dscr: null, avg_dscr: null, llcr: null, plcr: null, wacc_vs_discount_rate_consistent: true,
      completeness: { ...COMPLETENESS } })
    renderView()
    await screen.findByTestId('ic-report')
    expect(screen.getByTestId('ic-headline-project_irr_post_tax').textContent).toBe('6.00 %')
    expect(screen.getByTestId('ic-headline-equity_irr_post_tax').textContent).toBe('not established')
    expect(screen.getByTestId('ic-headline-payback_years').textContent).toBe('not established')
    expect(screen.getByTestId('ic-cashflows-unavailable').textContent).toMatch(/not established/)
    expect(screen.getByTestId('ic-counterfactual-statement').textContent).toMatch(/states no counterfactual/)
    expect(screen.getByTestId('ic-debt').textContent).toMatch(/serves no detail/)
    expect(screen.getByTestId('ic-tax').textContent).toMatch(/Not established/)
    expect(screen.getByTestId('ic-export-xlsx')).toBeTruthy()
  })
})
