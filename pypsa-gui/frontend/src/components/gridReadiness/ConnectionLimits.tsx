// The connection's energization, ramp limit, oscillation band and fault-level
// source, in Connection & grid (design §5: they belong to section 3). This is
// the one editor for the three limits you can set (DC 03 slice 6): a value with
// its unit, where it comes from (only "assumed" leaves it to check), a note,
// Save per line, and a reset that names what it resets to. The rows say where
// each value is set. Energization stays read-only here; its schedule is edited
// on the connection link. The fault level's value lives here too (one editor,
// written to the PoC bus's `eh_sk_mva`); Properties and the site form show it
// read-only and route here. Guided and Expert get the same control: here in
// Expert, and in Guided the field drawer a to-check item opens (App.tsx).
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PageSection } from '../PageKit'
import type { FlowTarget } from '../../api/flow'
import {
  campusApi, type GridLimitItem, type GridLimitSetting, type GridLimitSource, type GridReadinessRow,
} from '../../api/campusElectrical'
import { networkApi } from '../../api/network'
import { updateAsset } from '../../utils/assetWrite'
import { useUIStore } from '../../store/uiStore'
import { openFlowTarget, openFlowTargetInPlace } from '../../layout/flowNavigation'
import { blockerMessage } from '../../utils/blockerMessage'
import { nk } from '../../utils/queryKeys'
import { fmt, useGridReadiness } from './GridReadinessScorecard'

const SHOWN: Record<string, string> = {
  energization_peak: 'Energization',
  ramp_rate: 'Connection ramp limit',
  oscillation: 'Oscillation band',
  scr: 'Fault level',
}

/** The three limits the user can set: row kind → unit the value is entered in and its valid range. */
const EDITABLE: Record<string, { unit: string; max: number | null; hint: string }> = {
  ramp_rate: { unit: 'MW/min', max: null, hint: 'above 0' },
  oscillation: { unit: '% of connection, peak-to-peak', max: 100, hint: 'above 0, at most 100' },
  scr: { unit: '', max: null, hint: '' },
}

const SOURCES: { id: GridLimitSource; label: string }[] = [
  { id: 'code', label: 'Grid code' },
  { id: 'datasheet', label: 'Datasheet' },
  { id: 'measured', label: 'Measured' },
  { id: 'assumed', label: 'Assumed (to check)' },
]
const SOURCE_WORDS: Record<string, string> = { code: 'grid code', datasheet: 'datasheet', measured: 'measured', assumed: 'assumed' }

/** The row's kind: staged projects have one energization row per period, `energization_peak:2035`. */
const kindOf = (r: GridReadinessRow) => r.id.split(':')[0]

const valueOf = (s: GridLimitSetting) => (s.user ?? s.default).value
const sourceOf = (s: GridLimitSetting): GridLimitSource => {
  const src = (s.user ?? s.default).source
  return (SOURCES.some(o => o.id === src) ? src : 'assumed') as GridLimitSource
}

/** What a reset returns to, in words: the template's value and source, or the generic placeholder. */
export function resetTarget(s: GridLimitSetting, unit: string): string {
  const d = s.default
  const v = d.value != null ? `${Number(d.value.toPrecision(4))}${unit ? ` ${unit}` : ''}` : null
  const src = SOURCE_WORDS[d.source] ?? d.source
  if (d.origin === 'template') return `the template's ${v ? `${v}, ` : 'source '}${src}`
  return `the generic placeholder${v ? ` ${v}` : ''}, assumed`
}

/** The PoC bus whose fault level this line edits, and its value. */
interface FaultBus { name: string; value: number | null }

