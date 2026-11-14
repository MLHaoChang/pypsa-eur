// The business-case line table (business-case 09): the Unit select offers only
// what the quantity source allows, every refusal sits on its row and column,
// a spreadsheet paste fills the per-year grid, the preset applies and saves with
// If-Match, and a 412 asks for a reload.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  businessCaseApi, BusinessCaseRejected, StaleEditError,
  type BusinessCaseConfig, type BusinessCaseOptions, type LineItem,
} from '../../../api/businessCase'
import BusinessCaseLines from './BusinessCaseLines'
import { expectAllButtonsNamed } from '../../../test-utils/accessibleName'

vi.mock('../../../api/businessCase', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/businessCase')>()
  return { ...actual, businessCaseApi: {
    getInputs: vi.fn(), putInputs: vi.fn(), getOptions: vi.fn(), getPreset: vi.fn(), getStudy: vi.fn(),
  } }
})
const api = vi.mocked(businessCaseApi)

const OPTS: BusinessCaseOptions = {
  template_id: 'eh_h2_hub',
  variants: { merchant: 'Merchant hub' },
  offered_rows: [{ label: 'Staff', units: ['unit'], sources: ['typed'],
    line: { name: 'Staff', kind: 'opex', category: 'staff', unit: 'unit', quantity_source: 'typed', escalation: 'opex' } }],
  categories: { revenue: ['product_sale', 'by_product'], opex: ['staff', 'maintenance'], capex: ['facility'] },
  escalations: ['opex', 'capex', 'contract'],
  units_by_source: {
    typed: { revenue: ['MWh', 'kg', 't'], opex: ['MWh', 'kg', 't', 'unit'], capex: ['unit', 'lump'] },
    share_of: { revenue: ['share'], opex: ['share'], capex: ['share'] },
    linked: { 'served_h2.h2_load': { revenue: ['MWh_H2', 'kg'], opex: ['MWh_H2', 'kg'], capex: ['MWh_H2', 'kg'] } },
  },
  catalogue_version: '1',
  outputs: [{ id: 'served_h2.h2_load', label: 'H2 served to h2_load', units: ['MWh_H2', 'kg'], derived: false }],
  outputs_reason: null,
  horizon: [2030, 2032],
  base_year: 2030,
  refusals: [],
  refusals_reason: null,
}

const H2: LineItem = {
  name: 'H2 sales', kind: 'revenue', category: 'product_sale', unit: 'kg',
  quantity_source: 'linked:served_h2.h2_load@1', escalation: 'opex',
  hint: { low: 3, high: 6, unit: 'EUR/kg', source: 'Hydrogen bank auction', year: 2024 },
}
const CAPEX: LineItem = {
  name: 'Plant', kind: 'capex', category: 'facility', unit: 'lump', quantity_source: 'typed',
  quantity: 1, price: 5e6, escalation: 'capex', life_years: 20,
  assumed: { price: { key: 'k', value: 5e6, unit: 'EUR', source: 'Defaults pack', source_year: 2025, pack: 'p', status: 'assumed' } },
}
const config = (...lines: LineItem[]): BusinessCaseConfig => ({ inputs: { lines } })

function mount(cfg: BusinessCaseConfig | null, opts: Partial<BusinessCaseOptions> = {}) {
  api.getInputs.mockResolvedValue({ business_case: cfg, digest: 'd1', status: cfg ? 'ok' : 'not_set' })
  api.getOptions.mockResolvedValue({ ...OPTS, ...opts })
  api.getStudy.mockResolvedValue(null)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><BusinessCaseLines /></QueryClientProvider>)
}
const optionsOf = (el: HTMLElement) => within(el).getAllByRole('option').map(o => (o as HTMLOptionElement).value)

beforeEach(() => { vi.clearAllMocks() })
afterEach(() => cleanup())

describe('the Unit select', () => {
  it('offers a linked line its output’s units only', async () => {
    mount(config(H2))
    const unit = await screen.findByLabelText('Unit, H2 sales')
    expect(optionsOf(unit)).toEqual(['MWh_H2', 'kg'])
    expect(screen.getByTestId('bc-from-model')).toBeTruthy()
  })
  it('offers a typed line its kind’s units, and share for a share', async () => {
    mount(config(CAPEX, { name: 'Maint', kind: 'opex', category: 'maintenance', unit: 'share',
                          quantity_source: 'share_of:capex', quantity: 0.02, escalation: 'opex' }))
    expect(optionsOf(await screen.findByLabelText('Unit, Plant'))).toEqual(['unit', 'lump'])
    expect(optionsOf(screen.getByLabelText('Unit, Maint'))).toEqual(['share'])
  })
  it('switching the source to typed picks a unit the kind allows', async () => {
    mount(config(H2))
    fireEvent.change(await screen.findByLabelText('Quantity from, H2 sales'), { target: { value: 'typed' } })
    const unit = screen.getByLabelText('Unit, H2 sales') as HTMLSelectElement
    expect(optionsOf(unit)).toEqual(['MWh', 'kg', 't'])
    expect(unit.value).toBe('kg')
  })
})

