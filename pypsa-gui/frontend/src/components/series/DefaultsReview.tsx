// "Generate defaults for all" (TS UI; UX review, gatekeeper plan D): one
// review list over every family's default, in the Time Series header. Each
// row is a pending default with why it was picked (assumed) and how it is
// sized, or why the component is skipped. Apply all writes the ticked rows
// under one undo step and reports each row; a measured series is never
// replaced. Wind and solar join as one block (RP-04b): one generation per
// technology with the weather bar's year, written with one batch apply.
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronDown, ChevronRight } from 'lucide-react'
import { Btn, Seg, Tag } from '../PageKit'
import { errorText, profilesApi, seriesApi, type DefaultRow, type DefaultsApplyResult } from '../../api/profiles'
import { nk } from '../../utils/queryKeys'
import { generateAndApply, notReadyReason, readyRows, type GenerationOutcome } from './renewableGeneration'
import { weatherKey } from './WeatherBar'
import { WarningList, WeekChart } from './SeriesKit'

const fmt = (v: number | undefined, d = 2) =>
  v == null ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: d })

/** A row's fix target: its own series editor, for the families the host
 *  can open one for (no link where it cannot: no dead ends). */
export interface OpenEditor {
  families: ReadonlySet<string>
  open: (component: string, family: string) => void
}

