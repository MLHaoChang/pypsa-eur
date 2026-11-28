/** The data-centre set-up values that stay editable after creation (te 04,
 *  TEMPLATE-GAPS-1 Q3 (a)): PUE at design and critical share on the IT load
 *  (`DcLoadFields`), backup mode and power architecture in the backup section
 *  (`DcBackupArchFields`). Each saves through `PUT .../dc-load`, which checks
 *  the merged spec as the create form does; a refusal names the field and
 *  nothing changes. Shown only on a project created with a `dc_load`. */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { campusApi, errorText, type DcArchitecture, type DcBackupMode, type DcLoadPatch } from '../../../api/campusElectrical'
import { resultsApi } from '../../../api/simulation'
import { Btn, Seg } from '../../../components/PageKit'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { invalidateNetworkQueries } from '../../../utils/projectActions'
import { ARCH_HINT, BACKUP_HINT, DC_ARCH_OPTIONS, DC_BACKUP_OPTIONS, DC_FIELD_LABEL } from '../dcLoadForm'

interface DcSpec {
  pue_at_design?: number
  critical_share?: number
  architecture?: DcArchitecture
  backup?: { mode?: DcBackupMode }
}

const READ_ONLY_LINE = 'Read-only: you cannot change these values on this project.'
const PUE_ERROR = 'PUE at design: enter 1 or more.'
const SHARE_SUM_ERROR = 'Critical share (%): with the other shares, it adds up to more than 100 %. Lower it.'
const SHARE_ERROR = 'Critical share (%): enter a value above 0 and up to 100.'

const INPUT = 'h-6 rounded border border-border bg-bg px-1.5 text-xs text-text'

/** The stored spec of a data-centre project, or null when it has no `dc_load`. */
export function useDcSpec(project: string | null | undefined): DcSpec | null {
  const { data } = useQuery({
    queryKey: nk(project ?? '', 'adequacy', 'eh_template'),
    queryFn: () => resultsApi.getEhTemplate(project as string, { quiet: true }),
    enabled: !!project, staleTime: Infinity,
  })
  return (data as { dc_load?: { spec?: DcSpec } } | null)?.dc_load?.spec ?? null
}

const REFUSAL_LABEL: Record<string, string> = {
  pue_at_design: DC_FIELD_LABEL.pue, critical_share: 'Critical share (%)',
  architecture: DC_FIELD_LABEL.architecture, backup: DC_FIELD_LABEL.backup,
}

/** A refusal opens with the label of the field it names, as the form errors do. */
function refusalText(e: unknown): string {
  const text = errorText(e)
  const field = (e as { response?: { data?: { detail?: { field?: string } } } })?.response?.data?.detail?.field
  if (field?.endsWith('critical_share') && /exceed/i.test(text)) return SHARE_SUM_ERROR
  const label = field ? REFUSAL_LABEL[field.replace(/^dc_load\./, '').split('.')[0]] : undefined
  return label && !text.startsWith(label) ? `${label}: ${text}` : text
}

function useSaveDcLoad(project: string) {
  const qc = useQueryClient()
  const [refusal, setRefusal] = useState<string | null>(null)
  const [edited, setEdited] = useState<DcLoadPatch | null>(null)
  const save = useMutation({
    mutationFn: (patch: DcLoadPatch) => campusApi.setDcLoad(project, patch),
    onMutate: () => { setRefusal(null); setEdited(null) },
    onSuccess: () => {
      for (const queryKey of [nk(project, 'adequacy', 'eh_template'), ['eh-template', project],
        nk(project, 'loads'), ['series-current', project]]) void qc.invalidateQueries({ queryKey })
      // Results re-read their state, so a run that predates the edit reads "Out of date".
      invalidateNetworkQueries(qc, project)
    },
    onError: (e, patch) => {
      const kind = (e as { response?: { data?: { detail?: { error_kind?: string } } } })?.response?.data?.detail?.error_kind
      if (kind === 'dc_series_edited') setEdited(patch)
      else setRefusal(refusalText(e))
    },
  })
  return { save, refusal, edited, clearEdited: () => setEdited(null) }
}

