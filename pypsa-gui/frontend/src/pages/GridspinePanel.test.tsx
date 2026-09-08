// The Planning → dynamics panel renders what the backend REPORTS and does one
// POST per action — nothing derived client-side that the copilot's
// `gridspine_*` tools could not also see. So these tests hold the panel to its
// inputs: a stage-status payload becomes six stage rows; a completed study
// shows its ranked hours; the ledger's provenance counts appear as tags; the
// Run button posts to the run endpoint; and the two "not applicable" answers —
// no open project, and a capacity-expansion project's 409 — render as guidance
// rather than as an error toast per poll.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { Ledger, RankedSnapshot, StageStatus, StudyConfig } from '../api/gridspine'
import GridspinePanel from './GridspinePanel'

const store = vi.hoisted(() => ({ currentProject: 'Study A' as string | null }))

vi.mock('../store/uiStore', () => ({
  useUIStore: (sel: (s: { currentProject: string | null }) => unknown) => sel({ currentProject: store.currentProject }),
}))

const queue = vi.hoisted(() => ({ activeJob: undefined as { id: string } | undefined }))
vi.mock('../hooks/useSolveQueue', () => ({
  QUEUE_KEY: ['solveQueue'],
  useSolveQueue: () => ({ data: { jobs: [], current: null }, isLoading: false, isError: false }),
  activeJobForProject: () => queue.activeJob,
}))

// `vi.mock` factories are hoisted above every import and const, so the mock
// object they close over has to be hoisted with them.
const api = vi.hoisted(() => ({
  status: vi.fn(),
  snapshots: vi.fn(),
  ledger: vi.fn(),
  run: vi.fn(),
  setDispatchSource: vi.fn(),
  bundle: vi.fn(),
  config: vi.fn(),
  updateConfig: vi.fn(),
}))
vi.mock('../api/gridspine', async () => {
  const real = await vi.importActual<typeof import('../api/gridspine')>('../api/gridspine')
  return { ...real, gridspineApi: api }
})

const done = (state: 'done' | 'pending' | 'running' | 'failed' | 'aborted', n = 1) => ({ state, done: n, total: n })

const completed: StageStatus = {
  status: 'completed', resumable: true, error: null,
  selected_hours: [7, 19], converged_hours: [7, 19],
  bundles: { '7': 'bundle_h7', '19': 'bundle_h19' },
  stages: {
    ingest: done('done'), dispatch: done('done'), ranking: done('done'),
    loadflow: done('done', 2), screening: done('done', 2), handoff: done('done', 2),
  },
}

const snapshots: RankedSnapshot[] = [
  { hour: 19, reasons: ['max_load_mw'], converged: true, load_mw: 6100, import_mw: 0, inertia_mws: 40000,
    inertia_excl_equiv_mws: 20000, ibr_share: 0.12, n1_severity_dc: 1.1, n1_severity_ac: 2.3 },
  { hour: 7, reasons: ['min_inertia_excl_equiv_mws', 'max_ibr_share'], converged: true, load_mw: 3200, import_mw: 0,
    inertia_mws: 9000, inertia_excl_equiv_mws: 7780, ibr_share: 0.61, n1_severity_dc: 0.5, n1_severity_ac: 41.7 },
]

const ledger: Ledger = {
  entries: ['a', 'b', 'c'], provenance_counts: { measured: 0, datasheet: 96, assumed: 72 },
  measurements: {}, hour: 19, from_run: true,
  edits: [{ unit_id: 'G_BUS_32', param: 'h_s', value: 4.25, source: 'datasheet', edited_by: 'chat', at: 'now' }],
}

const config: StudyConfig = {
  hours: 8760, k: 5, window: 168, overlap: 24, screen: true, n2_prune_threshold_pct: 0, from_dispatch: null,
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><GridspinePanel /></QueryClientProvider>)
}

beforeEach(() => {
  store.currentProject = 'Study A'
  queue.activeJob = undefined
  vi.clearAllMocks()
  api.config.mockResolvedValue(config)
  api.updateConfig.mockImplementation(async (_p: string, patch: Partial<StudyConfig>) => ({ ...config, ...patch }))
  api.status.mockResolvedValue(completed)
  api.snapshots.mockResolvedValue(snapshots)
  api.ledger.mockResolvedValue(ledger)
  api.run.mockResolvedValue({ id: 'job-1', project_id: 'Study A', kind: 'gridspine', status: 'queued', position: 1 })
})
afterEach(() => cleanup())

