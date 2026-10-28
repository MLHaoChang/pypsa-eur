// Step 1 — Start (guided-mode spec §5.3): an energy-hub example site or the
// network already open. Templates come from the New-project wizard's own
// table and are created by the wizard's own (lifted) mutation, so the G4
// mode rule and the workbench navigation are the wizard's.
//
// U3 (owner decision 5): two paths side by side. "Is my site reliable?" is
// the energy-hub flow above; "Is this investment worth it?" lists the
// decision-study questions and starts one through the wizard's own entry hook
// (`useDecisionEntry`): the intake opens in the decision panel, and the study
// builds its own project, so the open one is not changed.
import { useEffect, useState } from 'react'
import { FolderOpen, Hexagon, Scale, SlidersHorizontal } from 'lucide-react'
import { GUIDED_QUESTIONS, useDecisionEntry } from '../../decision/useDecisionEntry'
import { TEMPLATES } from '../../../layout/NewProjectWizard'
import { useCreateFromTemplate } from '../../../hooks/useCreateFromTemplate'
import { useUIStore } from '../../../store/uiStore'
import { BLOCKED_NO_PROJECT } from '../flow'
import { useHubDesignStore } from '../hubDesignStore'
import { CardShell } from '../shared/CardShell'
import { Term } from '../shared/Term'
import { useHubStudy, useHubTemplate } from '../useHubData'
import { STUDY_RUNNING_SWITCH } from '../../../hooks/useCreateFromTemplate'
import { GenericSiteForm } from './GenericSiteForm'
import { plainIds } from '../plainWords'
import SiteSetupSection from '../../../site3d/SiteSetupSection'

// The generic site (`eh_site`) is not in the table: it has its own tile and form.
const HUB_TEMPLATES = TEMPLATES.filter(t => t.id.startsWith('eh_'))

/** One plain line of purpose per example site. The wizard's own blurbs
 *  (Expert) name equipment a first-time user may not know. */
const PURPOSE: Record<string, string> = {
  eh_datacenter: 'A data center that must keep running through grid outages.',
  eh_h2_hub: 'An industrial site that makes hydrogen from wind and solar power, with a grid connection.',
  eh_microgrid: 'An island that must keep its hospital running without its link to the mainland.',
  eh_datacenter_staged: 'A data center that grows over the years: plan what to build, and when, as its load and grid connection grow.',
  eh_h2_hub_staged: 'A hydrogen-making industrial site whose customers buy more each year: plan what to build, and when.',
  eh_ammonia_staged: 'An industrial site that makes ammonia from wind and solar power for a delivery contract that grows: plan what to build, and when.',
  eh_ipp_hybrid: 'A wind and solar plant with an optional battery behind one grid connection: size the battery and weigh PPA against merchant revenue.',
  eh_microgrid_staged: 'An island whose demand grows over the years: plan what to build, and when, without the mainland link.',
}

/** The Start card's purpose line; a template without one falls back to the
 *  first sentence of the wizard's description. */
export function purpose(id: string, description: string): string {
  if (PURPOSE[id]) return PURPOSE[id]
  const i = description.indexOf('. ')
  return i < 0 ? description : description.slice(0, i + 1)
}

/** Internal phase ids ("(P19 template)") are not for the user. */
export function plainProvenance(text: string): string {
  return text.replace(/\s*\(P\d+[^)]*\)/g, '')
}

