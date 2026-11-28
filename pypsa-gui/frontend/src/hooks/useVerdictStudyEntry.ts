// Whether the project has a verdict (reliability) study for Run to start:
// the one Run entry (template-e2e 17, TE2E-17-RUN-1). A site with no study
// to run (generation-only) keeps the optimisation as its one Run
// (GENONLY-RUN-1). Shared by Guided's section-9 Run and the header's Run.
import { useFlowScope, useInScope } from '../components/flowScope/useFlowScope'

export function useVerdictStudyEntry() {
  const { registry } = useFlowScope()
  const reliabilityOn = useInScope('reliability', 'goal_study')
  const goalOpt = registry?.cards.find(c => c.id === 'reliability')
    ?.options.find(o => o.id === 'goal_study')
  const solveOnly = goalOpt != null && goalOpt.status !== 'available'
  return { reliabilityOn, goalOpt, solveOnly, // Only where the registry offers it: an unread registry keeps the optimisation.
    hasStudy: goalOpt?.status === 'available' && reliabilityOn }
}
