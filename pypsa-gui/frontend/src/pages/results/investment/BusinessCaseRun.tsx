// Business case 10: the run, the KPI row and the P&L by year, under the line table
// (spec §7). Every figure is the stored report's: totals are `pnl.rows`, never a sum of
// streams (GK B F1); a missing figure reads "Not established: <reason>", never 0.
import { Fragment, useEffect, useId, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  businessCaseApi, InvestmentCaseMissing, SolverInFlightError,
  type BusinessCasePnl, type BusinessCaseReport, type BusinessCaseStudy,
} from '../../../api/businessCase'
import { formatElapsed, useElapsed } from '../../../hooks/useElapsed'
import { useUIStore } from '../../../store/uiStore'
import { ACTION } from './actionStyle'
import {
  breakEvenReason, fmtYear, hidden, KPI_ORDER, kpiText, NET_INCOME_NOTE, PNL_SECTIONS,
  rowReason, series, staleText, stageText, streamGroups, yearReason,
} from './businessCaseReport'
import { pctText, refusalWords, VOLUME_LABEL } from './businessCaseModel'
import { fmtAmount } from './valueFlows'

const words = (id: string) => id.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())

export interface BusinessCaseRunProps {
  study: BusinessCaseStudy | null
  /** The query key of `study`, refetched after a start or an abort. */
  studyKey: readonly unknown[]
  /** Why Run is off now (unsaved lines, no lines, refused lines…), or null. */
  disabledReason: string | null
  revenueLines: string[]
  breakEvenLine: string | null
  onBreakEvenLine: (name: string | null) => void
  /** The years the draft states a volume factor for (BC 15). */
  volumeFactorYears?: string[]
  /** Removes years from the draft's volume factor (marks it unsaved; Save writes it). */
  onRemoveVolumeFactorYears?: (years: number[]) => void
}

