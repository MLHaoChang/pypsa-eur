// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/**
 * The dispatch solve as a Results headline reads it (control-strategy walkthrough 06, improvement 2):
 * whether one exists, its objective, when it was recorded, and whether it is current.
 *
 * Existence is the status endpoint's dispatch shape (`dispatch !== 'none'` with an objective), never the
 * stamp: a reopened project keeps its dispatch without a time. Current is the backend's own verdict for the
 * optimisation run (`results.freshness_by_kind.optimisation === 'fresh'`, the fingerprint rule Check
 * answers use) on top of a fresh dispatch; anything else, unknown included, is not current.
 */
import { useQuery } from '@tanstack/react-query'
import { simulationApi } from '../api/simulation'
import type { SimulationStatus } from '../api/types'
import type { FlowFreshness } from '../api/flow'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { useProjectFlow } from './useProjectFlow'

export interface DispatchRun {
  /** A solved dispatch exists (fresh or out of date). */
  solved: boolean
  current: boolean
  objective: number | null
  at: string | null
}

export function dispatchRun(status: SimulationStatus | null | undefined,
  optimisation: FlowFreshness | null | undefined): DispatchRun {
  const objective = status?.objective != null && Number.isFinite(status.objective) ? status.objective : null
  const solved = !!status && status.dispatch != null && status.dispatch !== 'none' && objective != null
  return {
    solved,
    current: solved && status?.dispatch === 'fresh' && optimisation === 'fresh',
    objective: solved ? objective : null,
    at: solved ? status?.solved_at ?? null : null,
  }
}

/** Undefined until the status has been read. */
export function useDispatchRun(): DispatchRun | undefined {
  const project = useUIStore(s => s.currentProject)
  const status = useQuery({
    queryKey: nk(project, 'simulationStatus'), queryFn: simulationApi.getStatus, enabled: !!project,
  })
  const { flow } = useProjectFlow()
  const optimisation = flow?.sections.find(s => s.section === 'results')?.freshness_by_kind?.optimisation
  return status.isSuccess ? dispatchRun(status.data, optimisation) : undefined
}
