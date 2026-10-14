// The one entry into a decision study (U3; owner decision 5): the question
// cards of the New-project wizard's `decision` tab and of Guided's Start card
// both start a study through this hook, so availability, the reason a card is
// disabled and what "Start" does are stated once.
//
// A question-first study creates its OWN base project (plan S4 M0) and changes
// none of the user's. Its intake is answered in the `decision` panel before
// the study exists; `path` is only the project the create call names, for
// authorisation and the load upload. Where the backend refuses the study
// routes (multi-user mode) the card is disabled with the reason; it never
// offers a dead end.
import { useQuery } from '@tanstack/react-query'
import { decisionStudiesApi } from '../../api/decisionStudies'
import { useAuthMode } from '../../auth/AuthModeProvider'
import { useUIStore } from '../../store/uiStore'
import { errorCopy } from '../../utils/decisionVocabulary'
import { useDecisionStore } from './decisionStore'

export interface GuidedQuestion {
  /** The backend's question template id (`services/study/questions`). */
  id: string
  title: string
  blurb: string
  /** The draft study's name until the user renames it. */
  draftName: string
}

/** The questions a study can answer today, in the owner's order (decision 3). */
export const GUIDED_QUESTIONS: readonly GuidedQuestion[] = [
  {
    id: 'bess_at_site',
    title: 'Do I need a battery at my site?',
    blurb: 'From your load and your tariff: whether a battery pays for itself, what size to build, what it is worth, '
      + 'and how sure that is. You get a yes/no, a size, a value and a report.',
    draftName: 'Battery at my site',
  },
]

export const NO_PROJECT_REASON =
  'Create or open a project first: the study needs one to authorise it (nothing in it is changed).'

export interface DecisionEntry {
  /** Why the questions cannot start now, in words; null when they can. */
  reason: string | null
  ready: boolean
  /** Open the question's intake in the decision panel. No-op unless `ready`. */
  start: (q: GuidedQuestion) => boolean
}

export function useDecisionEntry(path: string | null): DecisionEntry {
  const { authEnabled } = useAuthMode()
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const startDraft = useDecisionStore(s => s.startDraft)
  const avail = useQuery({
    queryKey: ['decision', path ?? '', 'availability'],
    queryFn: () => decisionStudiesApi.availability(path!),
    enabled: !authEnabled && !!path,
    retry: false,
  })
  let reason: string | null = null
  if (authEnabled) {
    const c = errorCopy('decision_studies_unavailable'); reason = `${c.title} ${c.action}`
  } else if (!path) {
    reason = NO_PROJECT_REASON
  } else if (avail.data && !avail.data.available) {
    const c = errorCopy(avail.data.code); reason = `${c.title} ${c.action}`
  } else if (avail.isError) {
    reason = 'Could not check whether decision studies are available. Try again.'
  }
  const ready = !reason && avail.data?.available === true

  const start = (q: GuidedQuestion) => {
    if (!path || !ready) return false
    startDraft({ pathProject: path, name: q.draftName, baseName: '' })
    setSlidePanel('decision')
    return true
  }
  return { reason, ready, start }
}
