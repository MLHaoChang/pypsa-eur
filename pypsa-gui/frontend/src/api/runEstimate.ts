import client from './client'
import type { SolveJobKind } from './solveQueue'

/**
 * How long a queued run is likely to take (run progress spec §2; backend
 * `services/run_estimate.py`). `seconds` is null when the project has fewer
 * than two comparable past runs: unknown, never 0. `samples` is the basis.
 */
export interface RunEstimate {
  kind: SolveJobKind | string
  seconds: number | null
  samples: number
  run_ids: string[]
}

export const runEstimateApi = {
  get: (jobId: string) =>
    client.get<RunEstimate>(`/simulation/queue/${jobId}/estimate`).then(r => r.data),
}
