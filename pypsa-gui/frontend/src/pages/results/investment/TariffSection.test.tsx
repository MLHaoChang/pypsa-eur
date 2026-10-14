// The Tariff section (IC P3 WP3.7b), in its own file so Expert → Economics can
// mount it next to the Investment tab (Economics in Expert, E0): the project's
// import tariff in the builder, a blank one when there is none, and never a
// blank builder over a tariff it could not read.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { commercialApi } from '../../../api/commercial'
import TariffSection from './TariffSection'

vi.mock('../../../api/commercial', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/commercial')>()
  return { ...actual, commercialApi: { ...actual.commercialApi, getCommercial: vi.fn() } }
})

function renderSection() {
  useUIStore.setState({ currentProject: 'Demo' })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><TariffSection /></QueryClientProvider>)
}

afterEach(() => { cleanup(); vi.mocked(commercialApi.getCommercial).mockReset() })

describe('TariffSection', () => {
  it('offers to build a tariff when the project has none', async () => {
    vi.mocked(commercialApi.getCommercial).mockResolvedValue({ poc_link: 'import' } as never)
    renderSection()
    expect(await screen.findByText('The project has no import tariff: build one.')).toBeTruthy()
  })

  it('never opens a blank builder over a tariff it could not read', async () => {
    vi.mocked(commercialApi.getCommercial).mockRejectedValue(new Error('boom'))
    renderSection()
    expect((await screen.findByTestId('ic-tariff-error')).textContent).toMatch(/could not be read/)
  })

  it('says when the tariff is a Library item that saving detaches', async () => {
    vi.mocked(commercialApi.getCommercial).mockResolvedValue({
      poc_link: 'import', import_tariff_ref: { id: 'lib-tariff', version: 3 } } as never)
    renderSection()
    expect((await screen.findByTestId('ic-tariff-library-ref')).textContent)
      .toMatch(/Library item lib-tariff v3/)
  })
})
