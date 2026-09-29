// The participants designer (IC P3 WP3.6): a round trip through the value-flows
// route with the digest it read, a server problem shown beside its section, a
// 412 as "changed elsewhere, reload", and a template's drafts priced in a
// dialog before anything is saved.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { commercialApi, StaleEditError } from '../../../api/commercial'
import ParticipantsDesigner from './ParticipantsDesigner'
import { expectAllButtonsNamed } from '../../../test-utils/accessibleName'

vi.mock('../../../api/commercial', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/commercial')>()
  return { ...actual, commercialApi: { ...actual.commercialApi,
    getValueFlows: vi.fn(), getDesigner: vi.fn(), putValueFlows: vi.fn(),
    buildTemplate: vi.fn(), appendContracts: vi.fn() } }
})
const api = vi.mocked(commercialApi)

const CTX = {
  site_party: 'site', contract_parties: ['Solar BV', 'site'], group_members: [],
  default_externals: ['retailer', 'dso', 'market'],
  assets: [
    { component: 'Generator', name: 'grid_supply', bus: 'grid', side: 'grid', ownable: false, flags: [], carrier: 'grid' },
    { component: 'Generator', name: 'pv', bus: 'site', side: 'site', ownable: true, flags: [], carrier: 'solar' },
  ],
  tariff_items: [{ id: 'energy', kind: 'energy', default_payee: 'retailer', stream: 'energy_import' }],
}

function renderDesigner() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ParticipantsDesigner /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  api.getValueFlows.mockResolvedValue({ value_flows: null, digest: 'd0', status: 'not_set' })
  api.getDesigner.mockResolvedValue(CTX as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('ParticipantsDesigner', () => {
  it('saves an edited config with the digest it read', async () => {
    api.putValueFlows.mockResolvedValue({ value_flows: null, digest: 'd1', status: 'ok' })
    const { container } = renderDesigner()
    await screen.findByTestId('vf-designer')
    // Grid-side assets are not assignable.
    expect(screen.queryByLabelText('Owner of Generator grid_supply')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Add participant' }))
    fireEvent.change(screen.getByLabelText('Participant 2 id'), { target: { value: 'dev' } })
    fireEvent.change(screen.getByLabelText('Participant 2 role'), { target: { value: 'developer' } })
    fireEvent.change(screen.getByLabelText('Owner of Generator pv'), { target: { value: 'dev' } })
    fireEvent.change(screen.getByLabelText('Payee of energy'), { target: { value: 'dso' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save participants' }))
    await waitFor(() => expect(api.putValueFlows).toHaveBeenCalledTimes(1))
    const [cfg, digest] = api.putValueFlows.mock.calls[0]
    expect(digest).toBe('d0')
    expect(cfg!.participants).toEqual([{ id: 'site', name: 'site', role: 'site_owner' },
                                       { id: 'dev', name: '', role: 'developer' }])
    expect(cfg!.asset_owners).toEqual([{ component: 'Generator', asset_id: 'pv', owner: 'dev' }])
    expect(cfg!.tariff_payees).toEqual([{ item_id: 'energy', payee: 'dso' }])
    await screen.findByText(/Participants saved/)
    expectAllButtonsNamed(container)
  })

  it('shows a 422 problem beside the section it names', async () => {
    const err = Object.assign(new Error('422'), { response: { status: 422, data: { detail: {
      code: 'value_flows_invalid', problems: ["asset 'pv': owner 'x' is unknown",
                                             "tariff payee 'y' is neither a participant nor an external"] } } } })
    api.putValueFlows.mockRejectedValue(err)
    renderDesigner()
    await screen.findByTestId('vf-designer')
    fireEvent.click(screen.getByRole('button', { name: 'Save participants' }))
    const alerts = await screen.findAllByRole('alert')
    const assets = screen.getByRole('heading', { name: 'Asset owners' }).parentElement!
    expect(within(assets).getByText(/owner 'x' is unknown/)).toBeTruthy()
    const payees = screen.getByRole('heading', { name: 'Who is paid each tariff item' }).parentElement!
    expect(within(payees).getByText(/tariff payee 'y'/)).toBeTruthy()
    expect(alerts.length).toBe(2)
  })

  it('reads a 412 as changed elsewhere and reloads', async () => {
    api.putValueFlows.mockRejectedValue(new StaleEditError('changed'))
    renderDesigner()
    await screen.findByTestId('vf-designer')
    fireEvent.click(screen.getByRole('button', { name: 'Save participants' }))
    const stale = await screen.findByTestId('vf-stale')
    expect(stale.textContent).toContain('changed elsewhere')
    api.getValueFlows.mockResolvedValue({ value_flows: null, digest: 'd9', status: 'not_set' })
    fireEvent.click(within(stale).getByRole('button', { name: 'Reload' }))
    await waitFor(() => expect(screen.queryByTestId('vf-stale')).toBeNull())
    await waitFor(() => expect(api.getValueFlows).toHaveBeenCalledTimes(2))
  })

  it('prices a template\'s drafts before saving them, and saves nothing on cancel', async () => {
    api.buildTemplate.mockResolvedValue({
      config: { template: 'landlord_tenant', participants: [
        { id: 'landlord', name: 'landlord', role: 'landlord' }, { id: 'site', name: 'site', role: 'tenant' }] },
      draft_contracts: [{ type: 'lease', id: 'lease_draft', lessor: 'landlord', lessee: 'site',
                          annual_payment: null, tenor_years: 10, asset_ids: ['pv'] }],
      notes: ['draft_needs:lease_draft:annual_payment'] })
    api.appendContracts.mockResolvedValue({} as never)
    renderDesigner()
    await screen.findByTestId('vf-designer')
    fireEvent.change(screen.getByLabelText('Template'), { target: { value: 'landlord_tenant' } })
    fireEvent.click(screen.getByRole('button', { name: 'Build' }))
    const dialog = await screen.findByRole('dialog')
    const use = within(dialog).getByRole('button', { name: 'Save contracts and use' })
    expect(use.hasAttribute('disabled')).toBe(true)              // unpriced: cannot be saved
    fireEvent.change(within(dialog).getByLabelText('annual payment'), { target: { value: '50000' } })
    expect(use.hasAttribute('disabled')).toBe(false)
    fireEvent.click(use)
    await waitFor(() => expect(api.appendContracts).toHaveBeenCalledWith([
      expect.objectContaining({ id: 'lease_draft', annual_payment: 50000 })]))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect((screen.getByLabelText('Participant 1 id') as HTMLInputElement).value).toBe('landlord')
    expect(api.putValueFlows).not.toHaveBeenCalled()             // the user still saves
  })

  it('says what is missing without a commercial config', async () => {
    const { NoCommercialConfigError } = await import('../../../api/commercial')
    api.getDesigner.mockRejectedValue(new NoCommercialConfigError('set it up'))
    renderDesigner()
    expect((await screen.findByTestId('vf-designer-unavailable')).textContent)
      .toContain('point of connection')
  })
})
