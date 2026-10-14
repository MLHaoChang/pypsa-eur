// Every cost in the app is in EUR, and since S1d no creation form types an
// annuity (plan rule: no typed `capital_cost` anywhere). A priced item types
// its OVERNIGHT cost, per unit of what it sizes, never per year: €/kW for a
// generator or link, €/kWh for a store, €/MVA for a transformer, €/MVA/km for
// a line. This walks every palette item and pins both facts, so a new item
// cannot ship the old annuity field.
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

// Every palette item that prices its investment at creation, and the unit its
// overnight cost is typed in. Buses and loads price nothing; the storage units
// price their two parts on the card (STORAGE_PRICING_NOTE).
const PRICED_ITEMS: Array<[string, string, string]> = [
  ['thermal', 'Overnight cost', '€/kW'], ['renewable', 'Overnight cost', '€/kW'],
  ['transformer', 'Overnight cost', '€/MVA'], ['line', 'Overnight cost per km', '€/MVA/km'],
  ['electrolyzer', 'Overnight cost', '€/kW'], ['fuel_cell', 'Overnight cost', '€/kW'],
  ['power_to_heat', 'Overnight cost', '€/kW'], ['chp', 'Overnight cost', '€/kW'],
  ['thermal_storage', 'Overnight cost', '€/kWh'],
]
const ALL_ITEMS = [
  ...PRICED_ITEMS.map(([id]) => id), 'bus', 'battery', 'psh', 'hydrogen', 'caes', 'flywheel',
  'load_elec', 'load_h2', 'load_heat',
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

function labels(): string[] {
  return Array.from(document.querySelectorAll('label')).map(l => l.textContent ?? '')
}

describe('creation-form cost fields', () => {
  it.each(ALL_ITEMS)('%s: no typed annuity, and no dollar sign', (id) => {
    renderItem(id)
    expect(labels().some(t => /^Capital cost/.test(t)), `a Capital cost field on ${id}`).toBe(false)
    expect(document.body.textContent ?? '').not.toContain('$/')
  })

  it.each(PRICED_ITEMS)('%s: the overnight cost is typed per unit, never per year', (id, label, unit) => {
    renderItem(id)
    const overnight = labels().filter(t => t.startsWith(label))
    expect(overnight.length, `no ${label} field on ${id}`).toBe(1)
    expect(overnight[0]).toContain(`(${unit})`)
    expect(overnight[0]).not.toContain('/yr')
    expect(labels().some(t => /^Lifetime/.test(t)), `no Lifetime field on ${id}`).toBe(true)
  })
})
