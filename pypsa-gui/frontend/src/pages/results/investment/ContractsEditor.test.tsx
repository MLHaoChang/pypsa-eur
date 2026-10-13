// The contracts and connection-agreement editors (IC P3 WP3.7c): every P2
// contract type round-trips unchanged, an edit writes the model's field, a 422
// is shown at the contract it names, the Library pin is shown, and the
// connection agreement's capacity fee uses the tariff item editor.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { commercialApi, libraryApi } from '../../../api/commercial'
import ContractsEditor from './ContractsEditor'
import { expectAllButtonsNamed } from '../../../test-utils/accessibleName'

vi.mock('../../../api/commercial', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/commercial')>()
  return { ...actual,
    commercialApi: { ...actual.commercialApi, getCommercial: vi.fn(), getValueFlows: vi.fn(),
                     saveCommercial: vi.fn() },
    libraryApi: { ...actual.libraryApi, listSeries: vi.fn() } }
})
const api = vi.mocked(commercialApi)
const lib = vi.mocked(libraryApi)
const SERIES = { id: 'da', version: 1, hash: 'h', source: 'upload' }
const PIN = { kind: 'contract' as const, id: 'ppa_lib', version: 3, hash: 'x' }
// One of each P2 type, in the shapes the P2 models hold.
const CONTRACTS = [
  { type: 'ppa', id: 'ppa1', kind: 'pay_as_produced', price: 60, tenor_years: 15, seller: 'Solar BV',
    buyer: 'site', asset_ids: ['pv'], pricing: 'market_plus_premium', premium_eur_per_mwh: -2.5,
    reference_price: SERIES, indexation_pct_per_year: 2, base_year: 2030, library_ref: PIN },
  { type: 'cfd', id: 'cfd1', strike: 70, tenor_years: 10, asset_ids: ['pv'], counterparty: 'gov',
    reference: 'monthly_capture', suspend_on_negative_price: true },
  { type: 'dr', id: 'dr1', availability_eur_per_mw_year: 1000, activation_eur_per_mwh: 100,
    load_ids: ['site_load'], counterparty: 'dso', contracted_mw: 2, max_events: 20 },
  { type: 'lease', id: 'lease1', lessor: 'Leasing GmbH', lessee: 'site', annual_payment: 120000,
    tenor_years: 10, asset_ids: ['bess'] },
  { type: 'eaas', id: 'eaas1', provider: 'Heat Co', customer: 'site', fee_eur_per_mwh: 12,
    tenor_years: 8, asset_ids: ['bess'] },
  { type: 'retail', id: 'retail1', retailer: 'Energie AG', customer: 'site', tariff_id: 't', tenor_years: 2 },
]
const CONNECTION = { kind: 'firm', import_cap_mw: 70, available_from: '2030-01-01',
  capacity_fee: { id: 'fee', kind: 'capacity', unit: 'per_kw_year', periods: [{ name: 'all', rate: 45 }] } }

