// The projects-home entry for decision studies (plan F1-F, F2; gate S8 [S5]).
//
// A study lives in its OWN base project, which is usually not the project the
// workbench reopens, so after a reload the panel used to list the studies of
// a user project that has none and invite a duplicate. This entry lists the
// studies of the project of the study last opened (`decisionStore`'s
// remembered ref, `GET /api/projects/{name}/studies`), marks that one, and
// opens a chosen study in the decision panel. It renders nothing without a
// remembered study, in auth mode (the routes refuse, BC-6), or when the
// routes are off; a project that is gone (404) is forgotten.
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { decisionStudiesApi as api, studyError } from '../../api/decisionStudies'
import { useAuthMode } from '../../auth/AuthModeProvider'
import { useUIStore } from '../../store/uiStore'
import { REOPEN_LABELS } from '../../utils/decisionVocabulary'
import { maturityLabel } from './decisionModel'
import { storedStudyRef, useDecisionStore } from './decisionStore'

export default function DecisionStudiesEntry({ className = '' }: { className?: string }) {
  const { authEnabled } = useAuthMode()
  const navigate = useNavigate()
  // The panel's study, else the stored one (a draft clears `active` in memory).
  const remembered = useDecisionStore(s => s.active) ?? storedStudyRef()
  const openStudy = useDecisionStore(s => s.openStudy)
  const forget = useDecisionStore(s => s.forget)
  const project = remembered?.project ?? null
  const q = useQuery({
    queryKey: ['decision', project ?? '', 'list'],
    queryFn: () => api.list(project!),
    enabled: !authEnabled && !!project,
    retry: false,
  })
  const err = q.error ? studyError(q.error) : null
  const gone = err?.status === 404 && !err.code?.startsWith('decision_studies_')
  useEffect(() => { if (gone && remembered) forget(remembered) }, [gone, remembered, forget])

  const studies = q.data ?? []
  if (authEnabled || !project || !remembered || studies.length === 0) return null

  const open = (studyId: string) => {
    openStudy({ project, studyId })
    useUIStore.getState().setSlidePanel('decision')
    navigate('/app')
  }
  return (
    <section aria-labelledby="decision-studies-heading" data-testid="decision-studies-entry" className={`space-y-3 ${className}`}>
      <h2 id="decision-studies-heading" className="text-xl font-semibold tracking-[-0.02em]">{REOPEN_LABELS.homeHeading}</h2>
      <p className="text-sm leading-6 text-[var(--brand-ink-dim)]">{REOPEN_LABELS.homeIntro(project)}</p>
      <ul className="flex list-none flex-col gap-2 p-0">
        {studies.map(s => (
          <li key={s.study_id} className="flex flex-wrap items-center gap-3">
            <button type="button" onClick={() => open(s.study_id)} aria-label={REOPEN_LABELS.openStudy(s.name)}
              className="text-sm font-semibold text-[var(--brand-red-soft)] underline underline-offset-4 hover:text-[var(--brand-ink)]">
              {s.name}
            </button>
            <span className="text-xs text-[var(--brand-ink-dim)]">{maturityLabel(s.maturity)}</span>
            {s.study_id === remembered.studyId && (
              <span className="rounded-full border border-white/14 px-2 py-0.5 text-[11px] text-[var(--brand-ink-dim)]">
                {REOPEN_LABELS.lastOpen}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
