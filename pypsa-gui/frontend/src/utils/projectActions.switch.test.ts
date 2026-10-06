// `switchToProject` and the backend's study refusal (whole-branch review,
// M8 / M12). `POST /projects/{id}/activate` now answers 409 with
// `error_kind: "study_in_flight"` and the study's own sentence when an
// adequacy study is running on the current project. Before this file every
// 409 became 'busy-solve', whose fixed copy tells the user to abort "the
// running solve" — and the Abort button it points at hits /simulation/abort,
// which does not stop a study. The backend sentence names the study and the
// remedy, so it is surfaced verbatim as 'busy-study'.
import { beforeEach, describe, expect, it, vi } from 'vitest'

const activate = vi.fn()
const save = vi.fn()
const list = vi.fn()
const getLockStatus = vi.fn()

vi.mock('../api/projects', () => ({
  projectsApi: {
    activate: (...a: unknown[]) => activate(...a),
    save: (...a: unknown[]) => save(...a),
    list: (...a: unknown[]) => list(...a),
  },
}))
const getEhStudy = vi.fn()
const getEhReview = vi.fn()
const getFmeaModes = vi.fn()
vi.mock('../api/simulation', () => ({
  simulationApi: { getLockStatus: (...a: unknown[]) => getLockStatus(...a) },
  resultsApi: {
    getEhStudy: (...a: unknown[]) => getEhStudy(...a),
    getEhReview: (...a: unknown[]) => getEhReview(...a),
    getFmeaModes: (...a: unknown[]) => getFmeaModes(...a),
  },
}))
vi.mock('../api/network', () => ({ networkApi: {} }))
vi.mock('./pendingEdgeDeletes', () => ({
  flushPendingEdgeDeletes: vi.fn().mockResolvedValue({ flushed: 0, failed: 0 }),
}))

const { switchToProject } = await import('./projectActions')
const { useUIStore } = await import('../store/uiStore')

const qc = {
  getQueryData: () => undefined,
  invalidateQueries: vi.fn().mockResolvedValue(undefined),
  removeQueries: vi.fn(),
} as never

function reject409(detail: unknown) {
  return Object.assign(new Error('Request failed with status code 409'), {
    response: { status: 409, data: { detail } },
  })
}

beforeEach(() => {
  activate.mockReset()
  save.mockReset()
  list.mockReset()
  list.mockResolvedValue([])
  save.mockResolvedValue({ ts_columns_saved: 0 })
  getLockStatus.mockReset()
  getLockStatus.mockResolvedValue({ lock_held: false, worker_alive: false })
  useUIStore.setState({ currentProject: null })
})

describe('switchToProject on a 409 from activate', () => {
  it("surfaces the backend's study sentence as 'busy-study'", async () => {
    const message =
      'Cannot switch projects while a frontier study is running — it re-solves the ' +
      'in-memory network between its own iterates. Wait for it to finish, or abort it, and retry.'
    activate.mockRejectedValue(
      reject409({ error_kind: 'study_in_flight', study: 'frontier', message }),
    )
    const r = await switchToProject('B', qc)
    expect(r).toEqual({ status: 'busy-study', message })
    expect(useUIStore.getState().currentProject).toBeNull()
  })

  it('with a current project, the refused pre-switch save is swallowed and the study sentence surfaces once', async () => {
    // The switch first saves the OUTGOING project quietly; during a study
    // that save is refused with the same structured 409. It must not stop
    // the switch from reaching activate, and the result must be the single
    // 'busy-study' the entry point toasts (the interceptor's own toast for
    // that code is quiet — `QUIET_TOAST_CODES` in api/client.ts).
    useUIStore.setState({ currentProject: 'A' })
    const message = 'Cannot save the project while a frontier study is running — wait or abort it.'
    save.mockRejectedValue(reject409({ error_kind: 'study_in_flight', study: 'frontier', message }))
    const switchMessage = 'Cannot switch projects while a frontier study is running — wait or abort it.'
    activate.mockRejectedValue(
      reject409({ error_kind: 'study_in_flight', study: 'frontier', message: switchMessage }),
    )
    const r = await switchToProject('B', qc)
    expect(save).toHaveBeenCalledTimes(1)
    expect(activate).toHaveBeenCalledTimes(1)
    expect(r).toEqual({ status: 'busy-study', message: switchMessage })
    expect(useUIStore.getState().currentProject).toBe('A')
  })

  it("keeps 'busy-solve' for the solver-in-flight refusal", async () => {
    activate.mockRejectedValue(
      reject409({
        error_kind: 'solver_in_flight',
        message: 'Finish or abort the running solve before switching projects.',
      }),
    )
    await expect(switchToProject('B', qc)).resolves.toEqual({ status: 'busy-solve' })
  })

  it("keeps 'busy-solve' for a bare string detail", async () => {
    activate.mockRejectedValue(reject409('Simulation already running'))
    await expect(switchToProject('B', qc)).resolves.toEqual({ status: 'busy-solve' })
  })

  it("keeps 'not-found' for a 404", async () => {
    activate.mockRejectedValue(
      Object.assign(new Error('404'), { response: { status: 404, data: { detail: 'nope' } } }),
    )
    await expect(switchToProject('B', qc)).resolves.toEqual({ status: 'not-found' })
  })
})


