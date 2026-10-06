// Shell polish (UX assessment 2026-10-05, Q3 and Q5).
//
// Q3: the app prices in euro everywhere else (the edit cards, Economics, the
// finance engine), so the creation form must not say "$".
// Q5: a battery could be created only with a fixed size and no cost, so the
// first thing every user did was open the edit card. The create form now
// carries the edit card's sizing and cost keys.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import CreationForm from './CreationForm'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getCarriers: vi.fn(async () => []),
      getCatalog: vi.fn(async () => ({ component: 'StorageUnit', attributes: [] })),
      createGenerator: vi.fn(async () => ({})),
      createStorageUnit: vi.fn(async () => ({})),
    },
  }
})

import { networkApi } from '../api/network'

function renderForm(id: string, label: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  client.setQueryData(nk('Demo', 'buses'), [{ name: 'Elec A', carrier: 'AC' }])
  return render(
    <QueryClientProvider client={client}>
      <CreationForm item={{ id, label }} />
    </QueryClientProvider>,
  )
}

function fieldInput(labelRe: RegExp): HTMLInputElement {
  const label = Array.from(document.querySelectorAll('label'))
    .find(l => labelRe.test(l.textContent ?? ''))
  if (!label) throw new Error(`no field labelled ${labelRe}`)
  const scope = (label.querySelector('input') ? label : label.parentElement) as HTMLElement
  return scope.querySelector('input') as HTMLInputElement
}

function toggleExtendable() {
  fireEvent.click(fieldInput(/^Extendable/))
}

async function submit(name: string) {
  fireEvent.change(fieldInput(/^Name/), { target: { value: name } })
  fireEvent.change(fieldInput(/^Attach to Bus/), { target: { value: 'Elec A' } })
  await userEvent.click(screen.getByText('Add to Network'))
}

beforeEach(() => {
  vi.mocked(networkApi).createStorageUnit.mockClear()
  vi.mocked(networkApi).createGenerator.mockClear()
  useUIStore.setState({ currentProject: 'Demo', creationItem: null })
})

afterEach(() => {
  useUIStore.setState({ currentProject: null, creationItem: null })
})

describe('Q3 — the creation form prices in euro', () => {
  it.each([
    ['thermal', 'Conventional'],
    ['battery', 'Battery'],
  ])('%s shows no dollar sign', (id, label) => {
    const { container } = renderForm(id, label)
    toggleExtendable()
    expect(container.textContent).not.toContain('$')
    expect(container.textContent).toContain('€/MWh')
  })
})

describe('Q5 — a battery can be sized and costed at creation', () => {
  it('offers Extendable, marginal cost and capital cost', () => {
    renderForm('battery', 'Battery')
    expect(fieldInput(/^Extendable/).type).toBe('checkbox')
    expect(fieldInput(/^Marginal cost/)).toBeTruthy()
    expect(fieldInput(/^Capital cost/)).toBeTruthy()
  })

  it('reveals the size bounds only when Extendable is ticked', () => {
    renderForm('battery', 'Battery')
    expect(screen.queryByText(/P nom max/i)).toBeNull()
    toggleExtendable()
    expect(screen.getByText(/P nom min/i)).toBeTruthy()
    expect(screen.getByText(/P nom max/i)).toBeTruthy()
  })

  it('posts the sizing and cost the user typed, under the edit card’s keys', async () => {
    renderForm('battery', 'Battery')
    toggleExtendable()
    fireEvent.change(fieldInput(/^P nom max/i), { target: { value: '50' } })
    fireEvent.change(fieldInput(/^Capital cost/), { target: { value: '95700' } })
    await submit('BESS')
    await waitFor(() => expect(vi.mocked(networkApi).createStorageUnit).toHaveBeenCalled())
    const payload = vi.mocked(networkApi).createStorageUnit.mock.calls[0][0] as Record<string, unknown>
    expect(payload).toMatchObject({
      name: 'BESS', p_nom_extendable: true, p_nom_max: 50, capital_cost: 95700, marginal_cost: 0,
    })
  })

  it('does not post hidden bounds for a fixed-size battery', async () => {
    renderForm('battery', 'Battery')
    await submit('BESS_fixed')
    await waitFor(() => expect(vi.mocked(networkApi).createStorageUnit).toHaveBeenCalled())
    const payload = vi.mocked(networkApi).createStorageUnit.mock.calls[0][0] as Record<string, unknown>
    expect(payload.p_nom_extendable).toBe(false)
    expect(payload).not.toHaveProperty('p_nom_max')
    expect(payload).not.toHaveProperty('p_nom_min')
  })
})
