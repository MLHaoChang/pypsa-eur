// Every cost in the app is in EUR; the generator creation form said `$`.
//
// The same user who reads `€/MW/yr` on the Properties card met `$/MW` on the
// form that created the asset. One currency, one badge.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
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
      getCatalog: vi.fn(async () => ({ component: 'Generator', attributes: [] })),
      createGenerator: vi.fn(async () => ({})),
    },
  }
})

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo', creationItem: null }) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('prices a new generator in EUR, per MW per year for the annuity', () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(nk('Demo', 'buses'), [{ name: 'Elec A', carrier: 'AC' }])
  render(
    <QueryClientProvider client={client}>
      <CreationForm item={{ id: 'thermal', label: 'Conventional' }} />
    </QueryClientProvider>,
  )
  const text = document.body.textContent ?? ''
  expect(text).not.toContain('$/')
  expect(text).toContain('(€/MWh)')
  expect(text).toContain('(€/MW/yr)')
})
