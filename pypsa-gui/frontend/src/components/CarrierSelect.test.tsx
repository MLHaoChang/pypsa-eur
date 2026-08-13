// Characterization of CarrierSelect, written BEFORE the grid becomes its third
// consumer (spec D4). It is consumed UNCHANGED — these tests are what make that
// claim checkable. Zero coverage today.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import CarrierSelect from './CarrierSelect'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: { ...actual.networkApi, getCarriers: vi.fn(async () => []) },
  }
})

const PROJECT_CARRIERS = [
  { name: 'AC', co2_emissions: 0, color: '#111111', nice_name: 'AC', unit: '' },
  { name: 'my_odd_carrier', co2_emissions: 0, color: '#222222', nice_name: '', unit: '' },
]

function renderSelect(props: Partial<React.ComponentProps<typeof CarrierSelect>> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  client.setQueryData(nk('Demo', 'carriers'), PROJECT_CARRIERS)
  return render(
    <QueryClientProvider client={client}>
      <CarrierSelect value="" onChange={() => {}} {...props} />
    </QueryClientProvider>,
  )
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
})

describe('CarrierSelect option list — behaviour as of e8614a35', () => {
  it('groups options into optgroups', () => {
    renderSelect()
    expect(document.querySelectorAll('optgroup').length).toBeGreaterThan(1)
  })

  it('includes a carrier that exists only on the project', () => {
    renderSelect()
    expect(screen.getByRole('option', { name: 'my_odd_carrier' })).toBeTruthy()
  })

  it('includes curated catalog carriers the project does not have', () => {
    renderSelect()
    // "onwind" ships in CARRIER_CATALOG_NAMES and is absent from the project.
    expect(screen.getByRole('option', { name: 'onwind' })).toBeTruthy()
  })

  it('includes the current value even when it is in neither source', () => {
    renderSelect({ value: 'legacy_one_off' })
    expect(screen.getByRole('option', { name: 'legacy_one_off' })).toBeTruthy()
  })

  it('lists each carrier exactly once', () => {
    renderSelect({ value: 'AC' })
    const acs = screen.getAllByRole('option').filter(o => o.textContent === 'AC')
    expect(acs.length).toBe(1)
  })
})

describe('CarrierSelect rendering props — behaviour as of e8614a35', () => {
  it('renders a label by default', () => {
    renderSelect({ label: 'Carrier' })
    expect(screen.getByText('Carrier')).toBeTruthy()
  })

  it('omits the label when label={null} — the prop the grid passes', () => {
    const { container } = renderSelect({ label: null })
    expect(container.querySelector('label span')).toBeNull()
  })

  it('appends className to the select', () => {
    renderSelect({ className: 'grid-cell-select' })
    expect(screen.getByRole('combobox').className).toContain('grid-cell-select')
  })

  it('calls onChange with the chosen carrier name', async () => {
    const onChange = vi.fn()
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    client.setQueryData(nk('Demo', 'carriers'), PROJECT_CARRIERS)
    render(
      <QueryClientProvider client={client}>
        <CarrierSelect value="" onChange={onChange} />
      </QueryClientProvider>,
    )
    await userEvent.selectOptions(screen.getByRole('combobox'), 'AC')
    expect(onChange).toHaveBeenCalledWith('AC')
  })
})
