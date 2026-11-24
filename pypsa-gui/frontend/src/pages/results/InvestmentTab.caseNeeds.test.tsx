// Template-e2e issue 22, S2 (UX 2 on #1761): on the Investment case section
// the run panel's checklist is the only "needs"; the tab's own "needs the site
// connection" line steps aside and hands its need to the panel.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { commercialApi } from '../../api/commercial'
import InvestmentTab from './InvestmentTab'

vi.mock('../../api/commercial', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/commercial')>()
  return { ...actual, commercialApi: { ...actual.commercialApi,
    getBilling: vi.fn(), getValueFlowsResult: vi.fn(), getCommercial: vi.fn(), getPocState: vi.fn() } }
})
vi.mock('./investment/InvestmentCaseView', () => ({
  default: ({ connectionMissing }: { connectionMissing?: boolean }) =>
    <div data-testid="case-view" data-connection-missing={String(!!connectionMissing)} />,
}))

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><InvestmentTab /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', uiMode: 'expert' })
  vi.mocked(commercialApi.getBilling).mockResolvedValue(null)
  vi.mocked(commercialApi.getValueFlowsResult).mockResolvedValue(null)
  vi.mocked(commercialApi.getPocState).mockResolvedValue({ poc_link: null, open: false } as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('InvestmentTab, Investment case section', () => {
  it('no site connection: the panel lists it and the tab line steps aside', async () => {
    vi.mocked(commercialApi.getCommercial).mockResolvedValue(null)
    renderTab()
    await screen.findByTestId('ic-site-connection-needed')
    fireEvent.click(screen.getByRole('tab', { name: 'Investment case' }))
    expect((await screen.findByTestId('case-view')).getAttribute('data-connection-missing')).toBe('true')
    expect(screen.queryByTestId('ic-site-connection-needed')).toBeNull()
  })

  it('an island site with no connection: nothing is handed over (GK B3 4)', async () => {
    vi.mocked(commercialApi.getCommercial).mockResolvedValue(null)
    vi.mocked(commercialApi.getPocState).mockResolvedValue({ poc_link: null, open: true } as never)
    renderTab()
    await screen.findByTestId('ic-island')
    fireEvent.click(screen.getByRole('tab', { name: 'Investment case' }))
    expect((await screen.findByTestId('case-view')).getAttribute('data-connection-missing')).toBe('false')
    expect(screen.queryByTestId('ic-site-connection-needed')).toBeNull()
  })

  it('a site connection set: nothing is handed over', async () => {
    vi.mocked(commercialApi.getCommercial).mockResolvedValue({ poc_link: 'import' } as never)
    renderTab()
    fireEvent.click(await screen.findByRole('tab', { name: 'Investment case' }))
    expect((await screen.findByTestId('case-view')).getAttribute('data-connection-missing')).toBe('false')
  })
})
