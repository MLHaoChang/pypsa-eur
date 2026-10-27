import { useProjectFlow } from './useProjectFlow'

/** How many of the run kinds the Run requests have results older than the
 *  inputs (`results.freshness_by_kind`, W4b): the "Run (n stale)" count. A kind
 *  the Run does not request, a study-less project's null study and a flow that
 *  could not be read count nothing. */
export function useStaleRunKinds(): number {
  const { flow } = useProjectFlow()
  if (!flow || flow.deferred) return 0
  const by = flow.sections.find(s => s.section === 'results')?.freshness_by_kind
  if (!by) return 0
  const req = flow.run_request
  return [req.optimisation && by.optimisation, req.study && by.study, req.loadflow && by.loadflow]
    .filter(f => f === 'stale').length
}
