// Shared parts of the time-series editors (TS 01–04): the pending result
// with its numbers and warnings, the Apply, and the refinement ops.
//
// Every editor mounts these the same way wherever it is hosted (the
// Properties panel, the Time Series drawer, later the asset form's
// Availability block): host-agnostic props, no store reads besides the
// query client.
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, Plus, Trash2, Wand2 } from 'lucide-react'
import { Btn, Tag } from '../PageKit'
import {
  errorText, seriesApi,
  type PendingSeries, type RefineOp, type SeriesApplyResult, type SeriesStats,
} from '../../api/profiles'

export const INPUT =
  'px-2 py-1 text-xs border border-border rounded focus:outline-none focus:border-accent ' +
  'focus:ring-1 focus:ring-accent/20'

export type EditorMode = 'guided' | 'expert'

const fmt = (v: number | undefined | null, digits = 1) =>
  v == null || Number.isNaN(v) ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: digits })

function sumModelled(m: Record<string, number> | null | undefined): number | null {
  if (!m) return null
  return Object.values(m).reduce((a, b) => a + b, 0)
}

/** One pending artifact for one component: its numbers, warnings and Apply. */
export function PendingCard({
  project, component, pending, power, onApplied,
}: {
  project: string
  component: string
  pending: PendingSeries
  power: boolean
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
  const modelled = sumModelled(stats.modelled_mwh)
  const warnings = [...(pending.warnings ?? []),
    ...(pending.qa_warnings ?? []).filter(w => w.column === component).map(w => w.message)]
  // The preview ran apply's own alignment; when it could not, apply refuses
  // the same way, so the button would only fail.
  const blocked = warnings.find(w => w.startsWith('not_applicable:'))

  return (
    <div className="border border-border rounded p-2 mt-2" data-testid="pending-card">
      <div className="flex items-center gap-2 mb-1">
        <Tag tone={pending.provenance?.startsWith('measured') ? 'ok' : 'neutral'}>
          {pending.provenance ?? 'synthetic/assumed'}
        </Tag>
        {pending.refinements?.length ? <Tag tone="purple">refined</Tag> : null}
      </div>
      {power ? (
        <dl className="grid grid-cols-3 gap-1 text-[11px]">
          <div><dt className="text-muted">Peak</dt><dd data-testid="stat-peak">{fmt(stats.peak_mw)} MW</dd></div>
          <div><dt className="text-muted">Year</dt><dd data-testid="stat-annual">{fmt(stats.annual_mwh, 0)} MWh</dd></div>
          <div>
            <dt className="text-muted" title="What this model's snapshots count">In model</dt>
            <dd data-testid="stat-modelled">{modelled == null ? '—' : `${fmt(modelled, 0)} MWh`}</dd>
          </div>
        </dl>
      ) : (
        <dl className="grid grid-cols-3 gap-1 text-[11px]">
          <div><dt className="text-muted">Min</dt><dd>{fmt(stats.min, 3)}</dd></div>
          <div><dt className="text-muted">Mean</dt><dd>{fmt(stats.mean, 3)}</dd></div>
          <div><dt className="text-muted">Max</dt><dd>{fmt(stats.max, 3)}</dd></div>
        </dl>
      )}
      {warnings.length > 0 && (
        <ul className="mt-1 text-[11px] text-amber-700 list-disc pl-4" data-testid="pending-warnings">
          {warnings.map(w => <li key={w}>{w}</li>)}
        </ul>
      )}
      <div className="flex items-center gap-2 mt-2">
        <Btn variant="primary" onClick={() => apply.mutate()} disabled={apply.isPending || !!blocked}
          title={blocked ? 'This network cannot take the series yet; see the warning' : undefined}>
          <Check size={13} /> Apply to {component}
        </Btn>
        {apply.isError && <span className="text-[11px] text-red-600" data-testid="apply-error">{errorText(apply.error)}</span>}
      </div>
      {applied && (
        <div className="text-[11px] text-green-700 mt-1" data-testid="applied">
          Applied to {applied.component} {applied.attribute} (peak {fmt(applied.peak, 3)}). Undo reverts it.
          {applied.warnings?.length > 0 && (
            <ul className="text-amber-700 list-disc pl-4">
              {applied.warnings.map(w => <li key={w}>{w}</li>)}
            </ul>
          )}
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

function draftToOp(d: Draft, power: boolean): RefineOp {
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
      return { op: d.kind, mwh: n(d.a) }
  }
}

function describe(op: RefineOp): string {
  const o = op as Record<string, unknown>
  switch (op.op) {
    case 'scale_peak_to':
      return o.of ? `peak to ${Number(o.fraction) * 100} % of ${String(o.of).replace(/_/g, ' ')}`
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
  const kinds = (Object.keys(OP_LABELS) as OpKind[]).filter(k => power || k !== 'rescale_energy')

  return (
    <div className="mt-2 border-t border-border pt-2" data-testid="refine-panel">
      <p className="text-[11px] text-muted mb-1">
        Refine: each edit is applied in order. Put a peak or energy target last.
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
        <Btn onClick={() => { setOps([...ops, draftToOp(d, power)]); setD({ ...d, a: '', b: '', c: '' }) }}
          disabled={!d.a || (d.kind === 'scale_window' && (!d.b || !d.c))}>
          <Plus size={12} /> Add
        </Btn>
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
        {refine.isError && <span className="text-[11px] text-red-600" data-testid="refine-error">{errorText(refine.error)}</span>}
      </div>
    </div>
  )
}
