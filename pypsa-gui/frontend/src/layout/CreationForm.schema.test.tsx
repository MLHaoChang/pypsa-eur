// S1d-2: the creation form takes a described field's label, unit and scale
// from the asset schema (services/asset_schema/fields.py), as the Expert card
// does. It shows display units (€/kW, %) and posts stored ones (€/MW, a share);
// its defaults, and the defaults a choice snaps in, are written in stored units
// and shown converted.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import CreationForm from './CreationForm'
import { networkApi } from '../api/network'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  const echo = async (c: { name: string }) => ({ name: c.name })
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getCarriers: vi.fn(async () => []),
      getCatalog: vi.fn(async (component: string) => ({ component, attributes: [] })),
      createLink: vi.fn(echo), createGenerator: vi.fn(echo), createLine: vi.fn(echo),
    },
  }
})

const BUSES = [
  { name: 'AC bus', carrier: 'AC' }, { name: 'AC bus 2', carrier: 'AC' }, { name: 'H2 bus', carrier: 'H2' },
  { name: 'Heat bus', carrier: 'heat' }, { name: 'Gas bus', carrier: 'gas' },
]

function renderItem(id: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(nk('Demo', 'buses'), BUSES)
  render(<QueryClientProvider client={client}><CreationForm item={{ id, label: id }} /></QueryClientProvider>)
}

function inputFor(labelStart: string): HTMLInputElement {
  const label = Array.from(document.querySelectorAll('label'))
    .find(l => (l.textContent ?? '').trim().startsWith(labelStart))
  if (!label) throw new Error(`no field labelled ${labelStart}`)
  const scope = (label.querySelector('input, select') ? label : label.parentElement) as HTMLElement
  return scope.querySelector('input, select') as HTMLInputElement
}

async function submit(bus0: string, bus1: string, create: (...a: never[]) => unknown) {
  fireEvent.change(inputFor('Name'), { target: { value: 'X' } })
  const buses = Array.from(document.querySelectorAll('label'))
    .filter(l => /bus/i.test(l.textContent ?? '') && !l.querySelector('input'))
  fireEvent.change(buses[0].parentElement!.querySelector('input')!, { target: { value: bus0 } })
  if (buses[1]) fireEvent.change(buses[1].parentElement!.querySelector('input')!, { target: { value: bus1 } })
  fireEvent.click(screen.getByText('Add to Network'))
  await waitFor(() => expect(create).toHaveBeenCalled())
  return vi.mocked(create).mock.calls[0][0] as Record<string, unknown>
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo', creationItem: null }) })
afterEach(() => { cleanup(); vi.clearAllMocks(); useUIStore.setState({ currentProject: null }) })

describe('the creation form speaks the schema’s units', () => {
  it('an electrolyzer shows its 70 % efficiency and posts 0.7; its overnight cost goes in per kW', async () => {
    renderItem('electrolyzer')
    expect(inputFor('Efficiency').value).toBe('70')
    fireEvent.change(inputFor('Overnight cost'), { target: { value: '600' } })
    fireEvent.change(inputFor('Lifetime'), { target: { value: '20' } })
    const body = await submit('AC bus', 'H2 bus', networkApi.createLink)
    expect(body.efficiency).toBeCloseTo(0.7, 12)
    expect(body.overnight_cost).toBe(600_000)
    expect(body.lifetime).toBe(20)
    expect(body).not.toHaveProperty('capital_cost')
  })

  it('a heat pump’s COP snapped in by its type shows in % and posts as stored', async () => {
    renderItem('power_to_heat')
    fireEvent.change(inputFor('Type'), { target: { value: 'heat-pump-ground' } })
    expect(inputFor('Efficiency / COP').value).toBe('450')
    const body = await submit('AC bus', 'Heat bus', networkApi.createLink)
    expect(body.efficiency).toBeCloseTo(4.5, 12)
  })

  it('a CHP’s thermal efficiency borrows the efficiency’s unit', async () => {
    renderItem('chp')
    expect(inputFor('Electrical efficiency').value).toBe('40')
    expect(inputFor('Thermal efficiency').value).toBe('40')
    expect(screen.getByText(/^Thermal efficiency/).textContent).toContain('%')
  })

  it('an unpriced asset posts no investment: a blank overnight cost and lifetime are left to the model', async () => {
    renderItem('thermal')
    fireEvent.change(inputFor('Name'), { target: { value: 'G' } })
    fireEvent.change(inputFor('Attach to Bus').parentElement!.querySelector('input')!, { target: { value: 'AC bus' } })
    fireEvent.click(screen.getByText('Add to Network'))
    await waitFor(() => expect(networkApi.createGenerator).toHaveBeenCalled())
    const body = vi.mocked(networkApi.createGenerator).mock.calls[0][0] as Record<string, unknown>
    expect(body).not.toHaveProperty('overnight_cost')
    expect(body).not.toHaveProperty('lifetime')
  })

  it('a line types its investment per MVA and km, posted as typed', async () => {
    renderItem('line')
    fireEvent.change(inputFor('Overnight cost per km'), { target: { value: '432' } })
    fireEvent.change(inputFor('Lifetime'), { target: { value: '40' } })
    const body = await submit('AC bus', 'AC bus 2', networkApi.createLine)
    expect(body.inv_per_km_overnight).toBe(432)
    expect(body.lifetime).toBe(40)
  })
})
