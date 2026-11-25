// The CO₂ limit goal's refusal (DC review 02): after a solve that found no
// design under the limit, the solve's own sentence (it names the limit, the
// least emissions reachable and the one extra solve that found it) and the
// way to the field that sets the limit, in the user's mode (UX GO on DC 02 (5)).
import { useQuery } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { openReliabilityGoal } from '../../layout/flowNavigation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

export const co2LimitKey = (project: string | null) => nk(project, 'results', 'co2_limit')

/** The link's words: the Goal step in Guided, Solver settings in Expert. */
export const RAISE_LIMIT = {
  guided: 'Raise the limit in the Goal step ›',
  expert: 'Raise the limit in Solver settings ›',
} as const
export const CHANGE_LIMIT = {
  guided: 'Change the limit in the Goal step ›',
  expert: 'Change the limit in Solver settings ›',
} as const

export function Co2LimitRefusal({ mode }: { mode: 'guided' | 'expert' }) {
  const project = useUIStore(s => s.currentProject)
  const { data } = useQuery({
    queryKey: co2LimitKey(project), queryFn: resultsApi.getCo2Limit, enabled: !!project, retry: false,
  })
  const refusal = data?.refusal
  if (!refusal) return null
  // Without the limit no design worked either: raising it would not help, so no link.
  const link = refusal.without_limit === 'infeasible' ? null
    : refusal.code === 'co2_cap_unreachable' ? RAISE_LIMIT[mode] : CHANGE_LIMIT[mode]
  const testId = mode === 'guided' ? 'hub-co2-refusal' : 'results-co2-refusal'
  return (
    <div role="status" data-testid={testId}
      className="flex flex-col gap-1 rounded border border-warn/40 bg-warn/10 px-3 py-2 text-[12px] text-text">
      <span>{refusal.message}</span>
      {link && (
        <button type="button" data-testid={`${testId}-link`} onClick={() => openReliabilityGoal()}
          className="self-start text-[12px] text-accent hover:underline">{link}</button>
      )}
    </div>
  )
}
