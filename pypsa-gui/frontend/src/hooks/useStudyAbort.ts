import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { resultsApi } from '../api/simulation'
import { nk } from '../utils/queryKeys'

/** Shared by the Expert header and the Guided run: one sentence, one abort. */
export const STUDY_ABORT_TITLE = 'Stops at the next stage boundary; the network is left as it was.'
export const STUDY_ABORT_ERROR = 'The study could not be stopped. Try again in a moment.'

/** Abort the Guided goal study. `aborting` holds from the click until the study leaves `running`
 *  (not just while the POST is pending), so the button cannot be pressed twice. */
export function useStudyAbort(project: string | null, running: boolean) {
  const qc = useQueryClient()
  const [requested, setRequested] = useState(false)
  const abort = useMutation({
    mutationFn: () => resultsApi.abortEhStudy(),
    onSuccess: () => {
      setRequested(true)
      void qc.invalidateQueries({ queryKey: nk(project, 'results', 'eh_study') })
    },
    onError: () => { toast.error(STUDY_ABORT_ERROR) },
  })
  useEffect(() => { if (!running) setRequested(false) }, [running])
  return { abort, aborting: abort.isPending || (requested && running) }
}
