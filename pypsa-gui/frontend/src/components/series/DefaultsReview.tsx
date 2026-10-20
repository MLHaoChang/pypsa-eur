// "Generate defaults for all" (TS UI; UX review, gatekeeper plan D): one
// review list over every family's default, in the Time Series header. Each
// row is a pending default with why it was picked (assumed) and how it is
// sized, or why the component is skipped. Apply all writes the ticked rows
// under one undo step and reports each row; a measured series is never
// replaced. Renewables join the list through their family's own plugin.
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronDown, ChevronRight } from 'lucide-react'
import { Btn, Seg, Tag } from '../PageKit'
import { errorText, seriesApi, type DefaultRow, type DefaultsApplyResult } from '../../api/profiles'
import { WeekChart } from './SeriesKit'

const fmt = (v: number | undefined, d = 2) =>
  v == null ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: d })

export default function DefaultsReview({ project }: { project: string }) {
  const qc = useQueryClient()
  const [replace, setReplace] = useState(false)
  const [peakSized, setPeakSized] = useState<string[]>([])
  const [ticked, setTicked] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<string | null>(null)
  const [result, setResult] = useState<DefaultsApplyResult | null>(null)

  const preview = useMutation({
    mutationFn: () => seriesApi.previewDefaults(project, { replace_generated: replace, peak_sized: peakSized }),
    onSuccess: r => setTicked(new Set(r.rows.filter(x => x.job_id).map(x => x.component))),
  })
  // Re-preview when the options change: sizing changes the generated rows.
  useEffect(() => { setResult(null); preview.mutate() }, [replace, peakSized.join('|')]) // eslint-disable-line react-hooks/exhaustive-deps

  const rows = preview.data?.rows ?? []
  const planned = useMemo(() => rows.filter(r => r.job_id), [rows])
  const skipped = useMemo(() => rows.filter(r => r.skipped), [rows])

  const apply = useMutation({
    mutationFn: () => seriesApi.applyDefaults(project,
      planned.filter(r => ticked.has(r.component)).map(r => r.job_id as string)),
    onSuccess: r => { setResult(r); void qc.invalidateQueries() },
  })

  const toggle = (c: string) => setTicked(t => {
    const n = new Set(t)
    if (n.has(c)) n.delete(c)
    else n.add(c)
    return n
  })
  const setSizing = (r: DefaultRow, v: 'mean' | 'peak') =>
    setPeakSized(p => (v === 'peak' ? [...p.filter(x => x !== r.component), r.component] : p.filter(x => x !== r.component)))

  return (
    <div className="text-xs" data-testid="defaults-review">
      <p className="text-[11px] text-muted mb-2">
        A default for every component without a series. Each one is a stated assumption: check why it was
        picked before applying. Sized to keep the model&apos;s energy (its mean is the load&apos;s current
        demand) unless you size it by its peak. A measured upload is never replaced.
      </p>
      <label className="flex items-center gap-2 mb-2">
        <input type="checkbox" checked={replace} onChange={e => setReplace(e.target.checked)} />
        Also replace series generated earlier
      </label>
      {preview.isPending && <p className="text-muted">Generating defaults…</p>}
      {preview.isError && <p className="text-red-600" data-testid="defaults-error">{errorText(preview.error)}</p>}

      {planned.length > 0 && (
        <table className="w-full border-collapse" data-testid="defaults-rows">
          <thead>
            <tr className="text-left text-muted text-[10px] uppercase">
              <th className="w-6" /><th>Component</th><th>Default</th><th>Sized by</th>
              <th className="text-right">Peak MW</th><th className="text-right">Mean MW</th>
            </tr>
          </thead>
          <tbody>
            {planned.map(r => (
              <FragmentRow key={r.component} r={r} ticked={ticked.has(r.component)} open={open === r.component}
                onTick={() => toggle(r.component)} onOpen={() => setOpen(open === r.component ? null : r.component)}
                onSizing={v => setSizing(r, v)} />
            ))}
          </tbody>
        </table>
      )}

      {skipped.length > 0 && (
        <div className="mt-2" data-testid="defaults-skipped">
          <p className="text-muted text-[10px] uppercase">Skipped</p>
          <ul className="list-disc pl-4 text-[11px]">
            {skipped.map(r => <li key={`${r.family}:${r.component}`}><b>{r.component}</b>: {r.skipped}</li>)}
          </ul>
        </div>
      )}

      <div className="flex items-center gap-2 mt-3">
        <Btn variant="primary" onClick={() => apply.mutate()} disabled={!ticked.size || apply.isPending || preview.isPending}>
          <Check size={13} /> Apply {ticked.size} default{ticked.size === 1 ? '' : 's'}
        </Btn>
        {apply.isError && <span className="text-red-600">{errorText(apply.error)}</span>}
      </div>
      {result && (
        <div className="mt-2 text-[11px]" data-testid="defaults-result">
          <p className="text-green-700">Applied {result.applied}. One undo reverts them all.</p>
          <ul className="list-disc pl-4">
            {result.results.filter(x => !x.applied).map(x => (
              <li key={x.job_id} className="text-amber-700">{x.component ?? x.job_id}: {x.message}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function FragmentRow({ r, ticked, open, onTick, onOpen, onSizing }: {
  r: DefaultRow; ticked: boolean; open: boolean
  onTick: () => void; onOpen: () => void; onSizing: (v: 'mean' | 'peak') => void
}) {
  return (
    <>
      <tr className="border-t border-border align-top">
        <td><input type="checkbox" aria-label={`Apply the default for ${r.component}`} checked={ticked} onChange={onTick} /></td>
        <td>
          <button type="button" className="flex items-center gap-1" onClick={onOpen} aria-expanded={open}>
            {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}{r.component}
          </button>
        </td>
        <td>
          <span className="mr-1">{(r.kind ?? '').replace(/_/g, ' ')}</span>
          {r.assumed && <Tag tone="neutral">assumed</Tag>}
          <div className="text-[10px] text-muted" data-testid={`reason-${r.component}`}>{r.reason}</div>
          {r.warnings?.length ? (
            <ul className="text-[10px] text-amber-700 list-disc pl-4">
              {r.warnings.map(w => <li key={w}>{w}</li>)}
            </ul>
          ) : null}
        </td>
        <td>
          <Seg value={r.sizing ?? 'mean'} onChange={onSizing}
            options={[{ value: 'mean', label: 'Mean' }, { value: 'peak', label: 'Peak' }]} />
        </td>
        <td className="text-right">{fmt(r.stats?.peak_mw)}</td>
        <td className="text-right">{fmt(r.stats?.mean_mw)}</td>
      </tr>
      {open && r.week && (
        <tr><td /><td colSpan={5}><WeekChart week={r.week} unit="MW" /></td></tr>
      )}
    </>
  )
}
