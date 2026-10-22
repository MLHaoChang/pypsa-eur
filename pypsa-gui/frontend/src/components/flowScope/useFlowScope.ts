// The project's scope (W4a): which work cards the one Run computes. Read from
// `GET …/flow/scope` (the stored scope, or the template's preset) and written
// with `PUT`; a write refreshes the flow, whose items carry `in_scope`.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getFlowScope, inScope, putFlowScope, type FlowScopeValue, type ScopeCardId,
} from '../../api/flow'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

export const flowScopeKey = (project: string | null) => nk(project, 'flow', 'scope')

export function useFlowScope() {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: flowScopeKey(project),
    queryFn: () => getFlowScope(project!),
    enabled: !!project,
  })
  const save = useMutation({
    mutationFn: (scope: FlowScopeValue) => putFlowScope(project!, scope),
    onSuccess: scope => {
      qc.setQueryData(flowScopeKey(project), (old: typeof q.data) => old && { ...old, scope })
      // The flow's states, notes and run request follow the scope.
      void qc.invalidateQueries({ queryKey: nk(project, 'flow') })
      // The scope owns run options in the solver config (Asset sizing, Load flow).
      void qc.invalidateQueries({ queryKey: nk(project, 'solverConfig') })
    },
  })
  return { ...q, registry: q.data ?? null, scope: q.data?.scope ?? null, save }
}

/** Whether a card (or one sub-option) is in scope. While the scope is unread
 *  everything shows, so a block never flickers away before it is known. */
export function useInScope(card: ScopeCardId, option?: string): boolean {
  const { scope, isSuccess } = useFlowScope()
  return !isSuccess || inScope(scope, card, option)
}

/** Whether a sub-option the scope owns (Load flow, N-1 security; W4b) is on,
 *  live: the scope is what the scope cards write, so a reader beside them
 *  never shows a hydrated copy of the solver config. `fallback` is used until
 *  the scope is read. */
export function useScopeOptionOn(card: ScopeCardId, option: string, fallback: boolean): boolean {
  const { scope } = useFlowScope()
  return scope ? inScope(scope, card, option) : fallback
}

export const useLoadFlowOn = (fallback: boolean) => useScopeOptionOn('grid', 'load_flow', fallback)
export const useN1On = (fallback: boolean) => useScopeOptionOn('grid', 'n1_sclopf', fallback)