describe('GridspinePanel', () => {
  it('renders every stage from the status payload, with per-hour counts', async () => {
    renderPanel()
    for (const stage of ['ingest', 'dispatch', 'ranking', 'loadflow', 'screening', 'handoff']) {
      expect(await screen.findByTestId(`stage-${stage}`)).toBeTruthy()
    }
    expect(screen.getByTestId('stage-loadflow').textContent).toContain('2/2 hours')
    expect(api.status).toHaveBeenCalledWith('Study A')
  })

  it('shows the ranked hours of a completed study, sorted by hour, with their reasons', async () => {
    renderPanel()
    const rows = await screen.findAllByTestId(/^snapshot-/)
    expect(rows.map(r => r.getAttribute('data-testid'))).toEqual(['snapshot-7', 'snapshot-19'])
    expect(screen.getByTestId('snapshot-7').textContent).toContain('min inertia')
    expect(screen.getByTestId('snapshot-7').textContent).toContain('max IBR share')
    expect(screen.getByTestId('snapshot-19').textContent).toContain('peak load')
  })

  it('shows the ledger provenance counts and the recorded edits with who made them', async () => {
    renderPanel()
    const counts = await screen.findByTestId('provenance-counts')
    expect(counts.textContent).toContain('96 datasheet')
    expect(counts.textContent).toContain('72 assumed')
    expect(screen.getByTestId('template-edits').textContent).toContain('G_BUS_32.h_s = 4.25 (datasheet, edited by chat)')
  })

  it('posts to the run endpoint for the open project when Run is clicked', async () => {
    renderPanel()
    const btn = await screen.findByRole('button', { name: /run study|resume/i })
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false))
    await userEvent.click(btn)
    await waitFor(() => expect(api.run).toHaveBeenCalledWith('Study A'))
  })

  it('does not fetch snapshots for a study that has not completed', async () => {
    api.status.mockResolvedValue({
      ...completed, status: 'running', selected_hours: [], bundles: {},
      stages: { ...completed.stages, dispatch: done('running'), ranking: done('pending', 0),
                loadflow: done('pending', 0), screening: done('pending', 0), handoff: done('pending', 0) },
    })
    renderPanel()
    await screen.findByTestId('stage-dispatch')
    expect(api.snapshots).not.toHaveBeenCalled()
    expect(screen.queryByText(/ranked snapshots/i)).toBeNull()
  })

  it('renders an aborted run’s error artifact inline', async () => {
    api.status.mockResolvedValue({
      ...completed, status: 'aborted', bundles: {},
      error: { stage: 'dispatch', cause: "StudyAborted('aborted during dispatch')", element_ids: [] },
      stages: { ...completed.stages, dispatch: done('aborted') },
    })
    renderPanel()
    expect((await screen.findByTestId('study-error')).textContent).toContain('aborted during dispatch')
  })

  it('explains a capacity-expansion project instead of erroring, on the 409 the backend returns', async () => {
    api.status.mockRejectedValue({ response: { status: 409, data: { detail: 'not planning' } } })
    renderPanel()
    expect(await screen.findByText(/not a planning → dynamics project/i)).toBeTruthy()
    expect(api.snapshots).not.toHaveBeenCalled()
  })

  it('shows the config the next run will use and PUTs only the fields that were edited', async () => {
    renderPanel()
    const k = await screen.findByLabelText('k (hours per criterion)') as HTMLInputElement
    expect(k.value).toBe('5')
    expect((screen.getByLabelText('Hours') as HTMLInputElement).value).toBe('8760')
    const save = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)                       // nothing changed yet
    await userEvent.clear(k)
    await userEvent.type(k, '3')
    await userEvent.click(screen.getByLabelText(/screen n-1/i))
    await waitFor(() => expect(save.disabled).toBe(false))
    await userEvent.click(save)
    await waitFor(() => expect(api.updateConfig).toHaveBeenCalledWith('Study A', { k: 3, screen: false }))
    expect(api.updateConfig).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(save.disabled).toBe(true))  // the draft is spent
    expect(api.config).toHaveBeenCalledWith('Study A')
  })

  it('locks the config while a job for this project is queued or running', async () => {
    queue.activeJob = { id: 'job-9' }
    renderPanel()
    const k = await screen.findByLabelText('k (hours per criterion)') as HTMLInputElement
    expect(k.disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/locked while the study is queued or running/i)).toBeTruthy()
  })

  it('asks for a project when none is open', () => {
    store.currentProject = null
    renderPanel()
    expect(screen.getByText(/open a planning → dynamics project/i)).toBeTruthy()
    expect(api.status).not.toHaveBeenCalled()
  })
})
