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

  it('a rebuilt template starts with empty draft inputs (a fresh dialog per build)', async () => {
    api.buildTemplate.mockResolvedValue({
      config: { template: 'landlord_tenant', participants: [
        { id: 'landlord', name: 'landlord', role: 'landlord' }, { id: 'site', name: 'site', role: 'tenant' }] },
      draft_contracts: [{ type: 'lease', id: 'lease_draft', lessor: 'landlord', lessee: 'site',
                          annual_payment: null, tenor_years: 10, asset_ids: ['pv'] }],
      notes: [] })
    renderDesigner()
    await screen.findByTestId('vf-designer')
    fireEvent.change(screen.getByLabelText('Template'), { target: { value: 'landlord_tenant' } })
    fireEvent.click(screen.getByRole('button', { name: 'Build' }))
    let dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('annual payment'), { target: { value: '50000' } })
    expect((within(dialog).getByLabelText('annual payment') as HTMLInputElement).value).toBe('50000')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'Build' }))
    dialog = await screen.findByRole('dialog')
    expect((within(dialog).getByLabelText('annual payment') as HTMLInputElement).value).toBe('')
  })

  it('says what is missing without a commercial config', async () => {
    const { NoCommercialConfigError } = await import('../../../api/commercial')
    api.getDesigner.mockRejectedValue(new NoCommercialConfigError('set it up'))
    renderDesigner()
    expect((await screen.findByTestId('vf-designer-unavailable')).textContent)
      .toContain('point of connection')
  })
})

describe('ParticipantsDesigner — review round 1', () => {
  it('saves after a Reload with the NEW digest and the new config (#1)', async () => {
    api.putValueFlows.mockRejectedValueOnce(new StaleEditError('changed'))
    renderDesigner()
    await screen.findByTestId('vf-designer')
    fireEvent.click(screen.getByRole('button', { name: 'Save participants' }))
    const stale = await screen.findByTestId('vf-stale')
    api.getValueFlows.mockResolvedValue({ digest: 'd9', status: 'ok', value_flows: {
      participants: [{ id: 'site', name: 'NEW', role: 'site_owner' }] } })
    fireEvent.click(within(stale).getByRole('button', { name: 'Reload' }))
    await waitFor(() => expect((screen.getByLabelText('Participant 1 name') as HTMLInputElement).value)
      .toBe('NEW'))
    api.putValueFlows.mockResolvedValue({ value_flows: null, digest: 'd10', status: 'ok' })
    fireEvent.click(screen.getByRole('button', { name: 'Save participants' }))
    await waitFor(() => expect(api.putValueFlows).toHaveBeenCalledTimes(2))
    expect(api.putValueFlows.mock.calls[1][1]).toBe('d9')
  })

  it('shows hub problems with no group contract and can remove the settings (#2)', async () => {
    api.getValueFlows.mockResolvedValue({ digest: 'd0', status: 'ok', value_flows: {
      participants: [{ id: 'site', name: 'site', role: 'site_owner' }],
      hub_members: [{ link: 'import', participant: 'site' }], allocation: { basis: 'energy' } } })
    api.putValueFlows.mockRejectedValue(Object.assign(new Error('422'), { response: { status: 422,
      data: { detail: { problems: ['hub_members need a group contract',
                                   'an allocation key needs a group contract'] } } } }))
    renderDesigner()
    await screen.findByTestId('vf-designer')
    fireEvent.click(screen.getByRole('button', { name: 'Save participants' }))
    const hub = await screen.findByRole('heading', { name: 'Energy hub' })
    await waitFor(() => expect(within(hub.parentElement!).getAllByRole('listitem').length).toBe(2))
    fireEvent.click(within(hub.parentElement!).getByRole('button', { name: 'Remove hub settings' }))
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Energy hub' })).toBeNull())
    // …and the problems stay visible (the fallback list).
    expect(screen.getAllByText(/need(s)? a group contract/).length).toBe(2)
  })

  it('says why when the state cannot be read (#3)', async () => {
    api.getValueFlows.mockRejectedValue(new Error('boom'))
    renderDesigner()
    expect((await screen.findByTestId('vf-designer-unavailable')).textContent)
      .toContain('could not be loaded')
  })

  it('invalidates the results after appending contracts (#4)', async () => {
    api.buildTemplate.mockResolvedValue({ config: { participants: [] },
      draft_contracts: [{ type: 'lease', id: 'l', annual_payment: null }], notes: [] })
    api.appendContracts.mockResolvedValue({} as never)
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const spy = vi.spyOn(qc, 'invalidateQueries')
    render(<QueryClientProvider client={qc}><ParticipantsDesigner /></QueryClientProvider>)
    await screen.findByTestId('vf-designer')
    fireEvent.click(screen.getByRole('button', { name: 'Build' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('annual payment'), { target: { value: '1' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save contracts and use' }))
    await waitFor(() => expect(spy.mock.calls.some(([f]) =>
      JSON.stringify(f?.queryKey).includes('results'))).toBe(true))
    expect(await screen.findByText(/1 contract\(s\) saved/)).toBeTruthy()
  })

  it('shows a kind rule as the payee and can remove it (#5)', async () => {
    api.getValueFlows.mockResolvedValue({ digest: 'd0', status: 'ok', value_flows: {
      participants: [{ id: 'site', name: 'site', role: 'site_owner' }],
      tariff_payees: [{ kind: 'energy', payee: 'dso' }] } })
    renderDesigner()
    await screen.findByTestId('vf-designer')
    const select = screen.getByLabelText('Payee of energy') as HTMLSelectElement
    expect(select.options[0].textContent).toBe('dso (the energy rule)')
    fireEvent.click(screen.getByRole('button', { name: 'Remove the energy payee rule' }))
    expect((screen.getByLabelText('Payee of energy') as HTMLSelectElement).options[0].textContent)
      .toBe('retailer (default)')
  })

  it('names a stored config that does not validate, keeps the site id, dedupes externals', async () => {
    api.getValueFlows.mockResolvedValue({ digest: 'd0', status: 'value_flows_invalid',
      message: 'participants: bad', value_flows: { participants: 'x' } as never })
    renderDesigner()
    expect((await screen.findByTestId('vf-stored-invalid')).textContent).toContain('participants: bad')
    expect((screen.getByLabelText('Participant 1 id') as HTMLInputElement).readOnly).toBe(true)
    fireEvent.change(screen.getByLabelText('New external'), { target: { value: ' RETAILER ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add external' }))
    expect(screen.getAllByRole('button', { name: /Remove external/i })
      .filter(b => /retailer/i.test(b.getAttribute('aria-label') ?? '')).length).toBe(1)
  })
})
