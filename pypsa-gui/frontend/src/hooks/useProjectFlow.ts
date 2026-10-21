import { useQuery } from '@tanstack/react-query'
import { getProjectFlow, type FlowSection, type FlowSectionId, type ProjectFlow } from '../api/flow'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

/** The open project's flow (`GET /projects/{name}/flow`), shared by the
 *  Guided rail, the Expert sidebar, Issues and the Run's reason. One query key,
 *  so every reader sees the same state; asset writes invalidate it
 *  (`COMPONENT_QUERY_ROOTS`). A failed read is `isError` with no data: callers
 *  show "not known yet", never a tick (ADR-0001). */
export function useProjectFlow() {
  const project = useUIStore(s => s.currentProject)
  const q = useQuery<ProjectFlow>({
    queryKey: nk(project, 'flow'),
    queryFn: () => getProjectFlow(project as string),
    enabled: !!project,
    staleTime: 5_000,
    retry: false,
  })
  const flow = q.data ?? null
  const section = (id: FlowSectionId): FlowSection | null =>
    flow?.sections.find(s => s.section === id) ?? null
  return { flow, section, isError: q.isError, isPending: q.isPending && !!project, refetch: q.refetch }
}