describe('refusals sit on their row and column (G9)', () => {
  it('places the stored lines’ refusals, in words', async () => {
    mount(config(H2, CAPEX), { refusals: [
      { code: 'business_case_refused', refusal: 'price_not_established', field: 'lines[0].price', message: 'x' },
      { code: 'business_case_refused', refusal: 'life_missing', field: 'lines[1].life_years', message: 'x' },
    ] })
    const rows = await screen.findAllByTestId('bc-row-refusals')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toMatch(/^Price: not established/)
    expect(rows[1].textContent).toMatch(/^Life \(years\): needed for capex/)
    expect(screen.getByLabelText('Price, H2 sales').getAttribute('aria-invalid')).toBe('true')
    // A refusal on a detail column offers the way to it on its own line.
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Show details' }))
    expect(screen.getByLabelText('Life (years), Plant')).toBeTruthy()
  })
  it('hides them while there are unsaved edits, and says why', async () => {
    mount(config(H2), { refusals: [
      { code: 'business_case_refused', refusal: 'price_not_established', field: 'lines[0].price', message: 'x' },
    ] })
    await screen.findByTestId('bc-row-refusals')
    fireEvent.change(screen.getByLabelText('Price, H2 sales'), { target: { value: '4' } })
    expect(screen.queryByTestId('bc-row-refusals')).toBeNull()
    expect(screen.getByTestId('bc-unsaved')).toBeTruthy()
  })
  it('places a 422 from the save on its cell', async () => {
    mount(config(CAPEX))
    fireEvent.change(await screen.findByLabelText('Price, Plant'), { target: { value: '6000000' } })
    api.putInputs.mockRejectedValue(new BusinessCaseRejected({ code: 'business_case_inputs_invalid',
      errors: [{ loc: ['body', 'business_case', 'inputs', 'lines', '0', 'life_years'], msg: 'Input should be greater than or equal to 1' }] }))
    fireEvent.click(screen.getByRole('button', { name: 'Save the lines' }))
    const row = await screen.findByTestId('bc-row-refusals')
    expect(row.textContent).toMatch(/^Life \(years\): Input should be/)
  })
  it('says the lines are checked once the investment case has run', async () => {
    mount(config(H2), { refusals: null, refusals_reason: 'investment_case_missing' })
    expect((await screen.findByTestId('bc-refusals-reason')).textContent).toMatch(/Run the investment case/)
  })
  it('a busy model reads busy, not out of date (PG-09 Y1)', async () => {
    mount(config(H2), { refusals: null, refusals_reason: 'network_busy' })
    const text = (await screen.findByTestId('bc-refusals-reason')).textContent ?? ''
    expect(text).toMatch(/^The model is busy/)
    expect(text).not.toMatch(/out of date/)
  })
})

describe('revenue prices', () => {
  it('an empty revenue price reads not established, with the public range beside it, never filled', async () => {
    mount(config(H2))
    expect(await screen.findByTestId('bc-not-established')).toBeTruthy()
    expect(screen.getByTestId('bc-hint').textContent).toBe('Public range 3–6 EUR per kg · Hydrogen bank auction, 2024')
    expect((screen.getByLabelText('Price, H2 sales') as HTMLInputElement).value).toBe('')
  })
})

describe('the per-year grid', () => {
  it('pastes a spreadsheet row from the cell into the years, never text as 0', async () => {
    mount(config(CAPEX))
    fireEvent.click(await screen.findByRole('button', { name: 'Show details of Plant' }))
    const first = screen.getByLabelText('Price 2030, Plant')
    fireEvent.paste(first, { clipboardData: { getData: () => '100\tabc\t300\t400' } })
    expect((screen.getByLabelText('Price 2030, Plant') as HTMLInputElement).value).toBe('100')
    expect((screen.getByLabelText('Price 2031, Plant') as HTMLInputElement).value).toBe('')
    expect((screen.getByLabelText('Price 2032, Plant') as HTMLInputElement).value).toBe('300')
    expect(screen.getByTestId('bc-paste-note').textContent)
      .toBe('2 pasted · 1 not a number (2031), left as they were · 1 past 2032, not used')
  })
})

