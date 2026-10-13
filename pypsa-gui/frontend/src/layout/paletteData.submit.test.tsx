// WP0 review-gate finding 1: the palette data must agree with what the
// creation form actually SUBMITS, not only with its own class map. For
// every palette item: render the form, fill the required fields, submit,
// and assert the create call for the item's class ran with the default
// carrier from paletteData.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import CreationForm, { FIELD_MAP } from './CreationForm'
import { networkApi } from '../api/network'
import { PALETTE_SECTIONS_DATA, PALETTE_ITEM_IDS, paletteDefaults, type PortFilter } from './paletteData'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  const echo = async (c: { name: string }) => ({ name: c.name })
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getCarriers: vi.fn(async () => []),
      createBus: vi.fn(echo), createLine: vi.fn(echo), createTransformer: vi.fn(echo), createLink: vi.fn(echo),
      createGenerator: vi.fn(echo), createStorageUnit: vi.fn(echo), createStore: vi.fn(echo), createLoad: vi.fn(echo),
    },
  }
})

const BUSES = [
  { name: 'AC bus', carrier: 'AC' }, { name: 'AC bus 2', carrier: 'AC' },
  { name: 'H2 bus', carrier: 'H2' }, { name: 'Heat bus', carrier: 'heat' }, { name: 'Gas bus', carrier: 'gas' },
]
const BUS_FOR: Record<PortFilter | 'any', string> = { electricity: 'AC bus', 'non-h2': 'AC bus', h2: 'H2 bus', heat: 'Heat bus', gas: 'Gas bus', any: 'AC bus' }
const CREATE_FOR_CLASS = {
  Bus: 'createBus', Line: 'createLine', Transformer: 'createTransformer', Link: 'createLink',
  Generator: 'createGenerator', StorageUnit: 'createStorageUnit', Store: 'createStore', Load: 'createLoad',
} as const

function inputFor(labelText: string): HTMLInputElement {
  const label = Array.from(document.querySelectorAll('label')).find(l => (l.textContent ?? '').trim().startsWith(labelText))
  if (!label) throw new Error(`no field labelled ${labelText}`)
  const scope = (label.querySelector('input') ? label : label.parentElement) as HTMLElement
  return scope.querySelector('input') as HTMLInputElement
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', creationItem: null, readOnly: false, readOnlyReason: 'writable' })
})
afterEach(() => { cleanup(); vi.clearAllMocks(); useUIStore.setState({ currentProject: null, creationItem: null }) })

const labelOf = (id: string) => PALETTE_SECTIONS_DATA.flatMap(s => s.items).find(i => i.id === id)!.label

describe('the creation form submits what the palette data says', () => {
  it.each(PALETTE_ITEM_IDS)('%s', async id => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    client.setQueryData(nk('Demo', 'buses'), BUSES)
    const { getByText } = render(<QueryClientProvider client={client}><CreationForm item={{ id, label: labelOf(id) }} /></QueryClientProvider>)
    fireEvent.change(inputFor('Name'), { target: { value: `${id}-1` } })
    // Fill every bus field with a bus its filter accepts (two distinct AC buses for branches).
    let acUsed = 0
    for (const f of FIELD_MAP[id] ?? []) {
      if (f.type !== 'bus') continue
      const filter = ((f as { busCarrierFilter?: PortFilter }).busCarrierFilter ?? 'any') as PortFilter | 'any'
      let bus = BUS_FOR[filter]
      if (bus === 'AC bus' && acUsed++ > 0) bus = 'AC bus 2'
      fireEvent.change(inputFor(f.label), { target: { value: bus } })
    }
    fireEvent.click(getByText('Add to Network'))
    const d = paletteDefaults(id)
    const create = networkApi[CREATE_FOR_CLASS[d.cls]] as unknown as ReturnType<typeof vi.fn>
    await waitFor(() => expect(create, `${id} → ${CREATE_FOR_CLASS[d.cls]}`).toHaveBeenCalledTimes(1))
    const payload = create.mock.calls[0][0] as Record<string, unknown>
    if (d.carrier !== undefined) expect(payload.carrier, `${id} carrier`).toBe(d.carrier)
    // No other create call ran.
    for (const fn of Object.values(CREATE_FOR_CLASS)) if (fn !== CREATE_FOR_CLASS[d.cls]) expect(networkApi[fn], `${id} also called ${fn}`).not.toHaveBeenCalled()
  })
})
