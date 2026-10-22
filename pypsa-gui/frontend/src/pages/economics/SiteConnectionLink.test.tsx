// The one editor of the site connection sits in Connection & grid (audit #13).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { commercialApi } from '../../api/commercial'
import { SiteConnectionEditor } from './SiteConnectionLink'

vi.mock('../../api/commercial', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/commercial')>()
  return { ...actual, commercialApi: { ...actual.commercialApi,
    getCommercial: vi.fn(), saveSiteConnection: vi.fn() } }
})
vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi,
    getLinks: vi.fn().mockResolvedValue([{ name: 'import', bus0: 'a', bus1: 'b' }, { name: 'export', bus0: 'b', bus1: 'a' }]),
    getBuses: vi.fn().mockResolvedValue([]), getGenerators: vi.fn().mockResolvedValue([]) } }
})

afterEach(cleanup)
const mount = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <SiteConnectionEditor />
  </QueryClientProvider>)

describe('SiteConnectionEditor', () => {
  it('shows the form when no connection is set, and saves through the commercial route', async () => {
    vi.mocked(commercialApi.getCommercial).mockResolvedValue(null)
    vi.mocked(commercialApi.saveSiteConnection).mockResolvedValue({} as never)
    mount()
    expect(await screen.findByTestId('site-connection-form')).toBeTruthy()
    const poc = await screen.findByLabelText(/point of connection/i) as HTMLSelectElement
    await waitFor(() => expect(poc.value).toBe('import'))
    fireEvent.change(screen.getByLabelText(/time zone/i), { target: { value: 'UTC' } })
    vi.mocked(commercialApi.getCommercial).mockResolvedValue(
      { poc_link: 'import', export_link: 'export', timezone: 'UTC' } as never)
    fireEvent.click(screen.getByRole('button', { name: 'Save site connection' }))
    await waitFor(() => expect(commercialApi.saveSiteConnection).toHaveBeenCalledWith(
      { poc_link: 'import', export_link: 'export', timezone: 'UTC' }))
    expect((await screen.findByTestId('ic-site-connection-summary')).textContent).toMatch(/import/)
  })

  it('a set connection is a summary that opens the form to edit it', async () => {
    vi.mocked(commercialApi.getCommercial).mockResolvedValue({ poc_link: 'import' } as never)
    mount()
    expect((await screen.findByTestId('ic-site-connection-summary')).textContent).toMatch(/import/)
    fireEvent.click(screen.getByRole('button', { name: 'Edit site connection' }))
    expect(await screen.findByTestId('site-connection-form')).toBeTruthy()
  })
})
