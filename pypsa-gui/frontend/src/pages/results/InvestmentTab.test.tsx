// The Results → Investment tab shell (IC P3 WP3.5): completeness chips from the
// commercial results, a keyboard-reachable section switcher, and the bill
// summary. A missing result (204 → null) is "not established", never zero.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { commercialApi } from '../../api/commercial'
import InvestmentTab from './InvestmentTab'
import { expectAllButtonsNamed } from '../../test-utils/accessibleName'

vi.mock('../../api/commercial', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/commercial')>()
  return { ...actual, commercialApi: { ...actual.commercialApi,
    getBilling: vi.fn(), getValueFlowsResult: vi.fn() } }
})

const BILLING = {
  summary: { _: { total: 1234.5, total_supported: 1234.5,
                  per_item: { energy: 1000.25, demand: 234.25 } } },
  flags: [], contracts: { lines: [], flags: [], retail: {} },
  gap_summary: { gates: [], periods: {} }, per_period: {}, gap: {}, provenance: {},
}
const FLOWS = { status: 'ok', conservation_ok: true, participants: [], periods: {} }

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><InvestmentTab /></QueryClientProvider>)
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => { cleanup(); vi.mocked(commercialApi.getBilling).mockReset()
                  vi.mocked(commercialApi.getValueFlowsResult).mockReset() })

describe('InvestmentTab', () => {
  it('shows the completeness of the bill, the participants and conservation', async () => {
    vi.mocked(commercialApi.getBilling).mockResolvedValue(BILLING as never)
    vi.mocked(commercialApi.getValueFlowsResult).mockResolvedValue(FLOWS as never)
    renderTab()
    await waitFor(() => expect(screen.getByTestId('ic-section-bill').getAttribute('data-status')).toBe('ok'))
    await waitFor(() => expect(screen.getByTestId('ic-section-participants').getAttribute('data-status')).toBe('ok'))
    await waitFor(() => expect(screen.getByTestId('ic-section-conservation').getAttribute('data-status')).toBe('ok'))
  })

  it('reads a missing result as not established, never as zero', async () => {
    vi.mocked(commercialApi.getBilling).mockResolvedValue(null)
    vi.mocked(commercialApi.getValueFlowsResult).mockResolvedValue(
      { status: 'not_established', reason: 'no participants' } as never)
    renderTab()
    await waitFor(() => expect(screen.getByTestId('ic-section-bill').getAttribute('data-status')).toBe('not_established'))
    await waitFor(() => expect(screen.getByTestId('ic-section-participants').getAttribute('data-status')).toBe('not_established'))
    expect(screen.queryByText(/€\s*0/)).toBeNull()
  })

  it('names a failed conservation check', async () => {
    vi.mocked(commercialApi.getBilling).mockResolvedValue(BILLING as never)
    vi.mocked(commercialApi.getValueFlowsResult).mockResolvedValue(
      { ...FLOWS, conservation_ok: false } as never)
    renderTab()
    await waitFor(() => expect(screen.getByTestId('ic-section-conservation').getAttribute('data-status')).toBe('failed'))
  })

  it('lists the bill per item on the Bill section', async () => {
    vi.mocked(commercialApi.getBilling).mockResolvedValue(BILLING as never)
    vi.mocked(commercialApi.getValueFlowsResult).mockResolvedValue(FLOWS as never)
    renderTab()
    fireEvent.click(await screen.findByRole('tab', { name: 'Bill' }))
    const table = await screen.findByTestId('ic-bill-table')
    expect(within(table).getByText('energy')).toBeTruthy()
    expect(within(table).getByText('demand')).toBeTruthy()
  })

  it('switches sections with the arrow keys and names every button', async () => {
    vi.mocked(commercialApi.getBilling).mockResolvedValue(BILLING as never)
    vi.mocked(commercialApi.getValueFlowsResult).mockResolvedValue(FLOWS as never)
    const { container } = renderTab()
    const first = await screen.findByRole('tab', { name: 'Participants' })
    expect(first.getAttribute('aria-selected')).toBe('true')
    fireEvent.keyDown(first, { key: 'ArrowRight' })
    expect(screen.getByRole('tab', { name: 'Bill' }).getAttribute('aria-selected')).toBe('true')
    expectAllButtonsNamed(container)
  })
})
