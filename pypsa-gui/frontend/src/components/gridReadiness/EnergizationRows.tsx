// A staged site's connection limit, per period (template-e2e 02b): one row per period of the link's
// energization schedule, in period order, each with its energization check beside it and its own
// refusal under it. A flat period is edited here (`PUT /flow/energization`); one that varies inside
// the period is read-only and opens the link. The per-period value is the energized (study) limit;
// the link's capacity (p_nom) is its rating, which this never changes.
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { putFlowEnergization, type FlowEnergization, type FlowTarget } from '../../api/flow'
import type { GridReadinessRow } from '../../api/campusElectrical'
import { useUIStore } from '../../store/uiStore'
import { openFlowTarget, openFlowTargetInPlace } from '../../layout/flowNavigation'
import { blockerMessage } from '../../utils/blockerMessage'
import { displayName } from '../../utils/displayName'
import { nk } from '../../utils/queryKeys'
import { fmt, VerdictChip } from './GridReadinessScorecard'

/** The route's typed refusals, in words under the row (the server's kinds, `energization.py`). */
export const ENERGIZATION_REFUSALS: Record<string, string> = {
  energization_invalid_request: 'Connection limit: the change could not be read. Try again; if it repeats, set it on the link.',
  energization_invalid_mw: 'Connection limit: enter a number above 0 MW.',
  energization_no_schedule: 'Connection limit: this link has no energization schedule any more. Set its capacity on the link.',
  energization_unknown_period: 'Connection limit: this period is no longer in the schedule. The rows now show its periods.',
  energization_period_not_flat: 'Connection limit: this period now varies within it, so it is set on the link, not here.',
}

/** A refusal of `PUT /flow/energization` in words: its kind's sentence, else the server's own. */
export function energizationRefusal(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: { error_kind?: unknown } } } })?.response?.data?.detail
  const kind = typeof detail?.error_kind === 'string' ? detail.error_kind : ''
  return ENERGIZATION_REFUSALS[kind] ?? `Connection limit: ${blockerMessage(err)}`
}

/** The study limit vs the rating, said once on a surface that shows the rows. */
export const ENERGIZED_SENTENCE = 'Each period\'s value is the energized limit the study holds the site to in that period. '
  + 'The link\'s rating (its capacity) is the most it can ever carry; a value above the rating does not raise it.'

const periodOrder = (a: string, b: string) => Number(a) - Number(b) || a.localeCompare(b)
const periodWords = (p: string) => (p === 'all' ? 'All periods' : p)

/** A period the route cannot set: "varies 30–40", or, where every hour with a value has the
 *  same one (min = max, the rest unset), "15, some hours without a value" (GK B 68). */
export function rangeWords(v: { min: number; max: number }, unit = ''): string {
  const n = (x: number) => Number(x.toPrecision(6))
  const u = unit ? ` ${unit}` : ''
  return v.min === v.max ? `${n(v.min)}${u}, some hours without a value` : `varies ${n(v.min)}–${n(v.max)}${u}`
}

/** The schedule in words, for a one-line summary under the "Connection limit (MW)" label:
 *  "2027 15 · 2035 varies 30–40". */
export function energizationSummary(entry: FlowEnergization): string {
  const n = (v: number) => Number(v.toPrecision(6))
  return Object.keys(entry.by_period).sort(periodOrder).map(p => {
    const v = entry.by_period[p]
    const what = v == null ? 'not set' : typeof v === 'number' ? `${n(v)}` : rangeWords(v)
    return `${periodWords(p)} ${what}`
  }).join(' · ')
}

/** The energization check of one period: the readiness row `energization_peak:<period>`
 *  (the only row on a single-period network). */
function checkFor(readiness: GridReadinessRow[], period: string): GridReadinessRow | undefined {
  const rows = readiness.filter(r => r.id.split(':')[0] === 'energization_peak')
  return rows.find(r => r.id === `energization_peak:${period}`) ?? (period === 'all' && rows.length === 1 ? rows[0] : undefined)
}

/** The check beside a row: its verdict and the period's peak. "against X MW" only when X is the row's
 *  own value; the check's cap can differ (efficiency, firm gensets), and a different figure beside the
 *  row would read as a contradiction (UX 26). The readiness note stays as the tooltip. */
function Check({ row, period, value }: { row?: GridReadinessRow; period: string; value: number | null }) {
  if (!row) return null
  const same = typeof row.limit === 'number' && value != null && Math.abs(row.limit - value) < 1e-6
  return (
    <span data-testid={`energization-check-${period}`} title={row.note ?? undefined}
      className="inline-flex items-center gap-1 text-[11px] text-muted">
      <VerdictChip verdict={row.verdict} />
      {row.project_value != null && <span className="tabular-nums">
        peak {fmt(row.project_value, row.unit)}{same ? ` against ${fmt(row.limit, row.unit)}` : ''}</span>}
    </span>
  )
}