/** PUE at design and critical share, on the IT load. */
export function DcLoadFields() {
  const project = useUIStore(s => s.currentProject)
  const spec = useDcSpec(project)
  const { save, refusal, edited, clearEdited } = useSaveDcLoad(project ?? '')
  const [pue, setPue] = useState<string | null>(null)
  const [share, setShare] = useState<string | null>(null)
  const readOnly = useUIStore(s => s.readOnly)
  if (!project || !spec) return null
  const pueText = pue ?? String(spec.pue_at_design ?? '')
  const shareText = share ?? String(Math.round((spec.critical_share ?? 0) * 1000) / 10)
  const patch = (): DcLoadPatch => {
    const out: DcLoadPatch = {}
    if (pue !== null && Number(pue) !== spec.pue_at_design) out.pue_at_design = Number(pue)
    if (share !== null && Number(share) / 100 !== spec.critical_share) out.critical_share = Number(share) / 100
    return out
  }
  const next = patch()
  const pueBad = pue !== null && !(Number(pue) >= 1 && Number.isFinite(Number(pue)))
  const shareBad = share !== null && !(Number(share) > 0 && Number(share) <= 100)
  const bad = pueBad || shareBad
  return (
    <div data-testid="dc-load-fields" className="mt-2 flex flex-col gap-1 text-xs">
      {readOnly && <p role="status" data-testid="dc-read-only" className="text-muted">{READ_ONLY_LINE}</p>}
      <label className="flex items-center gap-2">
        <span className="w-28 text-muted">{DC_FIELD_LABEL.pue}</span>
        <input aria-label={DC_FIELD_LABEL.pue} className={`${INPUT} w-20${pueBad ? ' border-red-600' : ''}`} inputMode="decimal"
          value={pueText} onChange={e => setPue(e.target.value)} aria-invalid={pueBad} disabled={readOnly} />
      </label>
      <label className="flex items-center gap-2">
        <span className="w-28 text-muted">Critical share (%)</span>
        <input aria-label="Critical share (%)" className={`${INPUT} w-20${shareBad ? ' border-red-600' : ''}`} inputMode="decimal"
          value={shareText} onChange={e => setShare(e.target.value)} aria-invalid={shareBad} disabled={readOnly} />
      </label>
      {bad && (
        <p role="alert" data-testid="dc-load-invalid" className="text-red-600">
          {pueBad ? PUE_ERROR : ''}{pueBad && shareBad ? ' ' : ''}{shareBad ? SHARE_ERROR : ''}
        </p>
      )}
      <div className="flex items-center gap-2">
        <Btn variant="primary" disabled={readOnly || save.isPending || bad || Object.keys(next).length === 0}
          onClick={() => save.mutate(next, { onSuccess: () => { setPue(null); setShare(null) } })}>
          Save
        </Btn>
        <span className="text-muted" data-testid="dc-load-save-note">
          {!readOnly && !bad && Object.keys(next).length === 0
            ? 'Nothing changed yet. '
            : ''}Rewrites the IT load and cooling profile. Undo restores it.
        </span>
      </div>
      {refusal && <p role="alert" data-testid="dc-load-refusal" className="text-red-600">{refusal}</p>}
      {edited && (
        <p role="alert" data-testid="dc-series-edited" className="text-amber-700">
          The IT load or cooling profile was edited after creation. Saving replaces it.{' '}
          <Btn onClick={() => save.mutate({ ...edited, overwrite_series: true }, { onSuccess: () => { setPue(null); setShare(null) } })}>
            Replace the edited profile
          </Btn>{' '}
          <Btn onClick={clearEdited}>Keep it</Btn>
        </p>
      )}
    </div>
  )
}

/** Backup mode and power architecture, in the backup section. */
export function DcBackupArchFields({ only }: { only?: 'architecture' } = {}) {
  const project = useUIStore(s => s.currentProject)
  const spec = useDcSpec(project)
  const { save, refusal } = useSaveDcLoad(project ?? '')
  const readOnly = useUIStore(s => s.readOnly)
  if (!project || !spec) return null
  const mode = spec.backup?.mode ?? 'genset'
  const arch = spec.architecture ?? 'ac'
  return (
    <div data-testid="dc-backup-arch-fields" className="flex flex-col gap-1 rounded border border-border bg-panel p-4 text-xs">
      {readOnly && <p role="status" data-testid="dc-read-only" className="text-muted">{READ_ONLY_LINE}</p>}
      {only !== 'architecture' && (
        <>
          <div className="flex items-center gap-2">
            <span className="w-28 text-muted">{DC_FIELD_LABEL.backup}</span>
            <Seg value={mode} onChange={m => { if (!readOnly) save.mutate({ backup: { mode: m } }) }} options={DC_BACKUP_OPTIONS.map(([value, label]) => ({ value, label }))} />
          </div>
          <p className="text-muted" data-testid="dc-backup-hint">{BACKUP_HINT[mode]}</p>
        </>
      )}
      <label className="flex items-center gap-2">
        <span className="w-28 text-muted">{DC_FIELD_LABEL.architecture}</span>
        <select aria-label={DC_FIELD_LABEL.architecture} className={INPUT} value={arch} disabled={readOnly || save.isPending}
          onChange={e => save.mutate({ architecture: e.target.value as DcArchitecture })}>
          {DC_ARCH_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      </label>
      <p className="text-muted" data-testid="dc-arch-hint">{ARCH_HINT[arch]}</p>
      {refusal && <p role="alert" data-testid="dc-backup-arch-refusal" className="text-red-600">{refusal}</p>}
    </div>
  )
}