// ── A planning → dynamics study has no network to activate ─────────────────
//
// Found by driving the real app in Chromium, which no component test could:
// a study opened from the Projects page came up as "No project open" with a
// "Project '<id>' not found" toast. `/activate` hydrates a network context from
// `network.nc`, and a study deliberately has none — it is a config and a run
// directory — so the backend 404s for every study, and has since increment 4.
// It went unnoticed because the ONE path that ever opened a study, the
// new-project wizard, never calls `/activate`: it sets the current project and
// opens the study panel directly. So a study could be created and never
// re-opened. These hold `switchToProject` — which every other entry point uses
// (the project card, tabs, sidebar, command palette, workspace panel) — to what
// the wizard does.

const STUDY = { id: 's-uuid-1', name: 'Study S', project_kind: 'planning_dynamics' }
const NETWORK = { id: 'b-uuid-2', name: 'B', project_kind: null }
const cached = (projects: unknown[]) => ({
  getQueryData: (key: unknown[]) => (key[0] === 'projects' ? projects : undefined),
  invalidateQueries: vi.fn().mockResolvedValue(undefined),
  removeQueries: vi.fn(),
}) as never

describe('switchToProject with a planning → dynamics study', () => {
  it('opens a study by id without asking the backend to activate a network', async () => {
    const r = await switchToProject('s-uuid-1', cached([STUDY, NETWORK]))
    expect(activate).not.toHaveBeenCalled()
    expect(r).toEqual({ status: 'switched' })
    expect(useUIStore.getState().currentProject).toBe('Study S')
    expect(useUIStore.getState().activeSlidePanel).toBe('gridspine')
  })

  it('opens a study by name the same way', async () => {
    await switchToProject('Study S', cached([STUDY, NETWORK]))
    expect(activate).not.toHaveBeenCalled()
    expect(useUIStore.getState().currentProject).toBe('Study S')
  })

  it('looks the kind up when the project list is not cached yet', async () => {
    // The browser case exactly: `/app?project=<id>` on a fresh page load,
    // before anything has populated the ['projects'] query.
    list.mockResolvedValue([STUDY, NETWORK])
    const r = await switchToProject('s-uuid-1', qc)
    expect(list).toHaveBeenCalledTimes(1)
    expect(activate).not.toHaveBeenCalled()
    expect(r).toEqual({ status: 'switched' })
    expect(useUIStore.getState().currentProject).toBe('Study S')
  })

  it("does not save a network under a study's name on the way out", async () => {
    // Leaving a study used to save "the current project" — whatever network the
    // backend happened to hold — under the study's name.
    useUIStore.setState({ currentProject: 'Study S' })
    activate.mockResolvedValue({ activated: 'B', evicted: [] })
    await switchToProject('B', cached([STUDY, NETWORK]))
    expect(save).not.toHaveBeenCalled()
    expect(activate).toHaveBeenCalledWith('B')
  })

  it('still activates an ordinary project, and still saves an ordinary outgoing one', async () => {
    useUIStore.setState({ currentProject: 'A' })
    activate.mockResolvedValue({ activated: 'B', evicted: [] })
    const r = await switchToProject('B', cached([STUDY, NETWORK, { id: 'a', name: 'A', project_kind: null }]))
    expect(save).toHaveBeenCalledTimes(1)
    expect(activate).toHaveBeenCalledWith('B')
    expect(r).toEqual({ status: 'switched' })
  })

  it('falls back to the ordinary path when the kind cannot be looked up', async () => {
    // A failed list must not strand the user: the switch proceeds exactly as
    // it did before this change, and a genuine study then fails the way it
    // always did rather than in a new way.
    list.mockRejectedValue(new Error('network down'))
    activate.mockResolvedValue({ activated: 'B', evicted: [] })
    const r = await switchToProject('B', qc)
    expect(activate).toHaveBeenCalledWith('B')
    expect(r).toEqual({ status: 'switched' })
  })
})


