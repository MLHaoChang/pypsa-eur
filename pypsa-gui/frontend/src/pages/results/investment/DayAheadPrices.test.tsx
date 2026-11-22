import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { marketPricesApi, type ProjectMarketPrices } from '../../../api/marketPrices'
import { DayAheadPrices } from './DayAheadPrices'
import { expectAllButtonsNamed } from '../../../test-utils/accessibleName'

vi.mock('../../../api/marketPrices', () => ({
  marketPricesApi: { project: vi.fn(), fetchDayAhead: vi.fn(), saveToken: vi.fn(), clearToken: vi.fn() },
}))
const api = vi.mocked(marketPricesApi)

const INFO: ProjectMarketPrices = {
  token_set: true, can_set_token: false, can_fetch: true, last_year: 2025,
  zones: [{ code: 'DE-LU', label: 'Germany and Luxembourg', first_year: 2019 },
          { code: 'NO-2', label: 'Southern Norway', first_year: 2015 }],
  pin_zone: 'DE-LU', chosen_zone: null, zone: 'DE-LU',
}

function show(info: Partial<ProjectMarketPrices> = {}) {
  api.project.mockResolvedValue({ ...INFO, ...info })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><DayAheadPrices /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', readOnly: false, readOnlyReason: 'writable' })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('DayAheadPrices', () => {
  it('offers the zone from the pin and the last complete year, rows read "code · name"', async () => {
    const { container } = show()
    const zone = await screen.findByLabelText('Bidding zone') as HTMLSelectElement
    expect(zone.value).toBe('DE-LU')
    expect(screen.getByRole('option', { name: 'NO-2 · Southern Norway' })).toBeTruthy()
    expect((screen.getByLabelText('Year') as HTMLInputElement).value).toBe('2025')
    expect(screen.getByText('Zone from the site pin.')).toBeTruthy()
    expectAllButtonsNamed(container)
  })

  it('without a token says why; a non-admin is told who can add it and gets no field', async () => {
    show({ token_set: false })
    expect((await screen.findByTestId('da-no-token')).textContent)
      .toBe('Day-ahead prices need an ENTSO-E access token. Ask a super-admin to add it.')
    expect(screen.queryByTestId('da-token-input')).toBeNull()
    expect(screen.queryByTestId('da-fetch')).toBeNull()
  })

  it('a super-admin saves the token in a masked field, cleared on success', async () => {
    api.saveToken.mockResolvedValue({ token_set: true, can_set_token: true })
    show({ token_set: false, can_set_token: true })
    const field = await screen.findByLabelText('ENTSO-E access token') as HTMLInputElement
    expect(field.type).toBe('password')
    fireEvent.change(field, { target: { value: 'abc-123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save token' }))
    expect((await screen.findByRole('status')).textContent).toBe('Access token saved.')
    expect(api.saveToken).toHaveBeenCalledWith('abc-123')
    expect(field.value).toBe('')
  })

  it('fetches, says it is fetching, then shows source, zone, year and the fetch date', async () => {
    let finish: (v: unknown) => void = () => {}
    api.fetchDayAhead.mockReturnValue(new Promise(r => { finish = r }) as never)
    show()
    fireEvent.change(await screen.findByLabelText('Year'), { target: { value: '2024' } })
    fireEvent.click(screen.getByTestId('da-fetch'))
    expect((await screen.findByRole('status')).textContent).toBe('Fetching DE-LU day-ahead prices for 2024…')
    expect((screen.getByTestId('da-fetch') as HTMLButtonElement).disabled).toBe(true)
    finish({ ref: { id: 'entsoe_da_DE-LU_2024', version: 1, hash: 'h', source: 'ENTSO-E day-ahead' },
             zone: 'DE-LU', year: 2024, fetched: '2026-11-21', source: 'ENTSO-E day-ahead' })
    const landed = await screen.findByTestId('da-landed')
    expect(landed.textContent).toContain('ENTSO-E day-ahead · DE-LU · 2024 · fetched 2026-11-21')
    expect(api.fetchDayAhead).toHaveBeenCalledWith('Demo', 'DE-LU', 2024)
  })

  it('says a refusal once, in the server\'s plain words', async () => {
    api.fetchDayAhead.mockRejectedValue({ response: { status: 422, data: { detail: {
      kind: 'bad_token', message: 'ENTSO-E did not accept the access token.' } } } })
    show()
    fireEvent.click(await screen.findByTestId('da-fetch'))
    expect((await screen.findByTestId('da-refusal')).textContent).toBe('ENTSO-E did not accept the access token.')
    expect(screen.getAllByRole('alert')).toHaveLength(1)
  })

  it('refuses a year outside the zone\'s range, the message opening with the field label', async () => {
    show()
    fireEvent.change(await screen.findByLabelText('Year'), { target: { value: '2018' } })
    expect(screen.getByText('Year: choose a complete year from 2019 to 2025.')).toBeTruthy()
    expect((screen.getByTestId('da-fetch') as HTMLButtonElement).disabled).toBe(true)
  })

  it('asks for a zone when the pin does not settle one', async () => {
    show({ pin_zone: null, zone: null })
    await screen.findByLabelText('Bidding zone')
    expect(screen.getByText('The site pin does not settle the zone: choose one.')).toBeTruthy()
    expect((screen.getByTestId('da-fetch') as HTMLButtonElement).disabled).toBe(true)
  })

  it('a view-only user gets no Fetch action', async () => {
    show({ can_fetch: false })
    expect(await screen.findByText('You can view this project but not add prices to its Library.')).toBeTruthy()
    expect(screen.queryByTestId('da-fetch')).toBeNull()
  })

  it('a locked project shows the lock reason and disables Fetch', async () => {
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
    show()
    await waitFor(() => expect((screen.getByTestId('da-fetch') as HTMLButtonElement).disabled).toBe(true))
    expect(screen.getByTestId('da-prices').textContent).toMatch(/Read-only/)
  })
})
