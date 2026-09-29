// The Results → Investment tab shell (IC P3 WP3.5): completeness chips from the
// commercial results, a keyboard-reachable section switcher, and the bill
// summary. A missing result (204 → null) is "not established", never zero.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { commercialApi, SolverInFlightError } from '../../api/commercial'
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
  })

  it('shows unknown bill amounts as not established, never 0', async () => {
    vi.mocked(commercialApi.getBilling).mockResolvedValue({ ...BILLING,
      summary: { _: { total: null, total_supported: null, per_item: { energy: null } },
                 '2040': null } } as never)
    vi.mocked(commercialApi.getValueFlowsResult).mockResolvedValue(FLOWS as never)
    renderTab()
    await waitFor(() => expect(screen.getByTestId('ic-section-bill').getAttribute('data-status')).toBe('not_established'))
    fireEvent.click(await screen.findByRole('tab', { name: 'Bill' }))
    const table = await screen.findByTestId('ic-bill-table')
    expect(within(table).getAllByText('not established')).toHaveLength(3)
    expect(table.textContent).not.toMatch(/€|\b0\.00\b/)
  })

  it('a bill missing one period is not ok', async () => {
    vi.mocked(commercialApi.getBilling).mockResolvedValue({ ...BILLING,
      summary: { '2030': BILLING.summary._, '2040': null } } as never)
    vi.mocked(commercialApi.getValueFlowsResult).mockResolvedValue(FLOWS as never)
    renderTab()
    await waitFor(() => expect(screen.getByTestId('ic-section-bill').getAttribute('title')).toMatch(/2040/))
    expect(screen.getByTestId('ic-section-bill').getAttribute('data-status')).toBe('not_established')
  })

  it('says when there is no result, when it failed, and when the config is invalid', async () => {
    vi.mocked(commercialApi.getBilling).mockResolvedValue(null)
    vi.mocked(commercialApi.getValueFlowsResult).mockResolvedValue(null)
    renderTab()
    await waitFor(() => expect(screen.getByTestId('ic-participants-state').textContent).toMatch(/solve/))
    cleanup()
    vi.mocked(commercialApi.getValueFlowsResult).mockRejectedValue(new Error('boom'))
    renderTab()
    await waitFor(() => expect(screen.getByTestId('ic-participants-state').textContent).toMatch(/could not be loaded/i))
    cleanup()
    vi.mocked(commercialApi.getValueFlowsResult).mockResolvedValue(
      { status: 'value_flows_invalid', reason: 'participants: not a list' } as never)
    renderTab()
    await waitFor(() => expect(screen.getByTestId('ic-section-participants').getAttribute('data-status')).toBe('failed'))
    expect(screen.getByTestId('ic-participants-state').textContent).toMatch(/do not validate/)
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
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Bill' }), { key: 'End' })
    expect(screen.getByRole('tab', { name: 'Contracts' }).getAttribute('aria-selected')).toBe('true')
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Contracts' }), { key: 'Home' })
    expect(first.getAttribute('aria-selected')).toBe('true')
    // aria-controls only on the selected tab: it must point at a panel in the DOM.
    for (const tab of screen.getAllByRole('tab')) {
      const id = tab.getAttribute('aria-controls')
      if (id) expect(document.getElementById(id)).not.toBeNull()
    }
    expectAllButtonsNamed(container)
  })
})


describe('InvestmentTab billing failures', () => {
  it('a failed bill is failed, never "solve first"', async () => {
    vi.mocked(commercialApi.getBilling).mockRejectedValue(new Error('500'))
    vi.mocked(commercialApi.getValueFlowsResult).mockResolvedValue(FLOWS as never)
    renderTab()
    await waitFor(() => expect(screen.getByTestId('ic-section-bill').getAttribute('data-status')).toBe('failed'))
    fireEvent.click(await screen.findByRole('tab', { name: 'Bill' }))
    expect((await screen.findByTestId('ic-bill-error')).textContent).toMatch(/could not be loaded/i)
  })

  it('a solve in flight says so', async () => {
    vi.mocked(commercialApi.getBilling).mockRejectedValue(new SolverInFlightError('busy'))
    vi.mocked(commercialApi.getValueFlowsResult).mockResolvedValue(FLOWS as never)
    renderTab()
    await waitFor(() => expect(screen.getByTestId('ic-section-bill').getAttribute('title')).toMatch(/solve is running/))
  })
})
