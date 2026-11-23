import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import SpecialistPacketTab from './SpecialistPacketTab'

const api = vi.hoisted(() => ({
  status: vi.fn(), snapshots: vi.fn(), ledger: vi.fn(), config: vi.fn(),
}))
vi.mock('../../api/gridspine', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/gridspine')>()
  return { ...actual, gridspineApi: { ...actual.gridspineApi, ...api } }
})
vi.mock('../GridspinePanel', () => ({
  STATUS_KEY: (n: string) => ['gridspine', 'status', n], SNAPSHOTS_KEY: (n: string) => ['gridspine', 'snapshots', n],
  LEDGER_KEY: (n: string) => ['gridspine', 'ledger', n], CONFIG_KEY: (n: string) => ['gridspine', 'config', n],
  LedgerSection: () => <div data-testid="ledger-stub" />,
  SnapshotTable: ({ rows, bundles }: { rows: unknown[]; bundles: Record<string, string> }) =>
    <div data-testid="snapshots-stub">{rows.length} rows, {Object.keys(bundles).length} bundles</div>,
  describeSource: () => 'the solved network of oj-grid',
}))

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><SpecialistPacketTab /></QueryClientProvider>)
}
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('Hand-off › Specialist packet', () => {
  it('on a planning → dynamics project: the dispatch source, ranked snapshots with bundles, the ledger', async () => {
    useUIStore.setState({ currentProject: 'study-a' })
    api.status.mockResolvedValue({ status: 'completed', bundles: { '12': 'b12.zip' } })
    api.snapshots.mockResolvedValue([{ hour: 12 }])
    api.ledger.mockResolvedValue({ entries: [] })
    api.config.mockResolvedValue({})
    renderTab()
    expect((await screen.findByTestId('packet-source')).textContent).toBe('Dispatch from the solved network of oj-grid.')
    expect((await screen.findByTestId('snapshots-stub')).textContent).toBe('1 rows, 1 bundles')
    expect(await screen.findByTestId('ledger-stub')).toBeTruthy()
  })

  it('on a study that has not run, says there are no hours or bundles yet', async () => {
    useUIStore.setState({ currentProject: 'study-a' })
    api.status.mockResolvedValue({ status: 'idle', bundles: {} })
    api.ledger.mockResolvedValue({ entries: [] })
    api.config.mockResolvedValue({})
    renderTab()
    expect((await screen.findByTestId('packet-not-run')).textContent).toContain('has not run yet')
    expect(api.snapshots).not.toHaveBeenCalled()
  })

  it('on any other project, says what the packet will hold', async () => {
    useUIStore.setState({ currentProject: 'plain' })
    api.status.mockRejectedValue(Object.assign(new Error('x'), { isAxiosError: true, response: { status: 409, data: { detail: 'not a study' } } }))
    renderTab()
    expect((await screen.findByTestId('packet-empty')).textContent).toContain('PSS/E bundles')
  })
})
