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
const getLockStatus = vi.fn()

vi.mock('../api/projects', () => ({
  projectsApi: { activate: (...a: unknown[]) => activate(...a) },
}))
vi.mock('../api/simulation', () => ({
  simulationApi: { getLockStatus: (...a: unknown[]) => getLockStatus(...a) },
}))
vi.mock('../api/network', () => ({ networkApi: {} }))
vi.mock('./pendingEdgeDeletes', () => ({
  flushPendingEdgeDeletes: vi.fn().mockResolvedValue(undefined),
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
