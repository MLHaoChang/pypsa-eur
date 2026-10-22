// J9: Issues lists the findings under their flow section, in flow order, and
// an item opens its field beside the list (Issues stays, the selection moves)
// and clears when the preflight no longer reports it.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { simulationApi } from '../api/simulation'
import { networkApi } from '../api/network'
import * as flowApi from '../api/flow'
import { useUIStore } from '../store/uiStore'
import type { PreflightResult } from '../api/types'
import { nk } from '../utils/queryKeys'
import IssuesPanel from './IssuesPanel'

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

function flowOf(filed: Record<string, { code: string; entity: string }[]>): flowApi.ProjectFlow {
  return {
    sections: flowApi.FLOW_SECTIONS.map(section => ({
      section, label: section, required: true, summary: null,
      state: section === 'readiness' || section === 'results' ? null : (filed[section]?.length ? 'incomplete' : 'complete'),
      items: (filed[section] ?? []).map(f => ({
        code: f.code, severity: 'error', message: '', source: 'validation', blocking: true, acknowledged: false,
        target: { section, panel: null, component: null, entity: f.entity, field: null },
      })),
    })),
    run_ready: true, run_blocker: null, run_request: { optimisation: true, study: false },
    verdicts: { optimisation: { ready: true, blocker: null }, study: null },
    notes: [], deferred: false, deferred_reason: null,
  } as unknown as flowApi.ProjectFlow
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
afterEach(() => { useUIStore.setState({ currentProject: null, activeSlidePanel: null }) })

describe('IssuesPanel — by flow section', () => {
  it('files each finding under the section the flow files it, in flow order', async () => {
    vi.mocked(simulationApi.preflight).mockResolvedValue(preflight([
      issue('error', 'snapshots_empty', 'Snapshot', 'now'),
      issue('warning', 'load_zero', 'Load', 'l1'),
      issue('error', 'gen_bad', 'Generator', 'g1'),
    ]))
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue(flowOf({
      project: [{ code: 'snapshots_empty', entity: 'now' }],
      loads: [{ code: 'load_zero', entity: 'l1' }],
    }))
    const { container } = renderPanel()
    await screen.findByTestId('issues-section-project')
    await waitFor(() => expect(screen.getByTestId('issues-section-loads')).toBeTruthy())
    const order = Array.from(container.querySelectorAll('[data-testid^="issues-section-"]'))
      .map(el => el.getAttribute('data-testid')!.replace('issues-section-', ''))
    // Filed by the flow: project, loads. Unfiled by class: Generator → assets.
    expect(order).toEqual(['project', 'loads', 'assets'])
    expect(within(screen.getByTestId('issues-section-assets')).getByText('gen_bad on g1')).toBeTruthy()
  })

  it('a finding the flow cannot file lands under "Other checks", never dropped', async () => {
    vi.mocked(simulationApi.preflight).mockResolvedValue(preflight([issue('warning', 'odd', 'Mystery', 'x')]))
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue(flowOf({}))
    renderPanel()
    expect(await screen.findByTestId('issues-section-other')).toBeTruthy()
  })

  it('View opens the field beside the list: Issues stays open, the component is selected', async () => {
    vi.mocked(simulationApi.preflight).mockResolvedValue(preflight([issue('error', 'load_zero', 'Load', 'l1')]))
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue(flowOf({ loads: [{ code: 'load_zero', entity: 'l1' }] }))
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /View/ }))
    const ui = useUIStore.getState()
    expect(ui.activeSlidePanel).toBe('issues')
    expect(ui.selectedComponent).toEqual({ type: 'Load', name: 'l1' })
    expect(ui.rightPanelOpen).toBe(true)
  })

  it('the finding clears once the preflight stops reporting it', async () => {
    vi.mocked(simulationApi.preflight).mockResolvedValue(preflight([issue('error', 'load_zero', 'Load', 'l1')]))
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue(flowOf({ loads: [{ code: 'load_zero', entity: 'l1' }] }))
    renderPanel()
    await screen.findByTestId('issues-section-loads')
    vi.mocked(simulationApi.preflight).mockResolvedValue(preflight([]))
    await client.invalidateQueries({ queryKey: nk('Demo', 'preflight') })
    await waitFor(() => expect(screen.queryByTestId('issues-section-loads')).toBeNull())
    expect(screen.getByText('All checks passed')).toBeTruthy()
  })
})