export default function BusinessCaseRun(p: BusinessCaseRunProps) {
  const qc = useQueryClient()
  const s = p.study
  const running = s?.status === 'running'
  const elapsed = useElapsed(running)
  const [blocked, setBlocked] = useState<{ text: string; ic?: boolean } | null>(null)
  const beId = useId()

  // A run that ends while mounted brings its report in (the study GET carries it).
  const prev = useRef<string | null>(null)
  useEffect(() => {
    const before = prev.current
    prev.current = s?.status ?? null
    if (before === 'running' && s?.status !== 'running') void qc.invalidateQueries({ queryKey: p.studyKey })
  }, [s?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = useMutation({
    mutationFn: () => businessCaseApi.startRun(),
    onMutate: () => setBlocked(null),
    onSuccess: () => void qc.invalidateQueries({ queryKey: p.studyKey }),
    onError: (e: unknown) => setBlocked(
      e instanceof InvestmentCaseMissing
        ? { text: 'the business case needs a current investment case: run it first.', ic: true }
        : e instanceof SolverInFlightError
          ? { text: 'a solve is running; run the business case after it.' }
          : { text: (e as Error)?.message || 'the run could not start.' }),
  })
  const abort = useMutation({
    mutationFn: () => businessCaseApi.abortRun(),
    onSuccess: () => void qc.invalidateQueries({ queryKey: p.studyKey }),
    onError: (e: unknown) => setBlocked({ text: (e as Error)?.message || 'the abort did not go through.' }),
  })

  const off = running ? null : p.disabledReason
  const report = running ? null : (s?.report ?? null)
  const stale = running ? null : staleText(s)
  // A run refused on the volume factor reads its words, never the kind (BC 15).
  const refusal = (s?.refusal ?? null) as unknown as { refusal?: string; field?: string } | null
  const vfRefused = s?.status === 'refused' && refusal?.refusal?.startsWith('volume_factor_')
  const failure = vfRefused ? `Refused: ${refusalWords(refusal!.refusal!, refusal!.field)}`
    : s?.status === 'failed' || s?.status === 'refused'
    ? `${s.status === 'refused' ? 'Refused' : 'Failed'}: ${s.error || words(s.error_code ?? 'unknown')}`
    : null

  return (
    <section aria-labelledby="bc-run-h" className="space-y-2 text-[11px]" data-testid="bc-run">
      <h3 id="bc-run-h" className="font-semibold">Business case</h3>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={beId} className="text-muted">Break-even on</label>
        <select id={beId} data-testid="bc-break-even-line" className="border border-border rounded px-1 py-0.5 bg-bg"
                value={p.breakEvenLine ?? ''} disabled={running || p.revenueLines.length === 0}
                onChange={e => p.onBreakEvenLine(e.target.value || null)}>
          <option value="">No break-even</option>
          {p.revenueLines.map(n => <option key={n} value={n}>{n}</option>)}
        </select>
        {p.revenueLines.length === 0 && <span className="text-muted">Add a revenue line to choose one.</span>}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" data-testid="bc-run-button" disabled={running || run.isPending || off !== null}
                aria-describedby={off ? 'bc-run-off' : undefined}
                className="px-2 py-1 rounded bg-accent text-on-accent disabled:opacity-50"
                onClick={() => run.mutate()}>
          {running ? 'Running the business case…' : 'Run the business case'}</button>
        {off && <span id="bc-run-off" className="text-muted" data-testid="bc-run-off">{off}</span>}
        {running && (
          <button type="button" data-testid="bc-abort" disabled={abort.isPending} className={ACTION}
                  onClick={() => abort.mutate()}>Abort</button>
        )}
        {running && <span role="status" data-testid="bc-stage" className="text-muted">{stageText(s)}</span>}
        {running && <span className="text-muted tabular-nums" data-testid="bc-clock">{formatElapsed(elapsed)}</span>}
        {s?.status === 'aborted' && (
          <span className="text-warn" data-testid="bc-aborted">
            Stopped: the run was aborted; the report below, if any, is from an earlier run.</span>
        )}
        {failure && <span className="text-danger" data-testid="bc-failure">{failure}</span>}
        {blocked && (
          <span className="text-warn" data-testid="bc-blocked">Blocked: {blocked.text}
            {blocked.ic && <>{' '}<button type="button" className="underline" data-testid="bc-open-ic"
              onClick={() => useUIStore.getState().requestResultsTab('investment', 'case')}>
              Investment case ›</button></>}</span>
        )}
      </div>
      {stale && <p role="alert" className="text-warn" data-testid="bc-out-of-date">{stale}</p>}
      {report && <ReportView report={report} breakEvenLine={p.breakEvenLine}
                             draftYears={p.volumeFactorYears ?? []} onRemoveYears={p.onRemoveVolumeFactorYears} />}
      {/* One reason at a time (UX): "not run yet" only when nothing else is said. */}
      {!report && !running && s === null && off === null && !blocked && !run.isPending && (
        <p className="text-muted" data-testid="bc-not-run">Not run yet: save the lines, then run the business case.</p>
      )}
    </section>
  )
}

function ReportView({ report, breakEvenLine, draftYears, onRemoveYears }: {
  report: BusinessCaseReport; breakEvenLine: string | null
  draftYears: string[]; onRemoveYears?: (years: number[]) => void
}) {
  const ccy = report.currency ?? ''
  const ignored = report.pnl?.volume_factor_ignored ?? []
  const stillStated = ignored.filter(y => draftYears.includes(String(y)))
  return (
    <div className="space-y-3" data-testid="bc-report">
      {report.basis && <p className="text-muted" data-testid="bc-basis">Basis: {report.basis.label}{ccy && ` · ${ccy}`}</p>}
      {report.pnl?.volume_factor && (
        <p data-testid="bc-kpi-volume-factor">
          <span className="rounded px-1 bg-warn/15 text-warn">With volume factor (lease-up)</span>{' '}
          These figures differ from the Investment case because of the volume factor.</p>
      )}
      <KpiRow report={report} />
      <BreakEven report={report} line={breakEvenLine} />
      {ignored.length > 0 && (
        <p className="text-warn" data-testid="bc-volume-factor-ignored">
          Ignored, outside the operating years: {ignored.join(', ')}{' '}
          {stillStated.length > 0 && onRemoveYears
            ? <button type="button" className={ACTION} onClick={() => onRemoveYears(stillStated)}
                      aria-label={`Remove the volume factor of ${stillStated.join(', ')}`}>Remove</button>
            : <span className="text-muted">(removed from the unsaved lines: save, then run again)</span>}</p>
      )}
      {report.pnl
        ? <PnlTable pnl={report.pnl} />
        : <p className="text-muted" data-testid="bc-no-pnl">Not established: this report has no P&amp;L.</p>}
    </div>
  )
}

function KpiRow({ report }: { report: BusinessCaseReport }) {
  const k = report.kpis ?? {}
  return (
    <dl className="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-2" data-testid="bc-kpis">
      {KPI_ORDER.map(spec => {
        const kpi = k[spec.key]
        return (
          <div key={spec.key} className="border border-border rounded px-2 py-1"
               data-testid={`bc-kpi-${spec.key}`} data-status={kpi?.status ?? 'missing'}>
            <dt className="text-muted">{spec.label}</dt>
            <dd className={kpi?.status === 'ok' ? 'font-semibold tabular-nums' : 'text-muted'}>{kpiText(kpi, spec.fmt)}</dd>
          </div>
        )
      })}
    </dl>
  )
}

function BreakEven({ report, line }: { report: BusinessCaseReport; line: string | null }) {
  const be = report.break_even
  if (!be) return null
  const text = be.status
    ? `Not established: ${breakEvenReason(be.status)}`
    : be.price != null
      ? `${fmtAmount(be.price)} ${report.currency ?? ''} per unit${be.money_year ? ` (${be.money_year} money)` : ''}`
        + (be.price_scale != null ? `, ${(be.price_scale * 100).toFixed(1)} % of the entered price` : '')
      : 'Not established: no price served'
  return (
    <p data-testid="bc-break-even">
      <span className="text-muted">Break-even price{line ? ` of ${line}` : ''} (project NPV after tax = 0 at the WACC): </span>
      {text}</p>
  )
}

/** The report's lowercase note as a sentence. */
const sentence = (t: string) => t.replace(/^./, c => c.toUpperCase()).replace(/([^.])$/, '$1.')

/** Pinned to the scroll box's left edge, so a heading stays readable while the years scroll. */
const PIN = 'sticky left-0 inline-block'

/** Why a whole row is missing: under its label in the pinned column, so it stays in view. */
function Reason({ text }: { text: string }) {
  return <span className="block font-normal text-muted whitespace-normal max-w-[18rem]" data-testid="bc-row-reason">{text}</span>
}

/** The years of a missing row: "n/a" once, the reason being in the row header. */
function Gap({ span }: { span: number }) {
  return <td colSpan={span} className="text-muted px-1"><span aria-hidden="true">n/a</span></td>
}

function PnlTable({ pnl }: { pnl: BusinessCasePnl }) {
  const years = pnl.years
  const groups = streamGroups(pnl)
  const head = 'sticky left-0 bg-bg text-left pr-2 font-normal whitespace-nowrap'
  const cells = (key: string, v: (number | null)[] | null, pct = false) => years.map((y, i) => {
    const text = fmtYear(v?.[i] ?? null, pct)
    if (text !== null) return <td key={y} className="text-right tabular-nums px-1 whitespace-nowrap">{text}</td>
    const why = `n/a: ${yearReason(key)}`
    return (
      <td key={y} className="text-right px-1 whitespace-nowrap text-muted" title={why}>
        <span aria-hidden="true">n/a</span><span className="sr-only">{why}</span></td>
    )
  })
  return (
    <div className="overflow-x-auto" data-testid="bc-pnl">
      <table className="text-[11px] border-collapse">
        <caption className="text-left font-semibold">P&amp;L by year ({years[0]}–{years[years.length - 1]})</caption>
        <thead>
          <tr><th scope="col" className={head}>Line</th>
            {years.map(y => <th key={y} scope="col" className="text-right px-1 font-normal text-muted">{y}</th>)}</tr>
        </thead>
        <tbody>
          <tr><th scope="rowgroup" colSpan={years.length + 1} className="text-left pt-2 font-semibold">
            <span className={PIN}>Streams</span></th></tr>
          {!pnl.stream_source && groups.length > 0 && (
            <tr><td colSpan={years.length + 1} className="text-muted" data-testid="bc-streams-other">
              <span className={PIN}>This report predates the stream groups: its streams are listed under Other. Run again to group them.</span></td></tr>
          )}
          {groups.map(g => (
            <Fragment key={g.id}>
              <tr data-testid={`bc-group-${g.id}`}>
                <th scope="rowgroup" colSpan={years.length + 1} className="text-left text-muted"><span className={PIN + ' pl-1'}>{g.title}</span></th></tr>
              {g.streams.map(n => {
                const v = pnl.streams[n]
                return (
                  <tr key={n} data-testid={`bc-stream-${n}`}>
                    <th scope="row" className={head + ' pl-3'}>{words(n.replace(/^bc_/, ''))}
                      {!v && <Reason text={rowReason(`stream:${n}`, pnl)} />}</th>
                    {v ? cells(n, v) : <Gap span={years.length} />}
                  </tr>
                )
              })}
            </Fragment>
          ))}
          {PNL_SECTIONS.map((sec, si) => (
            <Fragment key={sec.title}>
              <tr><th scope="rowgroup" colSpan={years.length + 1} className="text-left pt-2 font-semibold">
                <span className={PIN}>{sec.title}
                  {sec.note && <span className="font-normal text-muted"> · {sec.note}</span>}</span></th></tr>
              {si === 0 && pnl.volume_factor && (
                <tr data-testid="bc-row-volume_factor">
                  <th scope="row" className={head + ' pl-3'}>{VOLUME_LABEL}
                    {pnl.volume_factor_note && (
                      <span className="block font-normal text-muted whitespace-normal max-w-[18rem]"
                            data-testid="bc-volume-factor-note">{sentence(pnl.volume_factor_note)}</span>
                    )}</th>
                  {years.map(y => (
                    <td key={y} className="text-right tabular-nums px-1 whitespace-nowrap">
                      {pctText(pnl.volume_factor![String(y)] ?? 1)}</td>
                  ))}
                </tr>
              )}
              {sec.rows.map(spec => {
                const v = series(pnl, spec.key)
                if (hidden(spec, v)) return null
                return (
                  <tr key={spec.key} data-testid={`bc-row-${spec.key}`}>
                    <th scope="row" className={head + (spec.total ? ' font-semibold' : ' pl-3')}>{spec.label}
                      {spec.key === 'net_income' && (
                        <span className="block font-normal text-muted" data-testid="bc-net-income-note">{NET_INCOME_NOTE}</span>
                      )}
                      {!v && <Reason text={rowReason(spec.key, pnl)} />}</th>
                    {v ? cells(spec.key, v, spec.pct) : <Gap span={years.length} />}
                  </tr>
                )
              })}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}
