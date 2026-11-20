// The investment case's run, shared by Guided's Investment tile and Expert's
// Investment case (template-e2e issue 22, slice S2; lifted from
// InvestmentCaseView). One POST however often the button is clicked (GK B3 F2);
// a busy 409 and a refusal are kept for InvestmentCaseRunPanel to word.
import { useCallback, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { financeApi } from '../../../api/finance'
import { simulationApi } from '../../../api/simulation'
import type { InvestmentCaseStudy } from '../../../api/types'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { dispatchSolved } from '../../hubDesign/chapters'
import { blockerOf, type Blocker } from './investmentCaseNeeds'

const refetchWhileRunning = (q: { state: { data: unknown } }): number | false =>
  (q.state.data as InvestmentCaseStudy | null)?.status === 'running' ? 2000 : false

export interface InvestmentCaseRun {
  study: InvestmentCaseStudy | null
  /** The study record has been read (a 204 reads as null). */
  loaded: boolean
  running: boolean
  /** A start request is in flight. */
  starting: boolean
  start: () => void
  /** Why the last start or the stored run did not finish; null when nothing blocks. */
  blocker: Blocker | null
}

export function useInvestmentCaseRun(): InvestmentCaseRun {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const studyKey = nk(project, 'results', 'investment_case')
  const study = useQuery({ queryKey: studyKey, queryFn: () => financeApi.getInvestmentCase(),
                           refetchInterval: refetchWhileRunning })
  const s = study.data ?? null
  const running = s?.status === 'running'
  const [refusal, setRefusal] = useState<unknown>(null)
  // The guard is a ref, not isPending: two clicks in one frame both see isPending false.
  const inFlight = useRef(false)
  const run = useMutation({
    mutationFn: () => financeApi.startInvestmentCase(),
    onMutate: () => setRefusal(null),
    onSuccess: () => void qc.invalidateQueries({ queryKey: studyKey }),
    onError: (e: unknown) => setRefusal(e),
    onSettled: () => { inFlight.current = false },
  })
  const start = useCallback(() => {
    if (inFlight.current || running) return
    inFlight.current = true
    run.mutate()
  }, [running, run])

  // A start refusal outlives the stored record until the next start; a stored
  // refused run shows until it is replaced.
  const raw = blockerOf(refusal, refusal ? null : s)
  const needsGaps = raw?.kind === 'needs'
  const status = useQuery({ queryKey: nk(project, 'simulationStatus'), queryFn: simulationApi.getStatus,
                            enabled: !!project && needsGaps })
  const finance = useQuery({ queryKey: nk(project, 'simulation', 'finance'), queryFn: () => financeApi.getFinance(),
                             enabled: !!project && needsGaps, retry: false })
  const blocker = needsGaps
    ? blockerOf(refusal, refusal ? null : s, {
      solved: status.isSuccess ? dispatchSolved(status.data) : null,
      financeSet: finance.isSuccess ? finance.data?.finance != null : null,
    })
    : raw

  return { study: s, loaded: study.isSuccess, running, starting: run.isPending, start, blocker }
}
