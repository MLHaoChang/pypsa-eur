// template-e2e 09: Expert's Issues shows every assumed row Guided shows,
// through the same AssumptionsCard and words, by flow section, and counts them
// as the sidebar badge does (open, unacknowledged, in-scope sections only).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { simulationApi } from '../api/simulation'
import { networkApi } from '../api/network'
import * as flowApi from '../api/flow'
import { useUIStore } from '../store/uiStore'
import type { PreflightResult } from '../api/types'
import IssuesPanel from './IssuesPanel'

/** The card opens collapsed (guided-results 03): open each one to read its rows. */
const openCards = (box: HTMLElement) => {
  for (const t of within(box).getAllByTestId('assumptions-toggle')) fireEvent.click(t)
}

vi.mock('../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulation')>()
  return { ...actual, simulationApi: { ...actual.simulationApi, preflight: vi.fn() } }
})
vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getCarriers: vi.fn().mockResolvedValue([]) } }
})
vi.mock('../api/flow', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/flow')>()
  return { ...actual, getProjectFlow: vi.fn() }
})

const issue = (severity: 'error' | 'warning', code: string, component_class: string, name: string) =>
  ({ severity, code, component_class, name, message: `${code} on ${name}` })

function preflight(issues: ReturnType<typeof issue>[]): PreflightResult {
  return {
    ok: !issues.some(i => i.severity === 'error'),
    errors: issues.filter(i => i.severity === 'error').length,
    warnings: issues.filter(i => i.severity === 'warning').length,
    issues,
  } as PreflightResult
}

let client: QueryClient
function renderPanel() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><IssuesPanel /></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(simulationApi.preflight).mockReset()
  vi.mocked(flowApi.getProjectFlow).mockReset()
  vi.mocked(networkApi.getCarriers).mockResolvedValue([])
  useUIStore.setState({
    currentProject: 'Demo', activeSlidePanel: 'issues', selectedComponent: null, rightPanelOpen: false,
  })
})
afterEach(() => {
  useUIStore.setState({ currentProject: null, activeSlidePanel: null })
  localStorage.clear()
})

type Row = { code: string; message: string; acknowledged?: boolean; severity?: 'warning' | 'error'; source?: string }
function assumedFlow(rows: Partial<Record<flowApi.FlowSectionId, Row[]>>, outOfScope: flowApi.FlowSectionId[] = []): flowApi.ProjectFlow {
  return {
    sections: flowApi.FLOW_SECTIONS.map(section => ({
      section, label: section, required: true, summary: null,
      state: section === 'readiness' || section === 'results' || outOfScope.includes(section) ? null : 'incomplete',
      items: (rows[section] ?? []).map(r => ({
        code: r.code, severity: r.severity ?? 'warning', message: r.message, source: r.source ?? 'flow', blocking: false,
        acknowledged: r.acknowledged ?? false,
        target: { section, panel: null, component: null, entity: null, field: null },
      })),
    })),
    run_ready: true, run_blocker: null, run_request: { optimisation: true, study: false },
    verdicts: { optimisation: { ready: true, blocker: null }, study: null },
    notes: [], deferred: false, deferred_reason: null,
  } as unknown as flowApi.ProjectFlow
}

describe('IssuesPanel — Assumptions (Expert shows what Guided shows)', () => {
  beforeEach(() => { vi.mocked(simulationApi.preflight).mockResolvedValue(preflight([])) })

  it('lists each open assumed row under its section, with Guided\'s card and words', async () => {
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue(assumedFlow({
      scenarios: [{ code: 'assumed_target_lole_h', message: 'Reliability goal (LOLE): 3 h/yr (assumed)' }],
      assets: [
        { code: 'assumed_outage_rate', message: 'Outage rate: 2 %/yr (assumed)' },
        { code: 'assumed_repair_time', message: 'Repair time: 24 h (assumed)', acknowledged: true },
        { code: 'battery_missing', message: 'Add a battery', severity: 'error' },
      ],
      loads: [{ code: 'assumed_site_type', message: 'Site type: data centre (assumed)' }],
    }, ['loads']))
    renderPanel()
    const box = await screen.findByTestId('issues-assumptions')
    // In flow order; the out-of-scope section and the acknowledged and non-assumed rows are left out.
    expect(Array.from(box.querySelectorAll('[data-testid^="issues-assumptions-"]')).map(n => n.getAttribute('data-testid')))
      .toEqual(['issues-assumptions-assets', 'issues-assumptions-scenarios'])
    openCards(box)
    const rows = within(box).getAllByTestId('assumption-row').map(r => r.textContent)
    expect(rows.some(t => t?.includes('Outage rate: 2 %/yr'))).toBe(true)
    expect(rows.some(t => t?.includes('Reliability goal (LOLE): 3 h/yr'))).toBe(true)
    expect(rows.some(t => t?.includes('Repair time'))).toBe(false)
    expect(rows.some(t => t?.includes('Site type'))).toBe(false)
    expect(within(box).getAllByTestId('assumptions-card')).toHaveLength(2)
    expect(within(box).getAllByTestId('assumption-edit')).toHaveLength(2)
  })

  it('lists the open items only the flow reports, so the rows match the sidebar badge', async () => {
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue(assumedFlow({
      project: [{ code: 'site_location_missing', message: 'The site has no location: no bus is placed.' }],
      // A validation item is the preflight's (here: none), never a second row.
      loads: [{ code: 'load_zero', message: 'Load l1 is zero', severity: 'error', source: 'validation' }],
      assets: [{ code: 'assumed_outage_rate', message: 'Outage rate: 2 %/yr (assumed)' }],
    }))
    renderPanel()
    const row = await screen.findByTestId('issues-flow-item')
    expect(row.textContent).toContain('The site has no location: no bus is placed.')
    expect(within(screen.getByTestId('issues-section-project')).getByTestId('issues-flow-item')).toBe(row)
    expect(screen.queryByText('All checks passed')).toBeNull()
    // 1 flow-only row + 1 assumed row = the badge's 2 (open, in scope).
    const assumed = screen.getByTestId('issues-assumptions')
    expect(within(assumed).getByTestId('assumptions-toggle').textContent).toContain('1 not yet confirmed')
    openCards(assumed)
    expect(screen.getAllByTestId('issues-flow-item').length
      + within(assumed).getAllByTestId('assumption-row').length).toBe(2)
  })

  it('shows no Assumptions section when nothing is assumed', async () => {
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue(assumedFlow({}))
    renderPanel()
    await screen.findByText('All checks passed')
    expect(screen.queryByTestId('issues-assumptions')).toBeNull()
  })
})
