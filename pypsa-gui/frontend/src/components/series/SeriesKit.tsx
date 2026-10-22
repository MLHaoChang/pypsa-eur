// Shared parts of the time-series editors (TS 01–04): the pending result
// with its numbers and warnings, the Apply, and the refinement ops.
//
// Every editor mounts these the same way wherever it is hosted (the
// Properties panel, the Time Series drawer, later the asset form's
// Availability block): host-agnostic props, no store reads besides the
// query client.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Check, Download, Plus, Trash2, Upload, Wand2 } from 'lucide-react'
import { Btn, Tag } from '../PageKit'
import { StatePill } from '../ResultState'
import { networkApi } from '../../api/network'
import { openTimeSeriesFor } from '../../layout/flowNavigation'
import {
  errorKind, errorText, seriesApi,
  type PendingSeries, type PreviewWeek, type RefineOp, type SeriesApplyResult, type SeriesStats,
} from '../../api/profiles'

export const INPUT =
  'px-2 py-1 text-xs border border-border rounded focus:outline-none focus:border-accent ' +
  'focus:ring-1 focus:ring-accent/20'

export type EditorMode = 'guided' | 'expert'

const fmt = (v: number | undefined | null, digits = 1) =>
  v == null || Number.isNaN(v) ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: digits })

/** A series field in plain words, never its PyPSA attribute. */
const FIELD_LABELS: Record<string, string> = {
  p_set: 'load', p_max_pu: 'availability', p_min_pu: 'must-run floor',
  marginal_cost: 'price', inflow: 'inflow', efficiency: 'COP',
}
export const fieldLabel = (attribute: string) => FIELD_LABELS[attribute] ?? 'series'

/** Provenance in the five plain words: measured, generated, assumed, from
 *  the network, missing. A generated series' backend record reads
 *  "synthetic/assumed"; only a guessed default is "assumed". */
export function provenanceWord(provenance: string | null | undefined, assumed = false): string {
  if (provenance == null) return assumed ? 'assumed' : 'generated'
  if (provenance === 'uploaded' || provenance.startsWith('measured')) return 'measured'
  if (provenance === 'network') return 'from the network'
  if (provenance === 'missing') return 'missing'
  return assumed ? 'assumed' : 'generated'
}

/** Why a button is off, and what turns it on: shown in place, never only
 *  as a tooltip (no dead ends). */
export function DisabledReason({ reason }: { reason: string | null }) {
  return reason ? <span className="text-[11px] text-muted" data-testid="disabled-reason">{reason}</span> : null
}

/** The editors' "Measured file" tab, the same wherever the editor is
 *  hosted: one file in the row template's layout (the model's hours, a
 *  column named after the component), read into a pending series that the
 *  card previews and applies like a generated one. A file that does not fit
 *  says why and what to change, here, with the template one click away. */
