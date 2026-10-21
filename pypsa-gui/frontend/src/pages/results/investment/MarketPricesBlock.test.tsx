// "Prices the optimisation uses" (IPP ticket 07): the export and wholesale import price series
// in the one tariff editor, saved through the commercial config's save path, with provenance.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { commercialApi, libraryApi } from '../../../api/commercial'
import TariffSection from './TariffSection'

vi.mock('../../../api/commercial', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/commercial')>()
  return {
    ...actual,
    commercialApi: { ...actual.commercialApi, getCommercial: vi.fn(), saveCommercial: vi.fn() },
    libraryApi: { ...actual.libraryApi, listSeries: vi.fn() },
  }
})

const DA = { id: 'da_base', version: 1, hash: 'h1', source: 'illustrative (assumed)' }
const UP = { id: 'my_da', version: 2, hash: 'h2', source: 'upload' }

function renderSection() {
  useUIStore.setState({ currentProject: 'Demo' })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><TariffSection /></QueryClientProvider>)
}

afterEach(() => { cleanup(); vi.mocked(commercialApi.getCommercial).mockReset()
                  vi.mocked(commercialApi.saveCommercial).mockReset()
                  vi.mocked(libraryApi.listSeries).mockReset() })

describe('MarketPricesBlock', () => {
  it('shows both prices with their provenance and saves the chosen series', async () => {
    vi.mocked(commercialApi.getCommercial).mockResolvedValue({
      poc_link: 'grid_import', export_link: 'grid_export',
      export_price_ref: DA, import_price_ref: DA } as never)
    vi.mocked(libraryApi.listSeries).mockResolvedValue([DA, UP] as never)
    vi.mocked(commercialApi.saveCommercial).mockResolvedValue({} as never)
    renderSection()
    expect((await screen.findByTestId('ic-import-price-provenance')).textContent)
      .toMatch(/da_base v1 · illustrative.*not market data/)
    expect(screen.getByTestId('ic-export-price-provenance').textContent).toMatch(/da_base v1/)
    await screen.findAllByRole('option', { name: 'my_da v2' })
    fireEvent.change(screen.getByLabelText('Import price series'), { target: { value: 'my_da@2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the prices' }))
    await waitFor(() => expect(commercialApi.saveCommercial).toHaveBeenCalled())
    expect(vi.mocked(commercialApi.saveCommercial).mock.calls[0][0])
      .toEqual({ import_price_ref: UP, export_price_ref: DA })
  })

  it('offers the import price only when there is no export Link', async () => {
    vi.mocked(commercialApi.getCommercial).mockResolvedValue({ poc_link: 'import' } as never)
    vi.mocked(libraryApi.listSeries).mockResolvedValue([DA] as never)
    renderSection()
    expect(await screen.findByLabelText('Import price series')).toBeTruthy()
    expect(screen.queryByLabelText('Export price series')).toBeNull()
  })

  it('shows the server refusal (an uncovered series) and keeps the choice', async () => {
    vi.mocked(commercialApi.getCommercial).mockResolvedValue({ poc_link: 'import' } as never)
    vi.mocked(libraryApi.listSeries).mockResolvedValue([DA] as never)
    vi.mocked(commercialApi.saveCommercial).mockRejectedValue(
      { response: { data: { detail: { message: 'Library series da_base v1 does not cover 4 snapshot(s)' } } } })
    renderSection()
    await screen.findByRole('option', { name: 'da_base v1' })
    fireEvent.change(screen.getByLabelText('Import price series'), { target: { value: 'da_base@1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the prices' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/does not cover 4 snapshot/)
  })
})
