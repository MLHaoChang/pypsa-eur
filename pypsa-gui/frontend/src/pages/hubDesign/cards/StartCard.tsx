// Step 1 — Start (guided-mode spec §5.3): an energy-hub example site or the
// network already open. Templates come from the New-project wizard's own
// table and are created by the wizard's own (lifted) mutation, so the G4
// mode rule and the workbench navigation are the wizard's.
import { FolderOpen, Hexagon } from 'lucide-react'
import { TEMPLATES } from '../../../layout/NewProjectWizard'
import { useCreateFromTemplate } from '../../../hooks/useCreateFromTemplate'
import { useUIStore } from '../../../store/uiStore'
import { BLOCKED_NO_PROJECT } from '../flow'
import { useHubDesignStore } from '../hubDesignStore'
import { CardShell } from '../shared/CardShell'
import { Term } from '../shared/Term'
import { useHubStudy, useHubTemplate } from '../useHubData'
import { STUDY_RUNNING_SWITCH } from '../../../hooks/useCreateFromTemplate'

const HUB_TEMPLATES = TEMPLATES.filter(t => t.id.startsWith('eh_'))

/** One line of purpose: the description's first sentence (the rest names
 *  the engine's archetype pack). */
export function purpose(description: string): string {
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
            <span className="text-muted">{template.study_notes[0]}</span>
          )}
        </div>
      )}

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
                <span className="text-[11px] leading-relaxed text-muted">{purpose(t.description)}</span>
              </button>
            )
          })}
        </div>
      </div>

      <button type="button" data-testid="hub-start-own-network"
        onClick={() => setStep('site', { user: true })}
        disabled={!project}
        title={project ? 'Design a hub on the network that is open now' : BLOCKED_NO_PROJECT}
        className="self-start inline-flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-[12px] text-text hover:border-accent hover:text-accent disabled:opacity-50">
        <FolderOpen size={13} /> Use my network
      </button>
    </CardShell>
  )
}