export function MeasuredFileTab({
  project, componentType, attribute, component, onPending,
}: {
  project: string
  componentType: 'generators' | 'links'
  attribute: string
  component: string
  onPending: (p: PendingSeries) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const read = useMutation({
    mutationFn: () => seriesApi.uploadSeries(project, file as File,
      { component_type: componentType, attribute, component }),
    onSuccess: onPending,
  })
  const template = async () => {
    const blob = componentType === 'links'
      ? await networkApi.downloadLinkTemplate(attribute, undefined, component)
      : await networkApi.downloadGeneratorTemplate('conventional', attribute, undefined, component)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${component.replace(/\s+/g, '_')}_${fieldLabel(attribute).replace(/\s+/g, '_')}_template.xlsx`
    a.click()
    URL.revokeObjectURL(url)
  }
  return (
    <div className="mt-2 space-y-1" data-testid="measured-file-tab">
      <p className="text-[11px] text-muted">
        One value per model hour in the {fieldLabel(attribute)}&apos;s own units, in a column named
        after {component} (or a single column).
      </p>
      <input aria-label="Measured file" type="file" accept=".csv,.xlsx,.xls" className="block max-w-full"
        onChange={e => { setFile(e.target.files?.[0] ?? null); read.reset() }} />
      <button type="button" className="block text-[11px] text-accent hover:underline" onClick={() => void template()}>
        <Download size={11} className="inline" /> Download the template
      </button>
      <div className="flex items-center gap-2">
        <Btn variant="primary" onClick={() => read.mutate()} disabled={!file || read.isPending}>
          <Upload size={12} /> Read file
        </Btn>
        <DisabledReason reason={file ? null : 'Choose a measured file to read.'} />
      </div>
      {read.isError && (
        <p className="text-[11px] text-red-600" data-testid="measured-file-error">
          {sentence(errorText(read.error))}
        </p>
      )}
    </div>
  )
}

/** A field with no editor yet (a renewable's or demand response's profile):
 *  its series' state in plain words and the one way to set it, the row
 *  upload in Time Series. No second upload here. */
export function SeriesStateLink({
  project, componentType, attribute, component,
}: {
  project: string
  componentType: 'generators' | 'links'
  attribute: string
  component: string
}) {
  const { data } = useQuery({
    queryKey: ['series-current', project, componentType, attribute, component],
    queryFn: () => seriesApi.current(project, componentType, attribute, component),
  })
  const state = data == null ? '…' : data.exists ? provenanceWord(data.provenance) : 'missing'
  return (
    <div className="flex items-center gap-2 text-[11px]" data-testid="series-state">
      <span className="text-muted">Profile:</span>
      <span>{state}{data?.exists ? `, ${data.rows} h` : ''}</span>
      <button type="button" className="text-accent hover:underline"
        onClick={() => openTimeSeriesFor(componentType === 'links' ? 'Link' : 'Generator', component,
          { field: attribute })}>
        Upload in Time Series ›
      </button>
    </div>
  )
}

/** A backend refusal as a sentence: they start in lower case. */
export const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

function sumModelled(m: Record<string, number> | null | undefined): number | null {
  if (!m) return null
  return Object.values(m).reduce((a, b) => a + b, 0)
}

/** What the component carries now, and a way to refine it: an upload is
 *  shown as measured and the refinement ops act on it like on a generated
 *  series (UX review; gatekeeper plan M). */
export function CurrentSeriesLine({
  project, componentType, attribute, component, onPending,
}: {
  project: string
  componentType: string
  attribute: string
  component: string
  onPending: (p: PendingSeries) => void
}) {
  const { data } = useQuery({
    queryKey: ['series-current', project, componentType, attribute, component],
    queryFn: () => seriesApi.current(project, componentType, attribute, component),
  })
  const take = useMutation({
    mutationFn: () => seriesApi.fromCurrent(project, componentType, attribute, component),
    onSuccess: onPending,
  })
  if (!data?.exists) return null
  const what = `${provenanceWord(data.provenance)}${data.kind ? `, ${String(data.kind).replace(/_/g, ' ')}` : ''}`
  return (
    <div className="flex items-center gap-2 text-[11px] mb-2" data-testid="current-series">
      <span className="text-muted">Current {fieldLabel(attribute)}:</span>
      <span>{what}, {data.rows} h{data.refinements ? `, refined ×${data.refinements}` : ''}</span>
      <Btn onClick={() => take.mutate()} disabled={take.isPending}><Wand2 size={12} /> Refine this series</Btn>
      {take.isError && <span className="text-red-600" data-testid="current-error">{errorText(take.error)}</span>}
    </div>
  )
}

// Plain titles for the apply path's warning codes (`profile_gen/apply.py`,
// `refine.py`, `defaults.py`): a row reads as its title, the backend's
// sentence sits behind a disclosure, the code stays for support and search.
export const SERIES_WARNING_TITLES: Record<string, string> = {
  time_basis_assumed_utc: 'Time zone assumed UTC',
  energy_mismatch: 'Energy differs from the source year',
  bound_forces_off: 'Minimum above maximum forces the unit off',
  must_run_exceeds_sink: 'Must-run floor exceeds what the bus can take',
  min_stable_not_must_run: 'Minimum stable output is not a must-run',
  replaces_existing_series: 'Replaces the current series',
  unsupported_storage_inflow: 'Storage type does not take an inflow',
  money_basis_unverified: 'Currency basis not checked against the project',
  earlier_target_broken: 'A later edit undid an earlier target',
  connection_changed_since_refinement: 'Grid connection changed since this was refined',
  not_applicable: 'Cannot be applied to this model',
  default_peak_above_connection: 'Peak above the grid connection',
}

function splitWarning(w: string): { code: string | null; text: string } {
  const m = /^([a-z][a-z0-9_]*):\s*(.*)$/s.exec(w)
  return m ? { code: m[1], text: m[2] } : { code: null, text: w }
}

/** Warnings as one line each (a plain title), the sentence on demand. */
export function WarningList({ warnings, testId }: { warnings: string[]; testId?: string }) {
  if (!warnings.length) return null
  return (
    <ul className="mt-1 text-[11px] text-amber-700 space-y-0.5" data-testid={testId}>
      {warnings.map(w => {
        const { code, text } = splitWarning(w)
        // An unmapped code shows its sentence alone; the code stays in the
        // data attribute for support and search, never in the copy.
        const title = code ? SERIES_WARNING_TITLES[code] ?? null : null
        return (
          <li key={w} data-code={code ?? undefined}>
            {title ? (
              <details>
                <summary className="cursor-pointer">{title}</summary>
                <p className="pl-3 text-[10px]">{text}</p>
              </details>
            ) : text}
          </li>
        )
      })}
    </ul>
  )
}

const DAY = new Intl.DateTimeFormat('en-GB', { day: 'numeric', timeZone: 'UTC' })
const DAY_MONTH = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })
const FULL = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
// Snapshots are naive; read them as written (UTC), never shifted by the browser.
const stamp = (s: string) => new Date(s.endsWith('Z') ? s : `${s}Z`)

/** "14–20 Jan 2030", or "30 Jun – 6 Jul 2025" across a month. */
export function weekDates(week: PreviewWeek): string {
  const a = stamp(week.snapshots[0])
  const b = stamp(week.snapshots[week.snapshots.length - 1])
  if (a.getUTCMonth() === b.getUTCMonth() && a.getUTCFullYear() === b.getUTCFullYear()) {
    return `${DAY.format(a)}–${FULL.format(b)}`
  }
  return `${DAY_MONTH.format(a)} – ${FULL.format(b)}`
}

/** The preview week: what apply would write, the current series faint
 *  behind it, labelled with the week it shows and described in text. */
export function WeekChart({ week, unit }: { week: PreviewWeek; unit?: string }) {
  const rows = week.snapshots.map((t, i) => ({ t, new: week.new[i], current: week.current?.[i] }))
  const v = week.new.filter(x => Number.isFinite(x))
  const mean = v.reduce((a, b) => a + b, 0) / Math.max(v.length, 1)
  const u = unit ? ` ${unit}` : ''
  const when = `${week.label}: ${weekDates(week)}${week.period != null ? ` (period ${week.period})` : ''}`
  const text = `${when}. New series peak ${fmt(Math.max(...v), 3)}${u}, mean ${fmt(mean, 3)}${u}, `
    + `min ${fmt(Math.min(...v), 3)}${u}${week.current ? '; the current series is the faint line' : ''}.`
  return (
    <figure className="mt-2" data-testid="week-chart">
      <figcaption className="text-[11px] text-muted mb-0.5">{when}</figcaption>
      <div className="h-24" aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 2, right: 4, bottom: 0, left: 0 }}>
            <XAxis dataKey="t" hide />
            <YAxis width={36} tick={{ fontSize: 9 }} />
            <Tooltip labelFormatter={l => String(l).replace('T', ' ').slice(0, 16)}
              formatter={(val: number, name: string) => [`${fmt(val, 3)}${u}`, name === 'new' ? 'New' : 'Current']} />
            {week.current && (
              <Line dataKey="current" stroke="currentColor" strokeOpacity={0.3} dot={false} isAnimationActive={false} />
            )}
            <Line dataKey="new" stroke="var(--color-accent, #e5484d)" dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="sr-only" data-testid="week-chart-text">{text}</p>
    </figure>
  )
}

/** One pending artifact for one component: its numbers, warnings and Apply. */
export function PendingCard({
  project, component, pending, power, unit, onApplied, attribute,
}: {
  project: string
  component: string
  pending: PendingSeries
  power: boolean
  /** The target field, for the receipt's wording. */
  attribute?: string
  /** The unit of a non-power series' min/mean/max (MW for an inflow). */
  unit?: string
  onApplied?: (r: SeriesApplyResult) => void
}) {
  const qc = useQueryClient()
  const [applied, setApplied] = useState<SeriesApplyResult | null>(null)
  const apply = useMutation({
    mutationFn: () => seriesApi.apply(project, { job_id: pending.job_id, component }),
    onSuccess: r => {
      setApplied(r)
      onApplied?.(r)
      void qc.invalidateQueries()
    },
  })
  const stats = (pending.stats?.[component] ?? {}) as SeriesStats
  const windowMean = pending.profile_annual_basis === 'window_mean'
  const modelled = sumModelled(stats.modelled_mwh)
  const warnings = [...(pending.warnings ?? []),
    ...(pending.qa_warnings ?? []).filter(w => w.column === component).map(w => w.message)]
  // The preview ran apply's own alignment; when it could not, apply refuses
  // the same way, so the button would only fail.
  const blocked = warnings.find(w => w.startsWith('not_applicable:'))
  // Made from a series that has changed since, or against a connection
  // that has: the shared "Out of date" pill, with the reason beside it.
  const stale = errorKind(apply.error) === 'current_changed_since_refinement'
    || warnings.some(w => w.startsWith('connection_changed_since_refinement:'))
  const u = power ? ' MW' : unit ? ` ${unit}` : ''

  return (
    <div className="border border-border rounded p-2 mt-2" data-testid="pending-card">
      <div className="flex items-center gap-2 mb-1">
        <Tag tone={provenanceWord(pending.provenance) === 'measured' ? 'ok' : 'neutral'}>
          {provenanceWord(pending.provenance, pending.assumed === true)}
        </Tag>
        {pending.refinements?.length ? <Tag tone="purple">refined</Tag> : null}
        {stale && <StatePill state="stale" />}
      </div>
      {power ? (
        <dl className="grid grid-cols-3 gap-1 text-[11px]">
          <div><dt className="text-muted">Peak</dt><dd data-testid="stat-peak">{fmt(stats.peak_mw)} MW</dd></div>
          <div><dt className="text-muted" title={windowMean ? 'The snapshot window\'s mean power over a year: the year outside the window is unknown' : undefined}>{windowMean ? 'Year (window mean)' : 'Year'}</dt><dd data-testid="stat-annual">{fmt(stats.annual_mwh, 0)} MWh</dd></div>
          <div>
            <dt className="text-muted" title="What this model's snapshots count">In model</dt>
            <dd data-testid="stat-modelled">{modelled == null ? '—' : `${fmt(modelled, 0)} MWh`}</dd>
          </div>
        </dl>
      ) : (
        <dl className="grid grid-cols-4 gap-1 text-[11px]">
          <div><dt className="text-muted">Min</dt><dd data-testid="stat-min">{fmt(stats.min, 3)}{unit ? ` ${unit}` : ''}</dd></div>
          <div><dt className="text-muted">Mean</dt><dd>{fmt(stats.mean, 3)}{unit ? ` ${unit}` : ''}</dd></div>
          <div><dt className="text-muted">Max</dt><dd data-testid="stat-max">{fmt(stats.max, 3)}{unit ? ` ${unit}` : ''}</dd></div>
          {stats.annual_mwh != null && (
            <div><dt className="text-muted" title={windowMean ? 'The snapshot window\'s mean power over a year: the year outside the window is unknown' : undefined}>{windowMean ? 'Year (window mean)' : 'Year'}</dt><dd data-testid="stat-annual">{fmt(stats.annual_mwh, 0)} MWh</dd></div>
          )}
        </dl>
      )}
      {pending.weeks?.[component] && (
        <WeekChart week={pending.weeks[component]} unit={power ? 'MW' : unit} />
      )}
      <WarningList warnings={warnings} testId="pending-warnings" />
      {/* Applied once: the receipt replaces the button (a second Apply would
          only write the same series again). */}
      {!applied && <div className="flex items-center gap-2 mt-2">
        <Btn variant="primary" onClick={() => apply.mutate()} disabled={apply.isPending || !!blocked}>
          <Check size={13} /> Apply to {component}
        </Btn>
        {blocked && (
          <span className="text-[11px] text-muted" data-testid="apply-blocked">
            This network cannot take the series yet: see the warning above.
          </span>
        )}
        {apply.isError && <span className="text-[11px] text-red-600" data-testid="apply-error">{errorText(apply.error)}</span>}
      </div>}
      {applied && (
        <div className="text-[11px] text-green-700 mt-1" data-testid="applied">
          Applied to {applied.component}&apos;s {fieldLabel(applied.attribute ?? attribute ?? '')}:
          {' '}{power ? 'peak' : 'max'} {applied.before ? `${fmt(applied.before.peak, 3)} → ` : ''}{fmt(applied.peak, 3)}{u}
          {!power && <>, min {applied.before ? `${fmt(applied.before.min, 3)} → ` : ''}{fmt(applied.min, 3)}{u}</>}.
          {' '}Undo (Ctrl+Z) reverts it.
          {/* Only what the preview did not already say. */}
          <WarningList warnings={(applied.warnings ?? []).filter(w => !warnings.includes(w))} />
        </div>
      )}
    </div>
  )
}

// ── Refinement ops (TS-04): the same deterministic edits the copilot emits ──

type OpKind = 'scale_peak_to' | 'scale_window' | 'set_floor' | 'set_cap' | 'shift' | 'smooth' | 'rescale_energy'

const OP_LABELS: Record<OpKind, string> = {
  scale_peak_to: 'Scale the peak to',
  scale_window: 'Scale an hour window',
  set_floor: 'Floor',
  set_cap: 'Cap',
  shift: 'Shift by hours',
  smooth: 'Smooth',
  rescale_energy: 'Rescale the annual energy',
}

interface Draft { kind: OpKind; a: string; b: string; c: string; of: string }

function draftToOp(d: Draft, power: boolean, windowSource = false): RefineOp {
  const n = (s: string) => Number(s)
  switch (d.kind) {
    case 'scale_peak_to':
      if (d.of === 'mw') return power ? { op: d.kind, mw: n(d.a) } : { op: d.kind, value: n(d.a) }
      return { op: d.kind, fraction: n(d.a) / 100, of: d.of }
    case 'scale_window':
      return { op: d.kind, hours: [n(d.a), n(d.b)], factor: n(d.c) }
    case 'set_floor': case 'set_cap':
      return { op: d.kind, value: n(d.a) }
    case 'shift':
      return { op: d.kind, hours: Math.trunc(n(d.a)) }
    case 'smooth':
      return { op: d.kind, window_h: Math.trunc(n(d.a)) }
    case 'rescale_energy':
      return windowSource ? { op: d.kind, mwh: n(d.a), basis: 'window' } : { op: d.kind, mwh: n(d.a) }
  }
}

function describe(op: RefineOp): string {
  const o = op as Record<string, unknown>
  switch (op.op) {
    case 'scale_peak_to':
      return o.of ? `peak to ${Number(o.fraction) * 100} % of the ${o.of === 'connection' ? 'grid connection' : 'current peak'}`
        : `peak to ${o.mw ?? o.value}`
    case 'scale_window': {
      const [s, e] = o.hours as number[]
      return `${String(s).padStart(2, '0')}–${String(e).padStart(2, '0')} h × ${o.factor}`
    }
    default:
      return `${OP_LABELS[op.op as OpKind] ?? op.op} ${Object.entries(o).filter(([k]) => k !== 'op').map(([, v]) => v).join(' ')}`
  }
}

/** Builds an ordered op list and refines a pending artifact into a new one. */
export function RefinePanel({
  project, pending, power, onRefined,
}: {
  project: string
  pending: PendingSeries
  power: boolean
  onRefined: (p: PendingSeries) => void
}) {
  const [ops, setOps] = useState<RefineOp[]>([])
  const [d, setD] = useState<Draft>({ kind: 'scale_peak_to', a: '', b: '', c: '', of: 'mw' })
  const refine = useMutation({
    mutationFn: () => seriesApi.refine(project, pending.job_id, ops),
    onSuccess: r => { setOps([]); onRefined(r) },
  })
  // A series read from a snapshot window knows no hours outside it: no
  // shift or smoothing, and its energy target is the window's MWh.
  const windowSource = pending.profile_annual_basis === 'window_mean'
  const addMissing = !d.a ? (d.kind === 'scale_window' ? 'Enter the hours and the factor to add this edit.' : 'Enter a value to add this edit.')
    : d.kind === 'scale_window' && (!d.b || !d.c) ? 'Enter the hours and the factor to add this edit.' : null
  const kinds = (Object.keys(OP_LABELS) as OpKind[]).filter(k => (power || k !== 'rescale_energy')
    && !(windowSource && (k === 'shift' || k === 'smooth')))

  return (
    <div className="mt-2 border-t border-border pt-2" data-testid="refine-panel">
      <p className="text-[11px] text-muted mb-1">
        Refine: each edit is applied in order. Put a peak or energy target last.
        {windowSource && ' Read from the snapshot window: energy is the window\'s MWh.'}
      </p>
      <div className="flex flex-wrap items-center gap-1">
        <select aria-label="Refinement" className={INPUT} value={d.kind}
          onChange={e => setD({ ...d, kind: e.target.value as OpKind })}>
          {kinds.map(k => <option key={k} value={k}>{OP_LABELS[k]}</option>)}
        </select>
        {d.kind === 'scale_window' ? (
          <>
            <input aria-label="From hour" className={`${INPUT} w-14`} value={d.a} onChange={e => setD({ ...d, a: e.target.value })} placeholder="5" />
            <input aria-label="To hour" className={`${INPUT} w-14`} value={d.b} onChange={e => setD({ ...d, b: e.target.value })} placeholder="10" />
            <input aria-label="Factor" className={`${INPUT} w-16`} value={d.c} onChange={e => setD({ ...d, c: e.target.value })} placeholder="1.3" />
          </>
        ) : (
          <input aria-label="Value" className={`${INPUT} w-20`} value={d.a} onChange={e => setD({ ...d, a: e.target.value })} />
        )}
        {d.kind === 'scale_peak_to' && (
          <select aria-label="Peak basis" className={INPUT} value={d.of} onChange={e => setD({ ...d, of: e.target.value })}>
            <option value="mw">{power ? 'MW' : 'value'}</option>
            <option value="current_peak">% of the current peak</option>
            {power && <option value="connection">% of the grid connection</option>}
          </select>
        )}
        <Btn onClick={() => { setOps([...ops, draftToOp(d, power, windowSource)]); setD({ ...d, a: '', b: '', c: '' }) }}
          disabled={!!addMissing}>
          <Plus size={12} /> Add
        </Btn>
        {addMissing && <span className="text-[11px] text-muted" data-testid="refine-add-reason">{addMissing}</span>}
      </div>
      {ops.length > 0 && (
        <ol className="mt-1 text-[11px] list-decimal pl-5" data-testid="refine-ops">
          {ops.map((op, i) => (
            <li key={i} className="flex items-center gap-1">
              <span>{describe(op)}</span>
              <button type="button" aria-label={`Remove edit ${i + 1}`} className="text-muted hover:text-danger"
                onClick={() => setOps(ops.filter((_, j) => j !== i))}><Trash2 size={11} /></button>
            </li>
          ))}
        </ol>
      )}
      <div className="flex items-center gap-2 mt-1">
        <Btn onClick={() => refine.mutate()} disabled={!ops.length || refine.isPending}>
          <Wand2 size={12} /> Refine
        </Btn>
        {!ops.length && <span className="text-[11px] text-muted">Add an edit above to refine.</span>}
        {refine.isError && <span className="text-[11px] text-red-600" data-testid="refine-error">{errorText(refine.error)}</span>}
      </div>
    </div>
  )
}