export function StartCard() {
  const project = useUIStore(s => s.currentProject)
  const setStep = useHubDesignStore(s => s.setStep)
  const create = useCreateFromTemplate()
  const { template } = useHubTemplate()
  // A new project would replace the network under a running study (the
  // backend refuses with 409): say so on the buttons instead.
  const { running } = useHubStudy()
  // The Site card's "Start a new project from these values" opens the form
  // with that site's values.
  const siteSeed = useHubDesignStore(s => s.siteSeed)
  const setSiteSeed = useHubDesignStore(s => s.setSiteSeed)
  const [formToggled, setSiteFormOpen] = useState(false)
  const siteFormRequested = useHubDesignStore(s => s.siteFormRequested)
  useEffect(() => {
    if (!siteFormRequested) return
    setSiteFormOpen(true)
    useHubDesignStore.getState().setSiteFormRequested(false)
  }, [siteFormRequested])
  const siteFormOpen = formToggled || siteSeed !== null

  return (
    <CardShell step="start" testId="hub-card-start" title="Start">
      {project && template && (
        <div data-testid="hub-start-provenance"
          className="flex flex-col gap-1 rounded border border-accent/40 p-3 text-[12px]">
          <span className="font-semibold text-text">This project: the {template.name} example</span>
          {template.provenance && (
            <span className="text-muted">
              <Term k="template_provenance">Where the numbers come from</Term>{' '}
              {plainProvenance(template.provenance)}
            </span>
          )}
          {template.study_notes?.[0] && (
            <span className="text-muted">{plainIds(template.study_notes[0])}</span>
          )}
        </div>
      )}

      <section data-testid="hub-start-reliable" aria-labelledby="hub-start-reliable-h" className="flex flex-col gap-3">
        <h3 id="hub-start-reliable-h" className="text-[13px] font-semibold text-text">Is my site reliable?</h3>
        <div className="flex flex-col gap-2">
          <span className="text-[12px] font-semibold text-text">Start from an example site</span>
          <div data-testid="hub-start-templates" className="grid gap-2 sm:grid-cols-3">
            {HUB_TEMPLATES.map(t => {
              const creating = create.isPending && create.variables === t.id
              return (
                <button key={t.id} type="button" data-testid={`hub-start-template-${t.id}`}
                  onClick={() => create.mutate(t.id)}
                  disabled={create.isPending || running}
                  title={running ? STUDY_RUNNING_SWITCH : `Create a new project from the ${t.name} example`}
                  className="flex flex-col gap-1 rounded-lg border border-border p-3 text-left hover:border-accent/60 hover:bg-accent/5 disabled:opacity-50">
                  <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-text">
                    <Hexagon size={12} className="text-accent" /> {t.name}
                    {creating && <span className="text-[10px] font-normal text-accent animate-pulse">creating…</span>}
                  </span>
                  <span className="text-[11px] leading-relaxed text-muted">{purpose(t.id, t.description)}</span>
                </button>
              )
            })}
            <button type="button" data-testid="hub-start-own-site"
              aria-expanded={siteFormOpen}
              onClick={() => { if (siteFormOpen) setSiteSeed(null); setSiteFormOpen(!siteFormOpen) }}
              className={`flex flex-col gap-1 rounded-lg border p-3 text-left hover:border-accent/60 hover:bg-accent/5
                ${siteFormOpen ? 'border-accent bg-accent/5' : 'border-border'}`}>
              <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-text">
                <SlidersHorizontal size={12} className="text-accent" /> Your own site
              </span>
              <span className="text-[11px] leading-relaxed text-muted">
                A factory, hospital, office or campus: set its load and grid connection, and plan what to build.
              </span>
            </button>
          </div>
          {siteFormOpen && (
            <GenericSiteForm key={siteSeed ? 'seed' : 'preset'} create={create} seed={siteSeed}
              onCancel={() => { setSiteSeed(null); setSiteFormOpen(false) }}
              disabledReason={running ? STUDY_RUNNING_SWITCH : null} />
          )}
        </div>

        <button type="button" data-testid="hub-start-own-network"
          onClick={() => setStep('site', { user: true })}
          disabled={!project}
          title={project ? 'Design a hub on the network that is open now' : BLOCKED_NO_PROJECT}
          className="self-start inline-flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-[12px] text-text hover:border-accent hover:text-accent disabled:opacity-50">
          <FolderOpen size={13} /> Use my network
        </button>
      </section>

      <InvestQuestions project={project} />

      <SiteSetupSection variant="guided" />
    </CardShell>
  )
}

/** "Is this investment worth it?": the decision-study questions (U3). */
function InvestQuestions({ project }: { project: string | null }) {
  const { reason, ready, start } = useDecisionEntry(project)
  return (
    <section data-testid="hub-start-invest" aria-labelledby="hub-start-invest-h"
      className="flex flex-col gap-2 border-t border-border pt-3">
      <h3 id="hub-start-invest-h" className="text-[13px] font-semibold text-text">Is this investment worth it?</h3>
      <span className="text-[11px] text-muted">
        A decision study answers one question with a verdict, a size and a report. It builds its own project, so
        the one open now is not changed.
      </span>
      {reason && <span data-testid="hub-start-invest-reason" className="text-[11px] text-warn">{reason}</span>}
      <div className="grid gap-2 sm:grid-cols-3">
        {GUIDED_QUESTIONS.map(q => (
          <button key={q.id} type="button" data-testid={`hub-start-question-${q.id}`}
            onClick={() => start(q)} disabled={!ready}
            title={reason ?? `Start the study: ${q.title}`}
            className="flex flex-col gap-1 rounded-lg border border-border p-3 text-left hover:border-accent/60 hover:bg-accent/5 disabled:opacity-50">
            <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-text">
              <Scale size={12} className="text-accent" /> {q.title}
            </span>
            <span className="text-[11px] leading-relaxed text-muted">{q.blurb}</span>
          </button>
        ))}
      </div>
    </section>
  )
}