function renderEditor() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ContractsEditor /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  api.getCommercial.mockResolvedValue({ poc_link: 'import', site_party: 'site',
    contracts: structuredClone(CONTRACTS), connection: structuredClone(CONNECTION) } as never)
  api.getValueFlows.mockResolvedValue({ value_flows: null, digest: 'd', status: 'not_set' })
  lib.listSeries.mockResolvedValue([SERIES])
  api.saveCommercial.mockResolvedValue({} as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('ContractsEditor', () => {
  it('round-trips every P2 contract type unchanged and shows the Library pin', async () => {
    const { container } = renderEditor()
    await screen.findByTestId('ce-contract-5')
    expect(screen.getByTestId('ce-contract-0').textContent).toContain('Copied from Library item ppa_lib v3')
    // Type-specific fields: the premium only under market_plus_premium.
    expect(screen.getByLabelText('Contract ppa1 premium (per MWh)')).toBeTruthy()
    expect(screen.queryByLabelText('Contract ppa1 sleeving party')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Save the contracts' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalledWith({ contracts: CONTRACTS }))
    expectAllButtonsNamed(container)
  })

  it('an edit writes the field; clearing an optional field removes it', async () => {
    renderEditor()
    await screen.findByTestId('ce-contract-0')
    fireEvent.change(screen.getByLabelText('Contract lease1 annual payment'), { target: { value: '99000' } })
    fireEvent.change(screen.getByLabelText('Contract dr1 max events'), { target: { value: '' } })
    const ids = screen.getByLabelText('Contract lease1 assets')
    fireEvent.focus(ids); fireEvent.change(ids, { target: { value: 'bess, pv' } }); fireEvent.blur(ids)
    fireEvent.click(screen.getByRole('button', { name: 'Save the contracts' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalled())
    const saved = (api.saveCommercial.mock.calls[0][0] as { contracts: typeof CONTRACTS }).contracts
    expect(saved[3]).toMatchObject({ annual_payment: 99000, asset_ids: ['bess', 'pv'] })
    expect('max_events' in saved[2]).toBe(false)
  })

  it('adds a contract of a chosen type with the site as the buyer', async () => {
    renderEditor()
    await screen.findByTestId('ce-contract-5')
    fireEvent.change(screen.getByLabelText('New contract type'), { target: { value: 'lease' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add contract' }))
    const added = screen.getByTestId('ce-contract-6')
    expect((within(added).getByLabelText('Contract lease_7 lessee') as HTMLInputElement).value).toBe('site')
  })

  it('shows a 422 at the contract it names', async () => {
    api.saveCommercial.mockRejectedValue(Object.assign(new Error('422'), { response: { status: 422,
      data: { detail: [{ loc: ['body', 'commercial', 'contracts', 0, 'ppa'],
                         msg: 'Value error, a baseload PPA settles price against the reference; market_plus_premium is refused' }] } } }))
    renderEditor()
    await screen.findByTestId('ce-contract-0')
    fireEvent.change(screen.getByLabelText('Contract ppa1 kind'), { target: { value: 'baseload' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the contracts' }))
    const alert = await within(screen.getByTestId('ce-contract-0')).findByRole('alert')
    expect(alert.textContent).toContain('market_plus_premium is refused')
    expect(within(screen.getByTestId('ce-contract-1')).queryByRole('alert')).toBeNull()
  })

  it('edits the connection agreement and its capacity fee through the item editor', async () => {
    renderEditor()
    const conn = await screen.findByTestId('ce-connection')
    fireEvent.change(within(conn).getByLabelText('Import cap (MW)'), { target: { value: '60' } })
    fireEvent.change(within(conn).getByLabelText('Item fee period 1 rate'), { target: { value: '50' } })
    fireEvent.change(within(conn).getByLabelText('Envelope series'), { target: { value: 'da@1' } })
    fireEvent.click(within(conn).getByRole('button', { name: 'Save the connection agreement' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalled())
    const saved = (api.saveCommercial.mock.calls[0][0] as { connection: typeof CONNECTION & { envelope: unknown } }).connection
    expect(saved.import_cap_mw).toBe(60)
    expect(saved.capacity_fee.periods[0].rate).toBe(50)
    expect(saved.envelope).toEqual(SERIES)
  })

  // ── WP3.7c review round 1 ────────────────────────────────────────────────
  it('drops a field its kind or pricing no longer shows (#1)', async () => {
    api.getCommercial.mockResolvedValue({ poc_link: 'import', site_party: 'site', contracts: [
      { ...CONTRACTS[0], kind: 'sleeved', sleeving_party: 'Sleever', sleeving_fee_eur_per_mwh: 3,
        floor: 10, cap: 90 }] } as never)
    renderEditor()
    await screen.findByTestId('ce-contract-0')
    expect(screen.getByLabelText('Contract ppa1 sleeving party')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Contract ppa1 kind'), { target: { value: 'pay_as_produced' } })
    fireEvent.change(screen.getByLabelText('Contract ppa1 pricing'), { target: { value: 'fixed' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the contracts' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalled())
    const [saved] = (api.saveCommercial.mock.calls[0][0] as unknown as { contracts: Array<Record<string, unknown>> }).contracts
    for (const k of ['sleeving_party', 'sleeving_fee_eur_per_mwh', 'premium_eur_per_mwh', 'floor', 'cap']) {
      expect(k in saved).toBe(false)
    }
    expect(saved).toMatchObject({ kind: 'pay_as_produced', pricing: 'fixed', seller: 'Solar BV' })
  })

  it('refuses a blank required party before the server sees it (#2)', async () => {
    renderEditor()
    await screen.findByTestId('ce-contract-5')
    fireEvent.click(screen.getByRole('button', { name: 'Add contract' }))           // a PPA, no seller
    fireEvent.click(screen.getByRole('button', { name: 'Save the contracts' }))
    const alert = await within(screen.getByTestId('ce-contract-6')).findByRole('alert')
    expect(alert.textContent).toContain('seller: required')
    expect(api.saveCommercial).not.toHaveBeenCalled()
    expect(within(screen.getByTestId('ce-contract-0')).queryByRole('alert')).toBeNull()
  })

  it('shows an envelope the Library no longer lists as such, and can clear it (#3)', async () => {
    api.getCommercial.mockResolvedValue({ poc_link: 'import', site_party: 'site', contracts: [],
      connection: { ...CONNECTION, kind: 'non_firm_dynamic', envelope: { ...SERIES, version: 0, hash: 'old' } } } as never)
    renderEditor()
    const conn = await screen.findByTestId('ce-connection')
    const env = within(conn).getByLabelText('Envelope series') as HTMLSelectElement
    expect(env.value).toBe('da@0')
    expect(env.selectedOptions[0].textContent).toBe('da v0 (not listed)')
    fireEvent.change(env, { target: { value: '' } })
    fireEvent.click(within(conn).getByRole('button', { name: 'Save the connection agreement' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalled())
    expect((api.saveCommercial.mock.calls[0][0] as { connection: { envelope: unknown } }).connection.envelope)
      .toBeNull()
  })

  it('keeps the connection agreement when the server refuses its removal (#4)', async () => {
    api.saveCommercial.mockRejectedValue(Object.assign(new Error('409'), { response: { status: 409,
      data: { detail: { code: 'solver_in_flight', message: 'a solve is running' } } } }))
    renderEditor()
    const conn = await screen.findByTestId('ce-connection')
    fireEvent.click(within(conn).getByRole('button', { name: 'Remove the connection agreement' }))
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('not saved'))
    expect(screen.getByTestId('ce-connection')).toBeTruthy()
    api.saveCommercial.mockResolvedValue({} as never)
    fireEvent.click(within(screen.getByTestId('ce-connection'))
      .getByRole('button', { name: 'Remove the connection agreement' }))
    await waitFor(() => expect(screen.queryByTestId('ce-connection')).toBeNull())
    expect(api.saveCommercial).toHaveBeenLastCalledWith({ connection: null })
  })

  it('shows an untagged P0-era contract by its fields and an unknown type read-only (#5)', async () => {
    const legacy = { id: 'old_cfd', strike: 55, tenor_years: 5, asset_ids: ['pv'] }
    const odd = { type: 'swap', id: 'x1', notional: 3 }
    api.getCommercial.mockResolvedValue({ poc_link: 'import', site_party: 'site',
      contracts: [legacy, odd] } as never)
    renderEditor()
    const first = await screen.findByTestId('ce-contract-0')
    expect(first.querySelector('legend')!.textContent).toBe('CFD old_cfd')
    expect((within(first).getByLabelText('Contract old_cfd strike (per MWh)') as HTMLInputElement).value).toBe('55')
    expect(screen.getByTestId('ce-contract-1').textContent).toContain('kept as stored')
    fireEvent.click(screen.getByRole('button', { name: 'Save the contracts' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalledWith(
      { contracts: [{ ...legacy, type: 'cfd' }, odd] }))
  })

  it('sends an integer field as typed, never truncated (#6)', async () => {
    renderEditor()
    await screen.findByTestId('ce-contract-0')
    fireEvent.change(screen.getByLabelText('Contract lease1 tenor (years)'), { target: { value: '2.5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the contracts' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalled())
    expect((api.saveCommercial.mock.calls[0][0] as { contracts: Array<{ tenor_years: number }> })
      .contracts[3].tenor_years).toBe(2.5)
  })

  it('clears positional errors when a contract is removed (#7)', async () => {
    api.saveCommercial.mockRejectedValue(Object.assign(new Error('422'), { response: { status: 422,
      data: { detail: [{ loc: ['body', 'commercial', 'contracts', 1, 'cfd', 'strike'], msg: 'bad strike' }] } } }))
    renderEditor()
    await screen.findByTestId('ce-contract-1')
    fireEvent.click(screen.getByRole('button', { name: 'Save the contracts' }))
    expect((await within(screen.getByTestId('ce-contract-1')).findByRole('alert')).textContent)
      .toContain('strike: bad strike')
    fireEvent.click(screen.getByRole('button', { name: 'Remove Contract ppa1' }))
    await waitFor(() => expect(screen.queryAllByRole('alert')).toHaveLength(0))
  })

  it('shows a connection refusal at the connection agreement (#8) and a binding one at its contract (#9)', async () => {
    api.saveCommercial.mockRejectedValueOnce(Object.assign(new Error('422'), { response: { status: 422,
      data: { detail: [{ loc: ['body', 'commercial', 'connection'], msg: 'Value error, needs an envelope' }] } } }))
    renderEditor()
    const conn = await screen.findByTestId('ce-connection')
    fireEvent.click(within(conn).getByRole('button', { name: 'Save the connection agreement' }))
    expect((await within(conn).findByRole('alert')).textContent).toBe('Value error, needs an envelope')
    api.saveCommercial.mockRejectedValueOnce(Object.assign(new Error('422'), { response: { status: 422,
      data: { detail: { code: 'commercial_binding_invalid',
                        message: "ppa contract 'ppa1': asset 'ghost' is not a generator" } } } }))
    fireEvent.click(screen.getByRole('button', { name: 'Save the contracts' }))
    expect((await within(screen.getByTestId('ce-contract-0')).findByRole('alert')).textContent)
      .toContain("asset 'ghost'")
  })

  it('refuses to overwrite contracts changed since they were loaded (#10)', async () => {
    renderEditor()
    await screen.findByTestId('ce-contract-5')
    api.getCommercial.mockResolvedValue({ poc_link: 'import', site_party: 'site',
      contracts: [...structuredClone(CONTRACTS), { ...CONTRACTS[3], id: 'lease_from_chat' }] } as never)
    fireEvent.click(screen.getByRole('button', { name: 'Save the contracts' }))
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('changed since they were loaded'))
    expect(api.saveCommercial).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Reload the contracts' }))
    await screen.findByTestId('ce-contract-6')
    fireEvent.click(screen.getByRole('button', { name: 'Save the contracts' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalledTimes(1))
  })

  it('starts a new agreement with no import cap and offers its fee only as a capacity item (#11, #12)', async () => {
    api.getCommercial.mockResolvedValue({ poc_link: 'import', site_party: 'site', contracts: [] } as never)
    renderEditor()
    fireEvent.click(await screen.findByRole('button', { name: 'Add a connection agreement' }))
    const conn = await screen.findByTestId('ce-connection')
    expect((within(conn).getByLabelText('Import cap (MW)') as HTMLInputElement).value).toBe('')
    fireEvent.click(within(conn).getByRole('button', { name: 'Add a capacity fee' }))
    const kind = within(conn).getByLabelText('Item capacity_fee kind') as HTMLSelectElement
    const unit = within(conn).getByLabelText('Item capacity_fee unit') as HTMLSelectElement
    // Only what the connection's fee supports (a per_kwh unit failed at the solve; round 2).
    expect([...kind.options].map(o => o.value)).toEqual(['capacity'])
    expect([...unit.options].map(o => o.value)).toEqual(['per_kw_year', 'per_kw_month'])
    fireEvent.change(unit, { target: { value: 'per_kw_month' } })
    fireEvent.click(within(conn).getByRole('button', { name: 'Save the connection agreement' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalled())
    const saved = (api.saveCommercial.mock.calls[0][0] as { connection: { import_cap_mw: unknown;
      capacity_fee: { kind: string; unit: string } } }).connection
    expect(saved.import_cap_mw).toBeNull()
    expect(saved.capacity_fee).toMatchObject({ kind: 'capacity', unit: 'per_kw_month' })
  })

  it('keeps an untagged contract its type while a field is cleared (round 2)', async () => {
    api.getCommercial.mockResolvedValue({ poc_link: 'import', site_party: 'site', contracts: [
      { id: 'old', provider: 'Heat Co', customer: 'site', fee_eur_per_mwh: 12, tenor_years: 8,
        asset_ids: ['bess'] }] } as never)
    renderEditor()
    const box = await screen.findByTestId('ce-contract-0')
    fireEvent.change(within(box).getByLabelText('Contract old fee (per MWh)'), { target: { value: '' } })
    expect(screen.getByTestId('ce-contract-0').querySelector('legend')!.textContent).toBe('EAAS old')
  })
})
