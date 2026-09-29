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
    fireEvent.change(screen.getByLabelText('Contract dr1 max events (optional)'.replace(' (optional)', '')),
                     { target: { value: '' } })
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
})
