// Mounted in the app header (plan S8, spec §8.2 "after a solve in a study, the
// Verdict screen opens and a success toast names the verdict"). While a
// decision-study run is watched it polls the run STATUS route (never the
// findings); when the run ends it re-reads what the run produced once, opens
// the verdict and toasts its class. The study's own fork solves are not the
// active project's queue job, so the header's queue toasts never fire for them.
import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { useUIStore } from '../../store/uiStore'
import { RUN_STATUS_LABELS, VERDICT_LABELS, VERDICT_NOT_ESTABLISHED } from '../../utils/decisionVocabulary'
import { dq, invalidateDerived, studyKey } from './decisionQueries'
import { pollInterval } from './decisionModel'
import { useDecisionStore } from './decisionStore'

export default function DecisionRunWatcher({ pollMs = 2000 }: { pollMs?: number }) {
  const watch = useDecisionStore(s => s.watch)
  const qc = useQueryClient()
  const q = useQuery({
    ...dq.run(watch?.project ?? '', watch?.studyId ?? ''),
    enabled: !!watch,
    refetchInterval: query => pollInterval(query.state.data ?? undefined, pollMs),
  })
  const rec = q.data
  useEffect(() => {
    if (!watch || !rec || rec.status === 'running' || rec.study_id !== watch.studyId) return
    const { project, studyId } = watch
    useDecisionStore.getState().setWatch(null)
    void qc.invalidateQueries({ queryKey: [...studyKey(project, studyId), 'study'] })
    void qc.invalidateQueries({ queryKey: [...studyKey(project, studyId), 'ledger'] })
    invalidateDerived(qc, project, studyId)
    void qc.fetchQuery(dq.findings(project, studyId)).then(out => {
      const v = out.data?.verdict
      const cls = out.data?.available && v?.status === 'ok' && v.class ? VERDICT_LABELS[v.class] : VERDICT_NOT_ESTABLISHED
      if (rec.status === 'done') toast.success(`Decision study finished: ${cls}`)
      else toast(`Decision study ${RUN_STATUS_LABELS[rec.status].toLowerCase()}: ${cls}`)
      useDecisionStore.getState().openStudy({ project, studyId }, 'verdict')
      useUIStore.getState().setSlidePanel('decision')
    })
  }, [rec, watch, qc])
  return null
}
