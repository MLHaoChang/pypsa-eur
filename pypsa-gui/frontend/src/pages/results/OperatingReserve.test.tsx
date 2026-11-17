import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi, type OperatingReserveResult } from '../../api/simulation'
import OperatingReserve from './OperatingReserve'

const openOffGrid = vi.hoisted(() => vi.fn())
vi.mock('../../layout/flowNavigation', () => ({ openOffGridConstraints: openOffGrid }))
vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return { ...actual, resultsApi: { ...actual.resultsApi, getOperatingReserve: vi.fn() } }
})

beforeEach(() => useUIStore.setState({ currentProject: 'Demo' }))
afterEach(() => { cleanup(); vi.restoreAllMocks(); openOffGrid.mockReset(); useUIStore.setState({ currentProject: null }) })

const base: OperatingReserveResult = {
  shares: { load: 0.1, pv: 0.2, wind: 0.15 }, not_counted: [], per_snapshot: [], binding: [], reason: null,
}
function show(data: OperatingReserveResult | null) {
  vi.mocked(resultsApi.getOperatingReserve).mockResolvedValue(data)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><OperatingReserve /></QueryClientProvider>)
}

it('is hidden when the reserve was off or the route has nothing', async () => {
  const { container } = show({ ...base, shares: null, reason: 'the operating reserve was off for this solve' })
  await waitFor(() => expect(resultsApi.getOperatingReserve).toHaveBeenCalled())
  expect(container.innerHTML).toBe('')
  cleanup()
  const second = show(null)
  await waitFor(() => expect(resultsApi.getOperatingReserve).toHaveBeenCalledTimes(2))
  expect(second.container.innerHTML).toBe('')
})

it('lists the binding hours with the shares in plain text and the inputs link', async () => {
  show({
    ...base, not_counted: ['bat'], binding: ['2030-01-01 03:00'],
    per_snapshot: [
      { snapshot: '2030-01-01 02:00', required_mw: 1, held_mw: 5, binding: false },
      { snapshot: '2030-01-01 03:00', required_mw: 2, held_mw: 1.5, binding: true,
        holders: [{ name: 'Diesel', kind: 'generator', held_mw: 1.2 }, { name: 'Battery', kind: 'storage_unit', held_mw: 0.3 }] },
    ],
  })
  expect(await screen.findByTestId('operating-reserve-shares')).toHaveProperty('textContent', 'Reserve for load 10 %, solar 20 %, wind 15 %')
  expect(screen.getAllByTestId('operating-reserve-row')).toHaveLength(1)
  expect(screen.getByText('Shortfall (MW)')).toBeTruthy()
  expect(screen.getByText('0.5')).toBeTruthy()
  expect(screen.getByText(/bat\./)).toBeTruthy()
  expect(screen.getByTestId('operating-reserve-holders').textContent).toBe('Diesel 1.2 MW · Battery (storage) 0.3 MW')
  fireEvent.click(screen.getByTestId('operating-reserve-inputs'))
  expect(openOffGrid).toHaveBeenCalledTimes(1)
})

it('says so when the reserve never bound', async () => {
  show({ ...base, per_snapshot: [{ snapshot: 'a', required_mw: 1, held_mw: 5, binding: false }] })
  expect((await screen.findByTestId('operating-reserve-never')).textContent).toBe('The reserve never bound in this run.')
})

it('states the cap and translates the server reason', async () => {
  const many = Array.from({ length: 60 }, (_, i) => ({ snapshot: `h${i}`, required_mw: 2, held_mw: 2, binding: true }))
  show({ ...base, per_snapshot: many })
  expect((await screen.findByTestId('operating-reserve-cap')).textContent).toBe('Showing the first 50 of 60 hours.')
  expect(screen.getAllByTestId('operating-reserve-row')).toHaveLength(50)
  cleanup()
  show({ ...base, reason: 'the solve has no dispatch for g1' })
  expect((await screen.findByTestId('operating-reserve-reason')).textContent).not.toMatch(/the solve has no dispatch for g1/)
})

it('folds past five units behind Show all and explains a binding hour with no holders', async () => {
  const holders = Array.from({ length: 7 }, (_, i) => ({ name: `u${i}`, kind: 'generator' as const, held_mw: 7 - i }))
  show({ ...base, per_snapshot: [
    { snapshot: 'a', required_mw: 28, held_mw: 28, binding: true, holders },
    { snapshot: 'b', required_mw: 1, held_mw: 0, binding: true, holders: [] },
  ] })
  const cells = await screen.findAllByTestId('operating-reserve-holders')
  expect(cells[0].textContent).toBe('u0 7 MW · u1 6 MW · u2 5 MW · u3 4 MW · u4 3 MW · (2 more) Show all')
  expect(cells[1].textContent).toBe('No unit had spare capacity to hold reserve')
  fireEvent.click(screen.getByTestId('operating-reserve-show-all'))
  expect(screen.getAllByTestId('operating-reserve-holders')[0].textContent).toBe('u0 7 MW · u1 6 MW · u2 5 MW · u3 4 MW · u4 3 MW · u5 2 MW · u6 1 MW')
})
