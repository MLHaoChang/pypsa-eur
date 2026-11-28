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
import { ARCH_HINT, BACKUP_HINT, DC_ARCH_OPTIONS, DC_BACKUP_OPTIONS, DC_FIELD_LABEL } from '../dcLoadForm'

interface DcSpec {
  pue_at_design?: number
  critical_share?: number
  architecture?: DcArchitecture
  backup?: { mode?: DcBackupMode }
}

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
    },
    onError: (e, patch) => {
      const kind = (e as { response?: { data?: { detail?: { error_kind?: string } } } })?.response?.data?.detail?.error_kind
      if (kind === 'dc_series_edited') setEdited(patch)
      else setRefusal(errorText(e))
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
  const bad = (pue !== null && !(Number(pue) >= 1)) || (share !== null && !(Number(share) > 0 && Number(share) <= 100))
  return (
    <div data-testid="dc-load-fields" className="mt-2 flex flex-col gap-1 text-xs">
      <label className="flex items-center gap-2">
        <span className="w-28 text-muted">{DC_FIELD_LABEL.pue}</span>
        <input aria-label={DC_FIELD_LABEL.pue} className={`${INPUT} w-20`} inputMode="decimal"
          value={pueText} onChange={e => setPue(e.target.value)} aria-invalid={pue !== null && !(Number(pue) >= 1)} />
      </label>
      <label className="flex items-center gap-2">
        <span className="w-28 text-muted">Critical share (%)</span>
        <input aria-label="Critical share (%)" className={`${INPUT} w-20`} inputMode="decimal"
          value={shareText} onChange={e => setShare(e.target.value)} />
      </label>
      {bad && (
        <p role="alert" data-testid="dc-load-invalid" className="text-red-600">
          {pue !== null && !(Number(pue) >= 1) ? 'PUE is 1 or more. ' : ''}
          {share !== null && !(Number(share) > 0 && Number(share) <= 100) ? 'Critical share is above 0 and up to 100 %.' : ''}
        </p>
      )}
      <div className="flex items-center gap-2">
        <Btn variant="primary" disabled={save.isPending || bad || Object.keys(next).length === 0}
          onClick={() => save.mutate(next, { onSuccess: () => { setPue(null); setShare(null) } })}>
          Save
        </Btn>
        <span className="text-muted">Rewrites the IT load and cooling profile. Undo restores it.</span>
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
  if (!project || !spec) return null
  const mode = spec.backup?.mode ?? 'genset'
  const arch = spec.architecture ?? 'ac'
  return (
    <div data-testid="dc-backup-arch-fields" className="flex flex-col gap-1 rounded border border-border bg-panel p-4 text-xs">
      {only !== 'architecture' && (
        <>
          <div className="flex items-center gap-2">
            <span className="w-28 text-muted">{DC_FIELD_LABEL.backup}</span>
            <Seg value={mode} onChange={m => save.mutate({ backup: { mode: m } })} options={DC_BACKUP_OPTIONS.map(([value, label]) => ({ value, label }))} />
          </div>
          <p className="text-muted" data-testid="dc-backup-hint">{BACKUP_HINT[mode]}</p>
        </>
      )}
      <label className="flex items-center gap-2">
        <span className="w-28 text-muted">{DC_FIELD_LABEL.architecture}</span>
        <select aria-label={DC_FIELD_LABEL.architecture} className={INPUT} value={arch} disabled={save.isPending}
          onChange={e => save.mutate({ architecture: e.target.value as DcArchitecture })}>
          {DC_ARCH_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      </label>
      <p className="text-muted" data-testid="dc-arch-hint">{ARCH_HINT[arch]}</p>
      {refusal && <p role="alert" data-testid="dc-backup-arch-refusal" className="text-red-600">{refusal}</p>}
    </div>
  )
}