function PeriodRow({ link, period, value, unit, check, readOnly, target }: {
  link: string; period: string; value: number | { min: number; max: number } | null; unit: string
  check?: GridReadinessRow; readOnly: boolean; target: FlowTarget
}) {
  const project = useUIStore(s => s.currentProject) ?? ''
  const qc = useQueryClient()
  const [text, setText] = useState(typeof value === 'number' ? String(value) : '')
  // A save (or another edit) changes what is stored: the field follows it, the row's note stays.
  const [stored, setStored] = useState(value)
  if (JSON.stringify(stored) !== JSON.stringify(value)) {
    setStored(value)
    setText(typeof value === 'number' ? String(value) : '')
  }
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const n = Number(text)
  const ok = text.trim() !== '' && Number.isFinite(n) && n > 0
  const refresh = () => Promise.all([
    qc.invalidateQueries({ queryKey: ['campusElectrical', 'grid_readiness', project] }),
    qc.invalidateQueries({ queryKey: nk(project, 'results', 'eh_readiness') }),
    qc.invalidateQueries({ queryKey: nk(project, 'flow') }),
  ])
  const save = useMutation({
    mutationFn: () => putFlowEnergization(project, { link, period, mw: n }),
    onMutate: () => { setError(null); setNote(null) },
    onSuccess: r => { setNote(r.note); return refresh() },
    // The schedule may have changed under the row (a period gone, or now varying): read it again.
    onError: (e: unknown) => { setError(energizationRefusal(e)); void qc.invalidateQueries({ queryKey: nk(project, 'flow') }) },
  })
  const label = periodWords(period)
  const open = () => (useUIStore.getState().uiMode === 'expert' ? openFlowTarget(target) : openFlowTargetInPlace(target))
  const varies = value != null && typeof value === 'object'
  return (
    <li data-testid={`energization-period-${period}`} data-period={period} className="flex flex-col gap-1 text-[11px]">
      <div className="flex flex-wrap items-center gap-2">
        {varies ? (<>
          <span className="tabular-nums">{`${label} · ${rangeWords(value, unit)}`}</span>
          {!readOnly && <button type="button" className="underline" onClick={open}>Edit the link ›</button>}
        </>) : readOnly ? (
          <span className="tabular-nums">{`${label}: ${typeof value === 'number' ? `${Number(value.toPrecision(6))} ${unit}` : 'not set'}`}</span>
        ) : (<>
          <label className="flex items-center gap-1">
            <span className="w-20">{label}</span>
            <input aria-label={`Connection limit (${unit}), ${label}`} type="number" inputMode="decimal" min={0} step="any"
              value={text} aria-invalid={!ok} onChange={e => setText(e.target.value)}
              className="w-20 rounded border border-border bg-bg px-1 py-0.5 tabular-nums" />
            <span className="text-muted">{unit}</span>
          </label>
          <button type="button" aria-label={`Save connection limit, ${label}`}
            disabled={!ok || n === value || save.isPending} onClick={() => save.mutate()}
            className="rounded border border-border px-2 py-0.5 disabled:opacity-50">Save</button>
        </>)}
        <Check row={check} period={period} value={typeof value === 'number' ? value : null} />
      </div>
      {!varies && !readOnly && !ok && <p className="text-warn">Connection limit: must be above 0 {unit}.</p>}
      {error && <p role="alert" className="text-warn" data-testid={`energization-error-${period}`}>{error}</p>}
      {note && <p role="status" className="text-muted" data-testid={`energization-note-${period}`}>{note}</p>}
    </li>
  )
}

/** One scheduled link: "Connection limit (MW)" with its rating, then a row per period. The sentence
 *  on what the value means is the caller's, once per surface (`ENERGIZED_SENTENCE`). */
export function EnergizationRows({ entry, rating, unit, named, readiness }: {
  entry: FlowEnergization; rating: number; unit: string; named: boolean; readiness: GridReadinessRow[]
}) {
  // No edit control while the project is read-only (another user's edit lock, or a solve).
  const readOnly = useUIStore(s => s.readOnly)
  const periods = Object.keys(entry.by_period).sort(periodOrder)
  return (
    <li data-testid={`energization-link-${entry.link}`} className="text-[12px]">
      <span className="font-semibold">{`Connection limit (${unit})${named ? ` · ${displayName(entry.link)}` : ''}`}</span>
      <span className="text-muted">{`, per period; link rating ${Number(rating.toPrecision(6))} ${unit}`}</span>
      <ul className="mt-1 flex flex-col gap-1.5">
        {periods.map(p => {
          const check = checkFor(readiness, p)
          const target: FlowTarget = {
            section: 'connection', panel: null, component: 'Link', entity: entry.link, field: 'eh_energization_mw',
          }
          return <PeriodRow key={p} link={entry.link} period={p}
            value={entry.by_period[p]} unit={unit} check={check} readOnly={readOnly} target={target} />
        })}
      </ul>
    </li>
  )
}
