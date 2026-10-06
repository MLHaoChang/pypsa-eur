// The finance inputs form (IC P4 WP4.7a): a stored FinanceInputs round-trips
// unchanged, an empty input is null / not stated (never 0), the server's 422
// lands at the field it names, a 412 asks for a reload, and every button is
// named.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { financeApi, SolverInFlightError, StaleEditError } from '../../../api/finance'
import { commercialApi } from '../../../api/commercial'
import type { FinanceInputs } from '../../../api/types'
import FinanceInputsEditor from './FinanceInputsEditor'
import { expectAllButtonsNamed } from '../../../test-utils/accessibleName'

vi.mock('../../../api/finance', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/finance')>()
  return { ...actual, financeApi: { ...actual.financeApi, getFinance: vi.fn(), putFinance: vi.fn() } }
})
vi.mock('../../../api/commercial', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/commercial')>()
  return { ...actual, commercialApi: { ...actual.commercialApi, getDesigner: vi.fn() } }
})
const api = vi.mocked(financeApi)

// Every field the form edits, with nulls ("not stated") beside typed zeros,
// plus fields it does not edit (kept as stored).
const FINANCE = {
  currency: 'USD',
  financial_close: '2030-01-15',
  cod_by_asset: { pv: '2031-01-01', bess: '2031-01-01' },
  construction_months_by_asset: { pv: 12 },
  capex_phasing: [0.4, 0.6],
  contingency_share: 0.05,
  escalation: { opex: 0.025, ppa: 0, capex: 0.02 },
  degradation_by_asset: { pv: 0.005, bess: [0.02, 0.015] },
  analysis_years: 25,
  acquisition_date: null,
  construction_start: '2029-06-01',
  annualise: false,
  tax_losses: 'offset_other_income',
  financing_fee_tax: null,
  hebesatz_pct: 400,
  state_rate: null,
  pwa_met: true,
  small_business_163j: null,
  reserves_rate: 0.02,
  solve_ppa: { contract_id: null, target_irr: 0.1, target_year: 20 },
  depreciation_class_by_asset: { pv: 'macrs_5' },
  replacement_capex: [[2040, 'bess', 1000000]],
  terminal_value: { method: 'book_value', value: null },
  wacc_nominal: 0.07,
  cost_of_equity: 0.1,
  inflation: 0.02,
  debt: [
    { kind: 'term_loan', amount: null, gearing: 0.6, gearing_base: 'capex', rate: [0.05, 0.055],
      tenor_years: 15, sculpting: 'annuity', dscr_target: null, max_gearing: null, dsra_months: 6,
      upfront_fee: 0.01, commitment_fee: null, grace_years: 0 },
    { kind: 'mezzanine', amount: null, gearing: null, gearing_base: 'capex', rate: 0.08, tenor_years: 10,
      sculpting: 'dscr_target', dscr_target: 1.3, max_gearing: 0.8, dsra_months: null,
      upfront_fee: null, commitment_fee: null, grace_years: null },
  ],
  tax_pack_id: 'us_federal',
  incentives: [
    { kind: 'itc', rate: 0.3, amount: null,
      eligibility: { begin_construction_by: '2032-12-31', placed_in_service_by: null, asset_classes: ['pv'] },
      phase_out: [], feoc_flag: null, grant_tax_treatment: null },
    { kind: 'grant', rate: null, amount: 500000, eligibility: { asset_classes: [] }, phase_out: [],
      feoc_flag: false, grant_tax_treatment: 'reduces_basis' },
  ],
  tax_equity: null,
  participants: [],
} as unknown as FinanceInputs

