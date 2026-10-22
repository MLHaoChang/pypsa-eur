// W3c (UX-flow #24): the "asset added" toast leads on with "Next: give it a
// time series ›", landing on the asset's primary series field and its editor.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import CreationForm from './CreationForm'

const toasts = vi.hoisted(() => ({ render: null as null | ((t: { id: string }) => unknown) }))
vi.mock('react-hot-toast', () => {
  const success = vi.fn((r: unknown) => { toasts.render = typeof r === 'function' ? r as never : null })
  return { default: Object.assign(vi.fn(), { success, error: vi.fn(), dismiss: vi.fn() }) }
})

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getCarriers: vi.fn(async () => []),
      getCatalog: vi.fn(async () => ({ component: 'Load', attributes: [] })),
      createLoad: vi.fn(async () => ({})),
      createGenerator: vi.fn(async () => ({})),
    },
  }
})

function fieldInput(labelRe: RegExp): HTMLInputElement {
  const label = Array.from(document.querySelectorAll('label'))
    .find(l => labelRe.test(l.textContent ?? ''))
  if (!label) throw new Error(`no field labelled ${labelRe}`)
  const scope = (label.querySelector('input') ? label : label.parentElement) as HTMLElement
  return scope.querySelector('input') as HTMLInputElement
}

async function addAndClickNext(id: string, label: string, name: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(nk('Demo', 'buses'), [{ name: 'Elec A', carrier: 'AC' }])
  render(<QueryClientProvider client={client}><CreationForm item={{ id, label }} /></QueryClientProvider>)
  fireEvent.change(fieldInput(/^Name/), { target: { value: name } })
  fireEvent.change(fieldInput(/^(Attach to Bus|Electricity bus)/), { target: { value: 'Elec A' } })
  await userEvent.click(screen.getByText('Add to Network'))
  await vi.waitFor(() => expect(toasts.render).not.toBeNull())
  render(<>{toasts.render!({ id: 't1' }) as never}</>)
  fireEvent.click(screen.getByTestId('asset-give-time-series'))
}

beforeEach(() => {
  toasts.render = null
  useUIStore.setState({ currentProject: 'Demo', uiMode: 'expert', creationItem: null })
})
afterEach(() => useUIStore.setState({ currentProject: null, creationItem: null, timeSeriesFocus: null }))

describe('Next: give it a time series', () => {
  it('a new load opens the p_set editor', async () => {
    await addAndClickNext('load_elec', 'Electrical Demand', 'plant')
    expect(useUIStore.getState().timeSeriesFocus).toMatchObject(
      { type: 'Load', name: 'plant', field: 'p_set', openEditor: true })
  })

  it('a new generator opens the p_max_pu editor', async () => {
    await addAndClickNext('thermal', 'Conventional', 'gt1')
    expect(screen.getByTestId('asset-give-time-series').textContent).toBe('Next: give it a time series ›')
    expect(useUIStore.getState().timeSeriesFocus).toMatchObject(
      { type: 'Generator', name: 'gt1', field: 'p_max_pu', openEditor: true })
  })
})