describe('preset, add, save', () => {
  it('applies the preset, then saves it with If-Match', async () => {
    mount(null)
    api.getPreset.mockResolvedValue({ template_id: 'eh_h2_hub', variants: OPTS.variants,
      business_case: { inputs: { preset: 'eh_h2_hub.merchant@1', lines: [H2, CAPEX] } } })
    fireEvent.click(await screen.findByRole('button', { name: 'Apply preset' }))
    await screen.findByLabelText('Unit, H2 sales')
    expect(api.getPreset).toHaveBeenCalledWith('merchant')
    api.putInputs.mockResolvedValue({ business_case: config(H2, CAPEX), digest: 'd2', status: 'ok' })
    fireEvent.click(screen.getByRole('button', { name: 'Save the lines' }))
    await waitFor(() => expect(api.putInputs).toHaveBeenCalled())
    const [body, ifMatch] = api.putInputs.mock.calls[0]
    expect(ifMatch).toBe('d1')
    expect(body!.inputs.lines.map(l => l.name)).toEqual(['H2 sales', 'Plant'])
    expect(await screen.findByText('Lines saved.')).toBeTruthy()
  })
  it('a preset replaces the lines only: the receivable days stay', async () => {
    mount({ inputs: { receivable_days: 45, lines: [] } })
    api.getPreset.mockResolvedValue({ template_id: 'eh_h2_hub', variants: OPTS.variants,
      business_case: { inputs: { preset: 'eh_h2_hub.merchant@1', lines: [CAPEX] } } })
    fireEvent.click(await screen.findByRole('button', { name: 'Apply preset' }))
    await screen.findByLabelText('Unit, Plant')
    api.putInputs.mockResolvedValue({ business_case: config(CAPEX), digest: 'd2', status: 'ok' })
    fireEvent.click(screen.getByRole('button', { name: 'Save the lines' }))
    await waitFor(() => expect(api.putInputs).toHaveBeenCalled())
    expect(api.putInputs.mock.calls[0][0]!.inputs).toMatchObject({ receivable_days: 45, preset: 'eh_h2_hub.merchant@1' })
  })
  it('a linked line reads in words when the outputs are not offered, never the engine id', async () => {
    mount(config(H2), { outputs: null, outputs_reason: 'no_current_solve' })
    const src = await screen.findByLabelText('Quantity from, H2 sales') as HTMLSelectElement
    expect(src.selectedOptions[0].textContent).toBe('From the model (outputs not offered now)')
    expect(screen.getByTestId('bc-outputs-reason').textContent).toMatch(/no current solve; run the optimisation/)
  })
  it('asks before a preset replaces lines', async () => {
    mount(config(CAPEX))
    fireEvent.click(await screen.findByRole('button', { name: 'Apply preset' }))
    expect(screen.getByTestId('bc-preset-confirm').textContent).toMatch(/Replace the 1 line with the preset/)
    expect(api.getPreset).not.toHaveBeenCalled()
  })
  it('adds an offered row with a unique name, and a blank line', async () => {
    mount(config({ ...CAPEX, name: 'Staff', kind: 'opex', category: 'staff', unit: 'unit', assumed: {} }))
    fireEvent.change(await screen.findByTestId('bc-add'), { target: { value: '0' } })
    expect(screen.getByLabelText('Unit, Staff 2')).toBeTruthy()
    fireEvent.change(screen.getByTestId('bc-add'), { target: { value: 'blank:revenue' } })
    expect((screen.getByLabelText('Category, New line 1') as HTMLSelectElement).value).toBe('product_sale')
  })
  it('the preset needs the finance inputs: the refusal links to them', async () => {
    mount(null)
    api.getPreset.mockRejectedValue(new BusinessCaseRejected({ code: 'finance_inputs_missing', message: 'x' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Apply preset' }))
    expect((await screen.findByTestId('bc-open-finance')).textContent).toBe('Finance inputs ›')
  })
  it('discarding asks first and names what is lost', async () => {
    mount(config(CAPEX))
    fireEvent.change(await screen.findByLabelText('Price, Plant'), { target: { value: '1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }))
    expect(screen.getByTestId('bc-discard-confirm').textContent).toMatch(/^Discard the unsaved changes to the lines\?/)
    expect(api.getInputs).toHaveBeenCalledTimes(1)
  })
  it('a 412 asks for a reload', async () => {
    mount(config(CAPEX))
    fireEvent.change(await screen.findByLabelText('Price, Plant'), { target: { value: '1' } })
    api.putInputs.mockRejectedValue(new StaleEditError('changed'))
    fireEvent.click(screen.getByRole('button', { name: 'Save the lines' }))
    expect(await screen.findByTestId('bc-stale')).toBeTruthy()
  })
  it('an edit to an assumed value makes it stated', async () => {
    mount(config(CAPEX))
    expect((await screen.findByTestId('bc-provenance')).textContent).toBe('Assumed')
    fireEvent.change(screen.getByLabelText('Price, Plant'), { target: { value: '1' } })
    expect(screen.getByTestId('bc-provenance').textContent).toBe('Stated')
  })
  it('names every button', async () => {
    mount(config(H2, CAPEX))
    await screen.findByLabelText('Unit, H2 sales')
    expectAllButtonsNamed(document.body)
  })
})