function LimitEditor({ row, setting, label, faultBus }: {
  row: GridReadinessRow; setting: GridLimitSetting; label: string; faultBus?: FaultBus | null
}) {
  const project = useUIStore(s => s.currentProject) ?? ''
  const qc = useQueryClient()
  const kind = kindOf(row)
  const { unit, max, hint } = EDITABLE[kind]
  const item: GridLimitItem = setting.item
  const withValue = item !== 'fault_level'
  const [value, setValue] = useState(valueOf(setting) != null ? String(valueOf(setting)) : '')
  const [source, setSource] = useState<GridLimitSource>(sourceOf(setting))
  const [note, setNote] = useState(setting.user?.note ?? '')
  const [error, setError] = useState<string | null>(null)
  const [fault, setFault] = useState(faultBus?.value != null ? String(faultBus.value) : '')

  const faultNum = Number(fault)
  const faultOk = faultBus == null || (fault.trim() === '' ? faultBus.value == null : Number.isFinite(faultNum) && faultNum > 0)
  const faultChanged = faultBus != null && fault.trim() !== '' && faultNum !== faultBus.value
  const num = Number(value)
  const valueOk = faultOk && (!withValue || (value.trim() !== '' && Number.isFinite(num) && num > 0 && (max == null || num <= max)))
  const limitDirty = setting.user == null
    || source !== setting.user.source || note !== setting.user.note
    || (withValue && num !== setting.user.value)
  const dirty = limitDirty || faultChanged
  // Both reads move with a limit: the scorecard row and the project flow's to-check item.
  const refresh = () => Promise.all([
    qc.invalidateQueries({ queryKey: ['campusElectrical', 'grid_readiness', project] }),
    qc.invalidateQueries({ queryKey: nk(project, 'flow') }),
  ])
  const save = useMutation({
    mutationFn: async () => {
      // The value goes through the ordinary bus update (the Properties path); the limit holds only its source and note.
      if (faultChanged && faultBus) await updateAsset(qc, project, 'buses', faultBus.name, { eh_sk_mva: faultNum })
      if (limitDirty) await campusApi.setGridLimit(project, item, { ...(withValue ? { value: num } : {}), source, note })
    },
    onMutate: () => setError(null),
    onSuccess: refresh,
    onError: (e: unknown) => setError(blockerMessage(e)),
  })
  const reset = useMutation({
    mutationFn: () => campusApi.resetGridLimit(project, item),
    onMutate: () => setError(null),
    onSuccess: refresh,
    onError: (e: unknown) => setError(blockerMessage(e)),
  })
  const busy = save.isPending || reset.isPending
  const id = `limit-${item}`

  return (
    <div className="mt-1 flex flex-col gap-1" data-testid={`limit-editor-${item}`}>
      {withValue ? (
        <div className="flex items-center gap-1 text-[11px]">
          <input id={`${id}-value`} aria-label={`${label} value`} type="number" inputMode="decimal" min={0} step="any" value={value}
            aria-invalid={!valueOk} title={hint ? `${hint} ${unit}` : undefined}
            onChange={e => setValue(e.target.value)}
            className="w-24 rounded border border-border bg-bg px-1 py-0.5 tabular-nums" />
          <span className="text-muted">{unit}</span>
        </div>
      ) : (
        faultBus ? (
          <div className="flex items-center gap-1 text-[11px]">
            <input id={`${id}-value`} aria-label={`${label} value`} type="number" inputMode="decimal" min={0} step="any" value={fault}
              aria-invalid={!faultOk} onChange={e => setFault(e.target.value)}
              className="w-24 rounded border border-border bg-bg px-1 py-0.5 tabular-nums" />
            <span className="text-muted">MVA, on the point-of-connection bus {faultBus.name}</span>
          </div>
        ) : (
          <span className="text-[11px] text-muted">Tag a bus as the point of connection to set its fault level.</span>
        )
      )}
      <div className="flex flex-wrap items-center gap-1 text-[11px]">
        <span className="text-muted">Source:</span>
        <select aria-label={`${label} source`} value={source}
          onChange={e => setSource(e.target.value as GridLimitSource)}
          className="rounded border border-border bg-bg px-1 py-0.5">
          {SOURCES.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>
        <input type="text" aria-label={`${label} note`} placeholder="Note (where it is from)" maxLength={500}
          value={note} onChange={e => setNote(e.target.value)}
          className="min-w-[10rem] flex-1 rounded border border-border bg-bg px-1 py-0.5" />
      </div>
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <button type="button" disabled={!valueOk || !dirty || busy} onClick={() => save.mutate()}
          className="rounded border border-border px-2 py-0.5 disabled:opacity-50">
          Save {label.toLowerCase()}
        </button>
        {setting.user != null && (
          <button type="button" disabled={busy} onClick={() => reset.mutate()} className="underline">
            Reset to {resetTarget(setting, unit)}
          </button>
        )}
      </div>
      {!valueOk && <p className="text-[11px] text-warn">
        {withValue ? `The value must be ${hint} ${unit}.` : 'The fault level must be above 0 MVA.'}</p>}
      {error && <p role="alert" className="text-[11px] text-warn" data-testid={`limit-error-${item}`}>{error}</p>}
    </div>
  )
}

/** The fault level on the point-of-connection bus(es): the lowest, as the readiness check takes it, and the bus holding it. */
function usePocFaultBus(): FaultBus | null {
  const project = useUIStore(s => s.currentProject)
  const q = useQuery({ queryKey: nk(project, 'buses'), queryFn: networkApi.getBuses, enabled: !!project, staleTime: 30_000 })
  const pocs = (q.data ?? []).filter(b => b.eh_poc)
  const valued = pocs.map(b => ({ name: b.name, value: Number(b.eh_sk_mva) })).filter(v => Number.isFinite(v.value) && v.value > 0)
  if (valued.length) return valued.reduce((m, v) => (v.value < m.value ? v : m))
  return pocs.length ? { name: pocs[0].name, value: null } : null
}

/** `only` narrows the list to some row kinds: the Guided field drawer opens the one line a to-check item points at. */
export function ConnectionLimits({ only }: { only?: string[] } = {}) {
  const q = useGridReadiness()
  const faultBus = usePocFaultBus()
  const faultMva = faultBus?.value ?? null
  const expert = useUIStore(s => s.uiMode === 'expert')
  // Same button in both modes: Expert opens the Link's properties, Guided the field drawer over the card.
  const open = (t: FlowTarget) => (expert ? openFlowTarget(t) : openFlowTargetInPlace(t))
  const rows = (q.data?.rows ?? []).filter(r => kindOf(r) in SHOWN && (kindOf(r) !== 'scr' || r.setting) && (!only || only.includes(kindOf(r))))
  // One line per kind. Energization has one row per period: every period is listed,
  // so a connection that grows from 20 to 60 MW is never shown as 20 MW.
  const link = rows.find(r => kindOf(r) === 'ramp_rate')?.target as FlowTarget | undefined
  const kinds = Object.keys(SHOWN).filter(k => rows.some(r => kindOf(r) === k))
  return (
    <PageSection title="Connection limits" hint="Set the ones you know">
      {q.isPending ? <p className="text-[12px] text-muted">Loading the connection limits…</p>
        : q.isError ? <p className="text-[12px] text-warn" data-testid="connection-limits-error">
            The connection limits could not be read.</p>
        : kinds.length === 0 ? <p className="text-[12px] text-muted">No grid connection is tagged on this project.</p>
          : (
            <ul className="flex flex-col gap-3" data-testid="connection-limits">
              {kinds.map(k => {
                const group = rows.filter(r => kindOf(r) === k)
                const lead = group[0]
                const staged = group.length > 1 || lead.id.includes(':')
                const editable = k in EDITABLE && lead.setting
                const figures = k === 'scr' ? (faultMva != null ? `${Number(faultMva.toPrecision(4))} MVA` : 'not set') : staged
                  ? group.map(r => `${r.id.split(':')[1] ?? ''} ${fmt(r.limit, r.unit)}`.trim()).join(' · ')
                  : fmt(lead.limit, lead.unit)
                return (
                  <li key={k} data-testid={`connection-limit-${k}`} className="text-[12px]">
                    <span className="font-semibold">{SHOWN[k]}</span>
                    {figures != null && <>{': '}<span className="tabular-nums">{figures}</span></>}
                    {group.some(r => r.assumed) && <span className="ml-1 text-muted">(assumed)</span>}
                    <span className="block text-muted">{lead.source}{lead.note && !staged ? `. ${lead.note}` : ''}</span>
                    {editable
                      // Keyed on what is stored, so a saved or reset limit reloads the fields.
                      ? (<>
                          <LimitEditor key={`${JSON.stringify(lead.setting)}:${faultBus?.name}:${faultBus?.value}`} row={lead}
                            setting={lead.setting!} label={SHOWN[k]} faultBus={k === 'scr' ? faultBus : undefined} />
                          {!only && k === 'ramp_rate' && link && (
                            <span className="block text-[11px] text-muted">
                              Checked by Grid readiness; the optimisation's ramp is set on the connection link{' '}
                              <button type="button" className="underline" onClick={() => open(link)}>Edit the link ›</button>
                            </span>
                          )}
                        </>)
                      : k === 'energization_peak' && lead.target.field && (
                        <button type="button" className="underline text-[11px]" onClick={() => open(lead.target as FlowTarget)}>
                          Edit the energization schedule
                        </button>
                      )}
                  </li>
                )
              })}
            </ul>
          )}
    </PageSection>
  )
}
