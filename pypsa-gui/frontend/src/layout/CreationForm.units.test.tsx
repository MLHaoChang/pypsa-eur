// Every cost in the app is in EUR, and every `capital_cost` field is an
// annuity, so its badge carries `/yr` on every creation form.
//
// The generator form said `$`, and five converter and storage forms kept a
// bare `€/MW` beside a per-year quantity, the same trap the Properties card
// had. This walks every creation item that carries a capital-cost field and
// pins both facts, so a new item cannot ship the old badge.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import CreationForm from './CreationForm'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  const stub = vi.fn(async () => ({}))
  return {
    ...actual,
    networkApi: new Proxy({ ...actual.networkApi }, {
      get(target, prop: string) {
        if (prop === 'getCarriers') return vi.fn(async () => [])
        if (prop === 'getCatalog') return vi.fn(async () => ({ component: 'Generator', attributes: [] }))
        return (target as Record<string, unknown>)[prop] ?? stub
      },
    }),
  }
})

// Every palette item whose form carries a `capital_cost` field. Buses,
// loads and the storage archetypes price nothing at creation.
const PRICED_ITEMS = [
  'thermal', 'renewable', 'transformer',
  'electrolyzer', 'fuel_cell', 'power_to_heat', 'chp', 'thermal_storage',
]

const BUSES = [
  { name: 'Elec A', carrier: 'AC' }, { name: 'Elec B', carrier: 'AC' },
  { name: 'H2 A', carrier: 'H2' }, { name: 'Heat A', carrier: 'heat' },
]

function renderItem(id: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(nk('Demo', 'buses'), BUSES)
  return render(
    <QueryClientProvider client={client}>
      <CreationForm item={{ id, label: id }} />
    </QueryClientProvider>,
  )
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo', creationItem: null }) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('creation-form cost badges', () => {
  it.each(PRICED_ITEMS)('%s: EUR, and the capital-cost badge is per year', (id) => {
    renderItem(id)
    const labels = Array.from(document.querySelectorAll('label'))
      .map(l => l.textContent ?? '')
    const capital = labels.filter(t => t.startsWith('Capital cost'))
    expect(capital.length, `no Capital cost field on ${id}`).toBeGreaterThan(0)
    for (const t of capital) expect(t).toMatch(/\(€\/(MW|MWh|MVA)\/yr\)/)
    expect(document.body.textContent ?? '').not.toContain('$/')
  })
})
