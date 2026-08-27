// ADR-0001 ("Unresolvable figures ship as null, never as a defaulted zero")
// coverage for the sidebar's Issues badge.
//
// Before this fix, `const issueCount = (preflight?.errors ?? 0) + (preflight?.warnings ?? 0)`
// read straight off the query's `data` and never consulted its error state.
// Any failure of GET /api/simulation/preflight (409 lock conflict, 503,
// auth, a network blip) left `data` undefined, `issueCount` computed to 0,
// and the badge simply didn't render — indistinguishable from "checked, zero
// issues". These tests pin that a failed fetch instead renders an explicit
// unavailable affordance, and that a real zero-issue success still renders
// the genuine clean (no-badge) state.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { simulationApi } from '../api/simulation'
import Sidebar from './Sidebar'

vi.mock('../api/network', () => ({
  networkApi: { getMeta: vi.fn().mockResolvedValue({ bus_count: 3 }) },
}))
vi.mock('../api/io', () => ({ ioApi: {} }))
vi.mock('../api/projects', () => ({
  projectsApi: { list: vi.fn().mockResolvedValue([]), save: vi.fn() },
}))
vi.mock('../api/simulation', () => ({
  simulationApi: { preflight: vi.fn() },
}))
vi.mock('../api/solveQueue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/solveQueue')>()
  return { ...actual, solveQueueApi: { list: vi.fn().mockResolvedValue({ jobs: [] }) } }
})
vi.mock('../api/localSettings', () => ({
  fetchLocalSettings: vi.fn().mockResolvedValue({ api_key_hint: null, log_path: '/tmp/app.log' }),
}))
vi.mock('../pages/ImportExport', () => ({ ImportZone: () => <div /> }))
vi.mock('./NewProjectWizard', () => ({ default: () => <div /> }))
vi.mock('../components/ProjectPicker', () => ({ default: () => <div /> }))
vi.mock('../utils/projectActions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/projectActions')>()
  return {
    ...actual,
    invalidateNetworkQueries: vi.fn(),
    saveProjectQuietly: vi.fn(),
    resetBackendNetwork: vi.fn(),
    downloadProjectBundle: vi.fn(),
    abortRunningSim: vi.fn(),
    switchToProject: vi.fn(),
  }
})

function renderSidebar() {
  // retry: false — a rejected preflight mock must surface as an error on
  // the first attempt, not get silently retried away in the test.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function issuesButton() {
  return screen.getByText('Issues').closest('button') as HTMLButtonElement
}

beforeEach(() => {
  cleanup()
  vi.mocked(simulationApi.preflight).mockReset()
  useUIStore.setState({
    sidebarMode: 'expanded',
    currentProject: 'Demo',
    projectName: 'Demo',
    activeSlidePanel: null,
    assistantDockOpen: false,
  })
})

describe('Sidebar Issues badge — preflight failure vs. clean vs. findings (ADR-0001)', () => {
  it('shows an unavailable affordance, not a clean/zero badge, when the preflight fetch errors', async () => {
    vi.mocked(simulationApi.preflight).mockRejectedValue(new Error('503 Service Unavailable'))
    renderSidebar()

    const badge = await screen.findByTitle('Could not check for issues — the preflight validation request failed')
    expect(badge).toBeTruthy()
    expect(badge.textContent).toBe('?')
    // Not the numeric zero/clean rendering — the row carries only the
    // unavailable badge, no digit badge alongside it.
    expect(issuesButton().textContent).not.toMatch(/\d/)
  })

  it('renders no badge at all on a genuine zero-issue success (regression guard)', async () => {
    vi.mocked(simulationApi.preflight).mockResolvedValue({ ok: true, errors: 0, warnings: 0, issues: [] })
    renderSidebar()

    // Wait for the query to settle before asserting absence.
    await screen.findByText('Issues')
    await vi.waitFor(() => expect(simulationApi.preflight).toHaveBeenCalled())
    await vi.waitFor(() => {
      expect(screen.queryByTitle(/Could not check for issues/)).toBeNull()
    })
    expect(issuesButton().textContent).toBe('Issues')
  })

  it('still renders the numeric issue-count badge on a successful fetch with findings', async () => {
    vi.mocked(simulationApi.preflight).mockResolvedValue({ ok: false, errors: 2, warnings: 1, issues: [] })
    renderSidebar()

    await vi.waitFor(() => {
      expect(issuesButton().textContent).toBe('Issues3')
    })
    expect(screen.queryByTitle(/Could not check for issues/)).toBeNull()
  })
})