// ── A6 (deferred spec 2026-09-28 §2.3): the hub's queries follow a switch ────
//
// A mid-study project switch must not leave project A's study, review or risk
// rows on project B's cards. Every hub query is keyed by project (`nk`), so
// after `switchToProject` A's queries have no observer left and B's are
// fetched — once each.
describe('switchToProject and the hub-design queries (A6)', () => {
  it("A's eh_study / eh_review / fmea_modes go inactive; B's are fetched once each", async () => {
    const { createElement } = await import('react')
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query')
    const { renderHook, waitFor } = await import('@testing-library/react')
    const { useHubStudy, useHubReview } = await import('../pages/hubDesign/useHubData')
    const { useLiveStudyRunning } = await import('../hooks/useLiveStudyRunning')
    const { nk } = await import('./queryKeys')

    useUIStore.setState({ currentProject: 'A' })
    const per = (p: string | null) => p === 'A'
      ? { study: { status: 'running' }, review: { status: 'ok', findings: [{ id: 'a-only' }] }, modes: { per_mode: [{ name: 'a_gen' }], sweep_status: null } }
      : { study: null, review: null, modes: { per_mode: [{ name: 'b_gen' }], sweep_status: null } }
    getEhStudy.mockImplementation(async () => per(useUIStore.getState().currentProject).study)
    getEhReview.mockImplementation(async () => per(useUIStore.getState().currentProject).review)
    getFmeaModes.mockImplementation(async () => per(useUIStore.getState().currentProject).modes)
    activate.mockResolvedValue({ activated: 'B', evicted: [] })

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      createElement(QueryClientProvider, { client }, children)
    renderHook(() => ({ study: useHubStudy(), review: useHubReview(true), live: useLiveStudyRunning() }),
      { wrapper })
    await waitFor(() => expect(client.getQueryData(nk('A', 'results', 'fmea_modes'))).toBeTruthy())
    await waitFor(() => expect(client.getQueryData(nk('A', 'results', 'eh_review'))).toBeTruthy())
    getEhStudy.mockClear(); getEhReview.mockClear(); getFmeaModes.mockClear()

    const r = await switchToProject('B', client as never)
    expect(r).toEqual({ status: 'switched' })

    await waitFor(() => expect(getFmeaModes).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(getEhStudy).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(getEhReview).toHaveBeenCalledTimes(1))
    for (const root of ['eh_study', 'eh_review', 'fmea_modes']) {
      const old = client.getQueryCache().find({ queryKey: nk('A', 'results', root), exact: true })
      expect(old?.isActive(), `A's ${root} still observed`).toBe(false)
      const cur = client.getQueryCache().find({ queryKey: nk('B', 'results', root), exact: true })
      expect(cur?.isActive(), `B's ${root} observed`).toBe(true)
    }
    // No cross-project rows: B's modes are B's.
    expect(JSON.stringify(client.getQueryData(nk('B', 'results', 'fmea_modes')))).not.toContain('a_gen')
    await new Promise(res => setTimeout(res, 50))
    expect(getFmeaModes).toHaveBeenCalledTimes(1)
    expect(getEhStudy).toHaveBeenCalledTimes(1)
  })
})