function renderEditor() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><FinanceInputsEditor /></QueryClientProvider>)
}
const putBody = (n = 0) => api.putFinance.mock.calls[n][0] as unknown as Record<string, unknown>
const errorOf = (el: HTMLElement) => {
  const id = el.getAttribute('aria-describedby')
  return id ? document.getElementById(id)?.textContent ?? '' : ''
}
const unprocessable = (detail: unknown) => Object.assign(new Error('422'),
  { response: { status: 422, data: { detail } } })

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  api.getFinance.mockResolvedValue({ finance: structuredClone(FINANCE), digest: 'digest-1', status: 'ok' })
  api.putFinance.mockImplementation(async (f) => ({ finance: f, digest: 'digest-2', status: 'ok' }))
  vi.mocked(commercialApi.getDesigner).mockResolvedValue({ site_party: 'site', assets: [
    { component: 'Generator', name: 'pv', bus: 'site', side: 'site', ownable: true, flags: [], carrier: 'solar' },
  ], tariff_items: [], contract_parties: [], group_members: [], default_externals: [] } as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('FinanceInputsEditor', () => {
  it('round-trips a stored FinanceInputs unchanged, with If-Match', async () => {
    const { container } = renderEditor()
    await screen.findByTestId('fi-tranche-1')
    fireEvent.click(screen.getByRole('button', { name: 'Save the finance inputs' }))
    await waitFor(() => expect(api.putFinance).toHaveBeenCalledTimes(1))
    expect(putBody()).toEqual(FINANCE)
    expect(api.putFinance.mock.calls[0][1]).toBe('digest-1')
    expect(await screen.findByText(/Finance inputs saved/)).toBeTruthy()
    expectAllButtonsNamed(container)
  })

  it('shows every unstated value as "not stated", never 0', async () => {
    renderEditor()
    await screen.findByTestId('fi-tranche-1')
    const state = screen.getByLabelText('State / provincial rate') as HTMLInputElement
    expect(state.value).toBe('')
    expect(state.placeholder).toBe('not stated')
    expect((screen.getByLabelText('Small business (§163(j) exempt)') as HTMLSelectElement).value).toBe('')
    expect((screen.getByLabelText('Financing fee tax') as HTMLSelectElement)
      .selectedOptions[0].textContent).toBe('not stated')
    expect((screen.getByLabelText('Tranche 1 commitment fee') as HTMLInputElement).value).toBe('')
    // A typed 0 stays a 0; a class with no rate is empty, not 0.
    expect((screen.getByLabelText('Tranche 1 grace (years)') as HTMLInputElement).value).toBe('0')
    expect((screen.getByLabelText('Escalation ppa') as HTMLInputElement).value).toBe('0')
    expect((screen.getByLabelText('Escalation fuel') as HTMLInputElement).value).toBe('')
    // Stored lists are shown as lists.
    expect((screen.getByLabelText('Tranche 1 rate') as HTMLInputElement).value).toBe('0.05, 0.055')
    expect((screen.getByLabelText('Degradation, bess') as HTMLInputElement).value).toBe('0.02, 0.015')
  })

  it('an emptied number is null, an emptied escalation class is not stated, never 0', async () => {
    renderEditor()
    await screen.findByTestId('fi-tranche-1')
    fireEvent.change(screen.getByLabelText('WACC'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Tranche 1 DSRA (months)'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Escalation opex'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Hebesatz (%)'), { target: { value: '380' } })
    fireEvent.change(screen.getByLabelText('Tax losses'), { target: { value: 'carryforward' } })
    fireEvent.change(screen.getByLabelText('Prevailing wage and apprenticeship met'), { target: { value: '' } })
    const rate = screen.getByLabelText('Tranche 2 rate')
    fireEvent.focus(rate); fireEvent.change(rate, { target: { value: '0.07, 0.075' } }); fireEvent.blur(rate)
    fireEvent.click(screen.getByRole('button', { name: 'Save the finance inputs' }))
    await waitFor(() => expect(api.putFinance).toHaveBeenCalled())
    const body = putBody()
    expect(body.wacc_nominal).toBeNull()
    expect(body.pwa_met).toBeNull()
    expect(body.hebesatz_pct).toBe(380)
    expect(body.tax_losses).toBe('carryforward')
    expect(body.escalation).toEqual({ ppa: 0, capex: 0.02 })
    const debt = body.debt as Array<Record<string, unknown>>
    expect(debt[0].dsra_months).toBeNull()
    expect(debt[1].rate).toEqual([0.07, 0.075])
    // Untouched fields keep their stored shape.
    expect(body.replacement_capex).toEqual(FINANCE.replacement_capex)
    expect(body.construction_months_by_asset).toEqual({ pv: 12 })
  })

  it('the price basis: an unstated currency year is "not stated"; both fields are sent as typed', async () => {
    renderEditor()
    await screen.findByTestId('fi-tranche-1')
    const year = screen.getByLabelText('Currency year') as HTMLInputElement
    expect(year.value).toBe('')
    expect(year.placeholder).toBe('not stated')
    const basis = screen.getByLabelText('Price basis') as HTMLSelectElement
    expect(basis.value).toBe('')
    expect(basis.selectedOptions[0].textContent).toBe('(default: nominal)')
    fireEvent.change(year, { target: { value: '2020' } })
    fireEvent.change(basis, { target: { value: 'real' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the finance inputs' }))
    await waitFor(() => expect(api.putFinance).toHaveBeenCalled())
    expect(putBody().currency_year).toBe(2020)
    expect(putBody().price_basis).toBe('real')
  })

  it('offers the part-lifetimes replacement rule and the remaining-life terminal value (IC S0b S8)', async () => {
    renderEditor()
    await screen.findByTestId('fi-tranche-1')
    const rule = screen.getByLabelText('Replacements') as HTMLSelectElement
    expect(rule.value).toBe('')
    expect(rule.selectedOptions[0].textContent).toBe('(default: the stated replacement entries)')
    expect([...rule.options].map(o => o.value)).toEqual(['', 'fixed', 'part_lifetimes'])
    const tv = screen.getByLabelText('Terminal value method') as HTMLSelectElement
    expect([...tv.options].map(o => o.value)).toContain('remaining_life_annuity')
    fireEvent.change(rule, { target: { value: 'part_lifetimes' } })
    fireEvent.change(tv, { target: { value: 'remaining_life_annuity' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the finance inputs' }))
    await waitFor(() => expect(api.putFinance).toHaveBeenCalled())
    expect(putBody().replacement_rule).toBe('part_lifetimes')
    expect(putBody().terminal_value).toEqual({ method: 'remaining_life_annuity', value: null })
  })

  it('an emptied currency year is null, never 0', async () => {
    api.getFinance.mockResolvedValue({ finance: { ...structuredClone(FINANCE), currency_year: 2024,
      price_basis: 'nominal' }, digest: 'digest-1', status: 'ok' })
    renderEditor()
    await screen.findByTestId('fi-tranche-1')
    const year = screen.getByLabelText('Currency year') as HTMLInputElement
    expect(year.value).toBe('2024')
    fireEvent.change(year, { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the finance inputs' }))
    await waitFor(() => expect(api.putFinance).toHaveBeenCalled())
    expect(putBody().currency_year).toBeNull()
    expect(putBody().price_basis).toBe('nominal')
  })

  it('switching a tranche to DSCR sculpting drops its amount / gearing', async () => {
    renderEditor()
    await screen.findByTestId('fi-tranche-0')
    fireEvent.change(screen.getByLabelText('Tranche 1 repayment'), { target: { value: 'dscr_target' } })
    expect(screen.getByLabelText('Tranche 1 DSCR target')).toBeTruthy()
    expect(screen.queryByLabelText('Tranche 1 gearing')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Save the finance inputs' }))
    await waitFor(() => expect(api.putFinance).toHaveBeenCalled())
    const t = (putBody().debt as Array<Record<string, unknown>>)[0]
    expect(t.sculpting).toBe('dscr_target')
    expect('gearing' in t || 'amount' in t).toBe(false)
    expect(t.dscr_target).toBeNull()
  })

  it('a 422 lands at the field it names; the rest at the top', async () => {
    api.putFinance.mockRejectedValueOnce(unprocessable({
      code: 'finance_inputs_invalid', message: 'Input should be greater than or equal to 1',
      errors: [
        { loc: ['debt', 0, 'tenor_years'], msg: 'Input should be greater than or equal to 1' },
        { loc: ['incentives', 1, 'grant_tax_treatment'], msg: 'Input should be reduces_basis or taxable' },
        { loc: [], msg: 'Value error, capex_phasing must sum to 1' },
      ] }))
    renderEditor()
    await screen.findByTestId('fi-tranche-0')
    fireEvent.click(screen.getByRole('button', { name: 'Save the finance inputs' }))
    const tenor = screen.getByLabelText('Tranche 1 tenor (years)')
    await waitFor(() => expect(tenor.getAttribute('aria-invalid')).toBe('true'))
    expect(errorOf(tenor)).toMatch(/greater than or equal to 1/)
    expect(errorOf(screen.getByLabelText('Incentive 2 grant tax treatment'))).toMatch(/reduces_basis or taxable/)
    expect(screen.getByLabelText('Tranche 2 tenor (years)').getAttribute('aria-invalid')).toBe('false')
    expect(screen.getByText(/capex_phasing must sum to 1/).closest('#fi-top-errors')).not.toBeNull()
    expect(screen.getByRole('status').textContent).toMatch(/refused some fields/)
  })

  it('a FastAPI 422 (body-prefixed locs) lands at its field too', async () => {
    api.putFinance.mockRejectedValueOnce(unprocessable([
      { loc: ['body', 'finance', 'wacc_nominal'], msg: 'Input should be greater than or equal to 0' },
      { loc: ['body', 'finance', 'degradation_by_asset', 'pv'], msg: 'bad rate' },
    ]))
    renderEditor()
    await screen.findByTestId('fi-tranche-0')
    fireEvent.click(screen.getByRole('button', { name: 'Save the finance inputs' }))
    const wacc = screen.getByLabelText('WACC')
    await waitFor(() => expect(wacc.getAttribute('aria-invalid')).toBe('true'))
    expect(errorOf(wacc)).toMatch(/greater than or equal to 0/)
    expect(errorOf(screen.getByLabelText('Degradation, pv'))).toMatch(/bad rate/)
  })

  it('a 412 asks for a reload, and the reload re-seeds with the new digest', async () => {
    api.putFinance.mockRejectedValueOnce(new StaleEditError('finance_changed'))
    const { container } = renderEditor()
    await screen.findByTestId('fi-tranche-0')
    fireEvent.change(screen.getByLabelText('WACC'), { target: { value: '0.08' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the finance inputs' }))
    const stale = await screen.findByTestId('fi-stale')
    expect(stale.textContent).toMatch(/changed elsewhere/)
    expectAllButtonsNamed(container)
    api.getFinance.mockResolvedValue({ finance: { ...structuredClone(FINANCE), wacc_nominal: 0.065 },
                                       digest: 'digest-3', status: 'ok' })
    fireEvent.click(within(stale).getByRole('button', { name: 'Reload the finance inputs' }))
    await waitFor(() => expect((screen.getByLabelText('WACC') as HTMLInputElement).value).toBe('0.065'))
    expect(screen.queryByTestId('fi-stale')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Save the finance inputs' }))
    await waitFor(() => expect(api.putFinance).toHaveBeenCalledTimes(2))
    expect(api.putFinance.mock.calls[1][1]).toBe('digest-3')
  })

  it('a solve in flight says so', async () => {
    api.putFinance.mockRejectedValueOnce(new SolverInFlightError('a solve is running'))
    renderEditor()
    await screen.findByTestId('fi-tranche-0')
    fireEvent.click(screen.getByRole('button', { name: 'Save the finance inputs' }))
    expect((await screen.findByRole('status')).textContent).toMatch(/solve is running/)
  })

  it('with no inputs stated, starts an empty case the server judges', async () => {
    api.getFinance.mockResolvedValue({ finance: null, digest: 'digest-0', status: 'not_set' })
    const { container } = renderEditor()
    fireEvent.click(await screen.findByRole('button', { name: 'State the finance inputs' }))
    expect((screen.getByLabelText('Analysis years') as HTMLInputElement).value).toBe('')
    fireEvent.change(screen.getByLabelText('Financial close'), { target: { value: '2030-01-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add a debt tranche' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add an incentive' }))
    fireEvent.change(screen.getByLabelText('COD: new asset'), { target: { value: 'pv' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add a COD for this asset' }))
    fireEvent.change(screen.getByLabelText('COD, pv'), { target: { value: '2031-01-01' } })
    fireEvent.click(screen.getByLabelText('Solve for a PPA price'))
    expectAllButtonsNamed(container)
    fireEvent.click(screen.getByRole('button', { name: 'Save the finance inputs' }))
    await waitFor(() => expect(api.putFinance).toHaveBeenCalled())
    expect(putBody()).toEqual({
      financial_close: '2030-01-01', cod_by_asset: { pv: '2031-01-01' },
      debt: [{ kind: 'term_loan', amount: null, rate: null, tenor_years: null, upfront_fee: null,
               commitment_fee: null, dsra_months: null, grace_years: null }],
      incentives: [{ kind: 'itc' }],
      solve_ppa: { contract_id: null, target_irr: null, target_year: null },
    })
    expect(api.putFinance.mock.calls[0][1]).toBe('digest-0')
  })

  it('a list that is not numbers blocks the save', async () => {
    renderEditor()
    await screen.findByTestId('fi-tranche-0')
    const phasing = screen.getByLabelText('Capex phasing')
    fireEvent.focus(phasing); fireEvent.change(phasing, { target: { value: '0.5, half' } }); fireEvent.blur(phasing)
    expect((await screen.findByTestId('fi-blocked')).textContent).toMatch(/Capex phasing/)
    expect((screen.getByRole('button', { name: 'Save the finance inputs' }) as HTMLButtonElement).disabled).toBe(true)
  })
})
