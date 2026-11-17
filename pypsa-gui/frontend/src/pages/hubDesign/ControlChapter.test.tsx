import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import type { IslandReport } from '../../api/campusElectrical'

const api = vi.hoisted(() => ({ islandReport: vi.fn(), state: vi.fn() }))
vi.mock('../../api/campusElectrical', async (orig) => {
  const actual = await orig<typeof import('../../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, islandReport: api.islandReport, state: api.state } }
})
const scope = vi.hoisted(() => ({ inScope: true }))
vi.mock('../../components/flowScope/useFlowScope', async (orig) => ({
  ...(await orig<typeof import('../../components/flowScope/useFlowScope')>()),
  useInScope: () => scope.inScope,
}))
vi.mock('../../components/flowScope/ScopeCards', () => ({
  AddToScope: ({ testId }: { testId: string }) => <span data-testid={testId}>Outside scope · Add</span>,
}))
import { ControlChapter, controlFindings } from './ControlChapter'

const table = (id: string, columns: string[], rows: unknown[][]) =>
  ({ id, group: 'study' as const, title: id, model: 'hub LP', caveat: '', columns, rows, assumed_rows: [] })
const REPORT: IslandReport = { caveat: '', missing: [], tables: [
  table('costs', ['scenario', 'status', 'objective', 'cost of island capability'],
    [['baseline (no island rows)', 'ok', 3473806, 0], ['island', 'ok', 3657931, 184125]]),
  table('binding', ['scenario', 'row', 'hours binding', 'sum of duals'], [['island', 'reserve', 3, 12.5]]),
] }

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><ControlChapter /></QueryClientProvider>)
}

beforeEach(() => {
  scope.inScope = true
  api.state.mockResolvedValue({ stale: false })
  useUIStore.setState({ currentProject: 'Demo', uiMode: 'guided' }) })
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('ControlChapter (control-strategy 06)', () => {
  it('reads the cost of island capability and the limits reached from the report', () => {
    expect(controlFindings(REPORT)).toEqual({ costs: [{ scenario: 'island', eur: 184125 }], limits: 1 })
    expect(controlFindings({ ...REPORT, tables: [] })).toBeNull()
    expect(controlFindings(null)).toBeNull()
    expect(controlFindings({} as IslandReport)).toBeNull()
  })

  it('shows the island study and opens its detail in Expert', async () => {
    api.islandReport.mockResolvedValue(REPORT)
    mount()
    expect((await screen.findByTestId('hub-chapter-control-costs')).textContent).toBe('Island: €\u00a0184\u00a0k')
    expect(screen.getByTestId('hub-chapter-control-limits').textContent).toBe('1 island limit was reached.')
    fireEvent.click(screen.getByTestId('hub-chapter-control-open'))
    expect(screen.getByTestId('hub-chapter-control-open').textContent).toBe('Open in Expert ›')
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
  })

  it('names each scenario in the words Expert uses ("Seamless", not "seamless")', async () => {
    api.islandReport.mockResolvedValue({ ...REPORT, tables: [
      table('costs', ['scenario', 'status', 'objective', 'cost of island capability'],
        [['baseline (no island rows)', 'ok', 1, 0], ['seamless', 'ok', 2, 1000], ['ups_bridged', 'ok', 3, 2000]]),
    ] })
    mount()
    const items = (await screen.findByTestId('hub-chapter-control-costs')).querySelectorAll('li')
    expect([...items].map(li => li.textContent?.split(':')[0])).toEqual(['Seamless', 'UPS-bridged'])
  })

  it('shows its figures in the same inner tile as the other chapters', async () => {
    api.islandReport.mockResolvedValue(REPORT)
    mount()
    const tile = await screen.findByTestId('hub-chapter-control-tile')
    expect(tile.className).toContain('rounded-[8px] border border-border bg-bg-2')
    expect(tile.contains(screen.getByTestId('hub-chapter-control-costs'))).toBe(true)
  })

  it('marks the figures out of date after an edit, as Expert does', async () => {
    api.islandReport.mockResolvedValue(REPORT)
    api.state.mockResolvedValue({ stale: true })
    mount()
    expect((await screen.findByTestId('hub-chapter-control-stale')).textContent).toContain('Out of date')
    expect(screen.getByTestId('hub-chapter-control-costs').className).toContain('opacity-60')
  })

  it('carries no out-of-date mark while the study is current', async () => {
    api.islandReport.mockResolvedValue(REPORT)
    mount()
    await screen.findByTestId('hub-chapter-control-costs')
    await new Promise(r => setTimeout(r, 0))
    expect(api.state).toHaveBeenCalledWith('Demo')
    expect(screen.queryByTestId('hub-chapter-control-stale')).toBeNull()
  })

  it('stays visible, greyed, with "Outside scope · Add" when Control is out of scope (RC3)', async () => {
    scope.inScope = false
    api.islandReport.mockResolvedValue(REPORT)
    mount()
    await screen.findByTestId('hub-chapter-control-out-of-scope')
    expect(screen.queryByTestId('hub-chapter-control-open')).toBeNull()
    expect(screen.getByTestId('hub-chapter-control').className).toContain('opacity-60')
  })

  it('is absent with no island study', async () => {
    api.islandReport.mockRejectedValue(new Error('409'))
    mount()
    await new Promise(r => setTimeout(r, 0))
    expect(screen.queryByTestId('hub-chapter-control')).toBeNull()
  })
})
