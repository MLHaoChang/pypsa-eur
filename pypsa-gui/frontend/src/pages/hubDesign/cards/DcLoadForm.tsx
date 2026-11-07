// The data-centre create form (DC 22): under the eh_datacenter and
// eh_datacenter_staged tiles, in the Guided Start card and the Expert wizard
// alike, so a data-centre project is created with a `dc_load` spec and the
// Backup and Power architecture sections show on it. Every field starts at the
// example's default, so Create works unedited. A refusal (422 dc_load_invalid)
// sits beside its field and opens with the field label.
import { useState } from 'react'
import { dcLoadRefusal, type TemplateCreate } from '../../../hooks/useCreateFromTemplate'
import {
  ARCH_HINT, BACKUP_HINT, DC_ARCH_OPTIONS, DC_BACKUP_OPTIONS, DC_DEFAULTS, DC_FIELD_LABEL,
  dcFieldOf, dcLoadBody, dcRefusalText, initialDcDraft, validateDc,
  type DcDraft, type DcField,
} from '../dcLoadForm'

interface CreateMutation {
  mutate: (arg: TemplateCreate, opts?: { onError?: (e: unknown) => void }) => void
  isPending: boolean
}

const INPUT = 'rounded border border-border bg-bg px-1.5 py-0.5 text-[12px] tabular-nums'

export function DcLoadForm({ templateId, templateName, create, disabledReason, onCancel }: {
  templateId: string
  templateName: string
  create: CreateMutation
  disabledReason: string | null
  onCancel: () => void
}) {
  const [draft, setDraft] = useState<DcDraft>(initialDcDraft)
  const [server, setServer] = useState<{ field: DcField | null; text: string } | null>(null)
  const staged = templateId === 'eh_datacenter_staged'
  const local = validateDc(draft, templateId)
  const errors: Partial<Record<DcField, string>> = { ...local }
  if (server?.field && !errors[server.field]) errors[server.field] = server.text
  const nErrors = Object.keys(errors).length
  const set = <K extends keyof DcDraft>(k: K, v: DcDraft[K]) => { setDraft({ ...draft, [k]: v }); setServer(null) }

  const submit = () => {
    if (nErrors || disabledReason) return
    create.mutate({ templateId, params: dcLoadBody(draft, templateId) }, {
      onError: (e: unknown) => {
        const r = dcLoadRefusal(e)
        if (!r) return
        const field = dcFieldOf(r.field)
        setServer({ field, text: dcRefusalText(field, r.message) })
      },
    })
  }
  const err = (f: DcField) => errors[f]
    ? <p role="alert" data-testid={`dc-form-error-${f}`} className="text-[11px] text-danger">{errors[f]}</p> : null

  return (
    <form data-testid="dc-load-form" aria-label={`${templateName} setup`}
      onSubmit={e => { e.preventDefault(); submit() }}
      className="flex flex-col gap-3 rounded-lg border border-accent/40 p-3 text-[12px]">
      <p className="text-muted">
        Set up the {templateName}. Every value starts at the example’s own default, so you can create it as it is.
      </p>

      <div className="flex flex-col gap-1">
        {staged
          ? <span id="dc-form-it-label" className="font-semibold text-text">{DC_FIELD_LABEL.it_load}</span>
          : <label htmlFor="dc-form-it" className="font-semibold text-text">{DC_FIELD_LABEL.it_load}</label>}
        {staged ? (
          <p aria-labelledby="dc-form-it-label" data-testid="dc-form-phases" className="text-muted">
            {DC_DEFAULTS.phases.map(p => `${p.mw} MW from ${p.year}`).join(', ')}. Phases come from the build plan: change them there.
          </p>
        ) : (
          <span className="inline-flex items-center gap-1.5">
            <input id="dc-form-it" data-testid="dc-form-it" type="number" min={0} step="any" className={`${INPUT} w-24`}
              value={draft.it_load} onChange={e => set('it_load', e.target.value)}
              aria-invalid={!!errors.it_load} />
            <span className="text-muted">MW of IT equipment, at the rack input</span>
          </span>
        )}
        {err('it_load')}
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="dc-form-pue" className="font-semibold text-text">{DC_FIELD_LABEL.pue}</label>
        <span className="inline-flex items-center gap-1.5">
          <input id="dc-form-pue" data-testid="dc-form-pue" type="number" min={1} step="any" className={`${INPUT} w-24`}
            value={draft.pue} onChange={e => set('pue', e.target.value)} aria-invalid={!!errors.pue} />
          <span className="text-muted">facility energy per unit of IT energy, at or below 20 °C</span>
        </span>
        {err('pue')}
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="dc-form-backup" className="font-semibold text-text">{DC_FIELD_LABEL.backup}</label>
        <select id="dc-form-backup" data-testid="dc-form-backup" className={`${INPUT} w-40`}
          value={draft.backup} onChange={e => set('backup', e.target.value as DcDraft['backup'])}
          aria-describedby="dc-form-backup-hint">
          {DC_BACKUP_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
        <p id="dc-form-backup-hint" data-testid="dc-form-backup-hint" className="text-muted">
          {BACKUP_HINT[draft.backup]}
        </p>
        {err('backup')}
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="dc-form-arch" className="font-semibold text-text">{DC_FIELD_LABEL.architecture}</label>
        <select id="dc-form-arch" data-testid="dc-form-arch" className={`${INPUT} w-56`}
          value={draft.architecture} onChange={e => set('architecture', e.target.value as DcDraft['architecture'])}
          aria-describedby="dc-form-arch-hints">
          {DC_ARCH_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
        <ul id="dc-form-arch-hints" data-testid="dc-form-arch-hints" className="flex flex-col gap-0.5">
          {DC_ARCH_OPTIONS.map(([v, label]) => (
            <li key={v} data-testid={`dc-form-arch-hint-${v}`} data-selected={draft.architecture === v}
              className={draft.architecture === v ? 'text-text' : 'text-muted'}>
              <span className="font-semibold">{label}:</span> {ARCH_HINT[v]}
              {draft.architecture === v && (
                <span data-testid="dc-form-arch-selected" className="ml-1.5 rounded border border-accent/50 px-1 text-[10px] text-accent">selected</span>
              )}
            </li>
          ))}
        </ul>
        {err('architecture')}
      </div>

      {server && !server.field && (
        <p role="alert" data-testid="dc-form-error-other" className="text-[11px] text-danger">{server.text}</p>
      )}
      {disabledReason && <p data-testid="dc-form-disabled-reason" className="text-[11px] text-warn">{disabledReason}</p>}
      <div className="flex items-center gap-2">
        <button type="submit" data-testid="dc-form-create" disabled={create.isPending || !!disabledReason || nErrors > 0}
          className="rounded bg-accent px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-50">
          {create.isPending ? 'Creating…' : 'Create project'}
        </button>
        <button type="button" onClick={onCancel}
          className="rounded border border-border px-3 py-1.5 text-[12px] text-text hover:border-accent">Cancel</button>
        {nErrors > 0 && (
          <span className="text-[11px] text-muted">{nErrors} {nErrors === 1 ? 'field needs' : 'fields need'} attention</span>
        )}
      </div>
    </form>
  )
}