export default function DefaultsReview({ project, onOpenEditor }: {
  project: string
  onOpenEditor?: OpenEditor
}) {
  const qc = useQueryClient()
  const [replace, setReplace] = useState(false)
  const [peakSized, setPeakSized] = useState<string[]>([])
  const [ticked, setTicked] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<string | null>(null)
  const [result, setResult] = useState<DefaultsApplyResult | null>(null)
  const [renewTick, setRenewTick] = useState(true)
  const [renewResult, setRenewResult] = useState<GenerationOutcome[] | null>(null)

  const readiness = useQuery({ queryKey: nk(project, 'profile_readiness'), queryFn: () => profilesApi.readiness(project) })
  const weather = useQuery({ queryKey: weatherKey(project), queryFn: () => profilesApi.weather(project) })
  const allRenewables = readiness.data ?? []

  const preview = useMutation({
    mutationFn: () => seriesApi.previewDefaults(project, { replace_generated: replace, peak_sized: peakSized }),
    onSuccess: r => setTicked(new Set(r.rows.filter(x => x.job_id).map(x => x.component))),
  })
  // Re-preview when the options change: sizing changes the generated rows.
  useEffect(() => { setResult(null); setRenewResult(null); preview.mutate() }, [replace, peakSized.join('|')]) // eslint-disable-line react-hooks/exhaustive-deps

  const rows = preview.data?.rows ?? []
  const planned = useMemo(() => rows.filter(r => r.job_id), [rows])
  const skipped = useMemo(() => rows.filter(r => r.skipped), [rows])

  const renewables = useMemo(() => readyRows(allRenewables, replace), [allRenewables, replace])
  const renewCount = renewTick ? renewables.length : 0

  const apply = useMutation({
    mutationFn: async () => {
      const jobs = planned.filter(r => ticked.has(r.component)).map(r => r.job_id as string)
      const series = jobs.length ? await seriesApi.applyDefaults(project, jobs) : null
      const generated = renewCount ? await generateAndApply(project, renewables) : null
      return { series, generated }
    },
    onSuccess: r => { setResult(r.series); setRenewResult(r.generated); void qc.invalidateQueries() },
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

      <div className="mb-2 border border-border rounded p-2" data-testid="defaults-renewables">
        <p className="text-[10px] uppercase text-muted">Wind and solar</p>
        {readiness.isPending ? <p className="text-muted">Reading the wind and solar generators…</p>
          : readiness.isError ? <p className="text-[11px] text-warn" data-testid="defaults-renewables-reason">The wind and solar generators could not be read.</p>
          : renewables.length > 0 ? (
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={renewTick} onChange={e => setRenewTick(e.target.checked)}
                aria-label="Generate weather profiles for the ready wind and solar generators" />
              <span>
                Generate a weather profile for {renewables.length} generator{renewables.length === 1 ? '' : 's'}
                {' '}({renewables.map(r => r.generator).join(', ')}).
                <span className="block text-[10px] text-muted" data-testid="defaults-renewables-weather">
                  {weather.data
                    ? `${weather.data.source === 'atlite' ? 'ERA5' : 'Synthetic'} weather, ${weather.data.year} (${weather.data.year_source === 'saved' ? 'your choice' : weather.data.year_source === 'snapshots' ? 'from your snapshots' : 'default'}). Change them in the weather bar of Renewables.`
                    : 'The weather choice is read from the weather bar of Renewables.'}
                </span>
              </span>
            </label>
          ) : (
            <p className="text-[11px] text-muted" data-testid="defaults-renewables-reason">{notReadyReason(allRenewables)}</p>
          )}
      </div>

      {planned.length > 0 && (
        <table className="w-full border-collapse" data-testid="defaults-rows">
          <thead>
            <tr className="text-left text-muted text-[10px] uppercase [&>th]:px-2 [&>th]:pb-1">
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
            {skipped.map(r => (
              <li key={`${r.family}:${r.component}`}>
                <b>{r.component}</b>: {r.skipped}
                <EditorLink component={r.component} family={r.family} onOpenEditor={onOpenEditor} />
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex items-center gap-2 mt-3">
        <Btn variant="primary" onClick={() => apply.mutate()} disabled={(!ticked.size && !renewCount) || apply.isPending || preview.isPending}>
          <Check size={13} /> Apply {ticked.size + renewCount} default{ticked.size + renewCount === 1 ? '' : 's'}
        </Btn>
        {!ticked.size && !renewCount && !preview.isPending && (
          <span className="text-[11px] text-muted" data-testid="defaults-apply-reason">
            {planned.length || renewables.length ? 'Tick a row to apply its default.' : 'Every component already has a series, or is skipped for the reason shown.'}
          </span>
        )}
        {apply.isError && <span className="text-red-600">{errorText(apply.error)}</span>}
      </div>
      {renewResult && (
        <ul className="mt-2 text-[11px] list-disc pl-4" data-testid="defaults-renewables-result">
          {renewResult.map(o => o.error
            ? <li key={o.technology} className="text-amber-700">{o.technology}: {o.error}</li>
            : <li key={o.technology} className="text-green-700">
                {o.technology}: {o.applied} profile{o.applied === 1 ? '' : 's'} applied. One undo (Ctrl+Z) reverts them.
              </li>)}
        </ul>
      )}
      {result && (
        <div className="mt-2 text-[11px]" data-testid="defaults-result">
          <p className="text-green-700">Applied {result.applied}. One undo (Ctrl+Z) reverts them all.</p>
          <ul className="list-disc pl-4">
            {result.results.filter(x => x.applied).map(x => (
              <li key={x.job_id} data-testid={`defaults-applied-${x.component}`}>
                {x.component}: peak {x.before ? `${fmt(x.before.peak)} → ` : ''}{fmt(x.peak)} MW
              </li>
            ))}
            {result.results.filter(x => !x.applied).map(x => {
              const row = x.component ? planned.find(r => r.component === x.component) : undefined
              return (
                <li key={x.job_id} className="text-amber-700" data-testid={`defaults-failed-${x.component ?? x.job_id}`}>
                  {x.component ?? 'A default'}: {x.message}
                  {x.component && row && (
                    <EditorLink component={x.component} family={row.family} onOpenEditor={onOpenEditor} />
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}

// A default's family in plain words (its kind follows).
const FAMILY_WORDS: Record<string, string> = {
  load: 'load archetype', availability: 'availability default', series: 'series default',
}

function EditorLink({ component, family, onOpenEditor }: {
  component: string; family: string; onOpenEditor?: OpenEditor
}) {
  if (!onOpenEditor?.families.has(family)) return null
  return (
    <button type="button" className="ml-1 text-accent hover:underline"
      onClick={() => onOpenEditor.open(component, family)}>
      Open its editor ›
    </button>
  )
}

function FragmentRow({ r, ticked, open, onTick, onOpen, onSizing }: {
  r: DefaultRow; ticked: boolean; open: boolean
  onTick: () => void; onOpen: () => void; onSizing: (v: 'mean' | 'peak') => void
}) {
  return (
    <>
      <tr className="border-t border-border align-top [&>td]:px-2 [&>td]:py-1">
        <td><input type="checkbox" aria-label={`Apply the default for ${r.component}`} checked={ticked} onChange={onTick} /></td>
        <td>
          <button type="button" className="flex items-center gap-1" onClick={onOpen} aria-expanded={open}>
            {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}{r.component}
          </button>
        </td>
        <td>
          <span className="mr-1">{FAMILY_WORDS[r.family] ?? 'default'}: {(r.kind ?? '').replace(/_/g, ' ')}</span>
          {r.assumed && <Tag tone="neutral">assumed</Tag>}
          <div className="text-[10px] text-muted" data-testid={`reason-${r.component}`}>{r.reason}</div>
          <WarningList warnings={r.warnings ?? []} />
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
