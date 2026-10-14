// M5b (plan 2026-10-14-m5-non-asset-forms.md): Solver Settings' global
// constraints are added and edited through one form rendered from the asset
// schema. Each type shows the fields it reads, a field it does not read saves
// null (no stale "co2_emissions" on an operational limit), and a row can be
// edited in place, where before it could only be deleted and re-added.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { GlobalConstraints } from './SolverSettings'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getGlobalConstraints: vi.fn(),
      getInvestmentPeriods: vi.fn(),
      getCarriers: vi.fn(),
      createGlobalConstraint: vi.fn(async () => ({ name: 'x' })),
      updateGlobalConstraint: vi.fn(async () => ({ name: 'x' })),
    },
  }
})

const CO2_CAP = { name: 'co2_cap', type: 'primary_energy', sense: '<=', constant: 100_000_000,
  carrier_attribute: 'co2_emissions', carrier: '', investment_period: null }

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><GlobalConstraints /></QueryClientProvider>)
}

const typeSelect = () => screen.getByLabelText(/^Type/) as HTMLSelectElement

beforeEach(() => {
  vi.mocked(networkApi.getGlobalConstraints).mockReset().mockResolvedValue([CO2_CAP])
  vi.mocked(networkApi.getInvestmentPeriods).mockReset().mockResolvedValue({ periods: [] } as never)
  vi.mocked(networkApi.getCarriers).mockReset().mockResolvedValue([
    { name: 'gas', co2_emissions: 0.2, color: '', nice_name: '', unit: '' },
  ])
  vi.mocked(networkApi.createGlobalConstraint).mockClear()
  vi.mocked(networkApi.updateGlobalConstraint).mockClear()
  useUIStore.setState({ currentProject: 'Demo' })
})
afterEach(() => { useUIStore.setState({ currentProject: null }) })

describe('the global constraint form from the asset schema (M5b)', () => {
  it('offers PyPSA’s five types and three senses, with the descriptors’ labels', async () => {
    renderSection()
    await screen.findByText('Add constraint')
    await waitFor(() => expect(typeSelect().options.length).toBeGreaterThan(1))
    expect(Array.from(typeSelect().options).map(o => o.value).filter(Boolean)).toEqual([
      'primary_energy', 'tech_capacity_expansion_limit', 'operational_limit',
      'transmission_volume_expansion_limit', 'transmission_expansion_cost_limit'])
    const sense = screen.getByLabelText(/^Sense/) as HTMLSelectElement
    expect(Array.from(sense.options).map(o => o.value).filter(Boolean)).toEqual(['<=', '==', '>='])
    expect(screen.getByLabelText(/^Constant/)).toBeTruthy()
  })

  it('shows the summed column for primary energy and the carrier for a carrier-scoped limit', async () => {
    renderSection()
    await screen.findByText('Add constraint')
    expect(await screen.findByLabelText('Carrier attribute')).toBeTruthy()
    expect(screen.queryByText('— pick a carrier —')).toBeNull()
    fireEvent.change(typeSelect(), { target: { value: 'operational_limit' } })
    expect(screen.queryByLabelText('Carrier attribute')).toBeNull()
    expect(screen.getByText('— pick a carrier —')).toBeTruthy()
    fireEvent.change(typeSelect(), { target: { value: 'transmission_volume_expansion_limit' } })
    expect(screen.queryByLabelText('Carrier attribute')).toBeNull()
    expect(screen.queryByText('— pick a carrier —')).toBeNull()
  })

  it('an operational limit saves its carrier and no carrier attribute', async () => {
    renderSection()
    await screen.findByText('Add constraint')
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'gas_cap' } })
    fireEvent.change(typeSelect(), { target: { value: 'operational_limit' } })
    fireEvent.change(screen.getByLabelText(/^Constant/), { target: { value: '40' } })
    const carrier = screen.getByText('— pick a carrier —').closest('select') as HTMLSelectElement
    await waitFor(() => expect(Array.from(carrier.options).some(o => o.value === 'gas')).toBe(true))
    fireEvent.change(carrier, { target: { value: 'gas' } })
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(networkApi.createGlobalConstraint).toHaveBeenCalled())
    expect(vi.mocked(networkApi.createGlobalConstraint).mock.calls[0][0]).toEqual({
      name: 'gas_cap', type: 'operational_limit', sense: '<=', constant: 40,
      carrier: 'gas', carrier_attribute: null, investment_period: null,
    })
  })

  it('a constraint without a name is not sent', async () => {
    renderSection()
    await screen.findByText('Add constraint')
    await screen.findByLabelText('Name')
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    expect(networkApi.createGlobalConstraint).not.toHaveBeenCalled()
  })

  it('a row is edited in place: seeded from the row, saved under its name', async () => {
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Edit constraint co2_cap' }))
    expect(await screen.findByText('Edit co2_cap')).toBeTruthy()
    const constant = await screen.findByLabelText(/^Constant/) as HTMLInputElement
    await waitFor(() => expect(constant.value).toBe('100000000'))
    expect((screen.getByLabelText('Carrier attribute') as HTMLInputElement).value).toBe('co2_emissions')
    fireEvent.change(constant, { target: { value: '50000000' } })
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(networkApi.updateGlobalConstraint).toHaveBeenCalled())
    const [name, body] = vi.mocked(networkApi.updateGlobalConstraint).mock.calls[0]
    expect(name).toBe('co2_cap')
    expect(body).toMatchObject({ name: 'co2_cap', type: 'primary_energy', constant: 50_000_000,
      carrier_attribute: 'co2_emissions', carrier: null })
  })

  it('the CO2 preset fills the add form', async () => {
    renderSection()
    await screen.findByText('Add constraint')
    await userEvent.click(screen.getByRole('button', { name: /CO2 cap preset/ }))
    expect((await screen.findByLabelText('Name') as HTMLInputElement).value).toBe('co2_cap')
    expect((screen.getByLabelText(/^Constant/) as HTMLInputElement).value).toBe('100000000')
  })
})
