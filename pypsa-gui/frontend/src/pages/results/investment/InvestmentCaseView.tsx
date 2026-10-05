// Results → Investment → Investment case (IC P4 WP4.7b): run the finance case
// (progress, abort), then its results — the headline returns, the WACC gate
// chip, the incremental-vs-counterfactual statement, the cashflows by year
// and stream, the debt schedule with DSCR, the tax by layer, the completeness
// chips — the stale marker, and the xlsx export. A figure the report does not
// establish is shown as "not established", never as 0 or a blank (ADR-0001).
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { financeApi, SolverInFlightError } from '../../../api/finance'
import type { InvestmentCaseReportPayload, InvestmentCaseStudy } from '../../../api/types'
import { CompletenessChips } from '../../../components/CompletenessChips'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { blockerMessage } from '../../../utils/blockerMessage'
import { downloadCSV } from '../shared'
import {
  AVOIDED_PREFIX, cashflowPivot, cashTotals, completenessRows, missingLines, counterfactualStatement, fmtCell, fmtHeadline, gateLegs,
  HEADLINE_KEYS, headlines, label, NOT_ESTABLISHED, perYearLists, progressFraction, progressText,
  reportFlags, reportYears, sectionOf, studyFailure, studyProgress, studyStale, toTable,
  uncheckableReason, waccGate,
  type GateState, type Table,
} from './financeModel'
import { fmtAmount } from './valueFlows'

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => !!v && typeof v === 'object' && !Array.isArray(v)
const isPrim = (v: unknown) => v === null || ['number', 'string', 'boolean'].includes(typeof v)

// ── generic payload rendering ────────────────────────────────────────────

function DataTable({ table, caption, testId }: { table: Table; caption?: string; testId?: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="text-[11px] w-full" data-testid={testId}>
        {caption && <caption className="text-left text-muted">{caption}</caption>}
        <thead><tr>{table.columns.map(c => (
          <th key={c} scope="col" className={c === table.columns[0] ? 'text-left' : 'text-right'}>{label(c)}</th>
        ))}</tr></thead>
        <tbody>{table.rows.map((r, i) => (
          <tr key={i}>{table.columns.map(c => (
            <td key={c} className={c === table.columns[0] ? 'text-left' : 'text-right'}>{fmtCell(c, r[c])}</td>
          ))}</tr>
        ))}</tbody>
      </table>
    </div>
  )
}

const nameOf = (v: Obj): string | null => {
  for (const k of ['name', 'layer', 'id', 'kind']) if (typeof v[k] === 'string') return v[k] as string
  return null
}

/** Whatever a section payload holds, defensively: scalars as a list (null →
 *  "not established"), per-year lists as a table, records recursively. */
function PayloadView({ value, caption, depth = 0, omit, axis }: {
  value: unknown; caption?: string; depth?: number; omit?: Set<string>; axis?: number[] | null
}) {
  // A list of flat records is one table; a record goes through its parts
  // below, so its scalars are shown beside its per-year lists.
  const table = Array.isArray(value) ? toTable(value, axis) : null
  if (table) return <DataTable table={table} caption={caption} />
  if (Array.isArray(value)) {
    if (value.every(isObj) && depth < 4) {
      return <>{(value as Obj[]).map((v, i) => (
        <PayloadView key={i} value={v} depth={depth + 1} axis={axis}
                     caption={`${caption ? `${caption}: ` : ''}${nameOf(v) ?? i + 1}`} />
      ))}</>
    }
    return <p><span className="text-muted">{caption}: </span>
      {value.length ? value.map(v => fmtCell(caption ?? '', v)).join(', ') : 'none'}</p>
  }
  if (!isObj(value)) return <p><span className="text-muted">{caption}: </span>{fmtCell(caption ?? '', value)}</p>
  const entries = Object.entries(value).filter(([k]) => !omit?.has(k))
  const scalars = entries.filter(([, v]) => isPrim(v) || v === undefined)
  // The per-year number lists become one table (a row per year); any other
  // list of values (flags, a rate list) is written out.
  const perYear = perYearLists(Object.fromEntries(entries), axis)
  const columnar = Object.keys(perYear).length ? toTable(perYear, axis) : null
  const lists = entries.filter(([k, v]) => Array.isArray(v) && v.length > 0 && v.every(isPrim) && !(k in perYear))
  const nested = entries.filter(([, v]) => isObj(v) || (Array.isArray(v) && !(v.length > 0 && v.every(isPrim))))
  return (
    <div className="space-y-1">
      {caption && <h4 className="font-semibold">{caption}</h4>}
      {scalars.length > 0 && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-3">
          {scalars.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted">{label(k)}</dt><dd>{fmtCell(k, v)}</dd>
            </div>
          ))}
        </dl>
      )}
      {columnar && <DataTable table={columnar} />}
      {lists.map(([k, v]) => (
        <p key={k}><span className="text-muted">{label(k)}: </span>
          {(v as unknown[]).map(x => fmtCell(k, x)).join(', ')}</p>
      ))}
      {depth < 4 && nested.map(([k, v]) => (
        Array.isArray(v) && v.length === 0
          ? <p key={k}><span className="text-muted">{label(k)}: </span>none</p>
          : <PayloadView key={k} value={v} caption={label(k)} depth={depth + 1} axis={axis} />
      ))}
    </div>
  )
}

/** A report section: its status and note, then its payload when it has one. */
function SectionBlock({ report, name, title, testId, omit }: {
  report: InvestmentCaseReportPayload; name: string; title: string; testId: string; omit?: Set<string>
}) {
  const s = sectionOf(report, name)
  const status = s?.status ?? report.completeness?.[name] ?? null
  return (
    <section className="space-y-1" data-testid={testId} data-status={status ?? 'absent'}>
      <h3 className="text-[11px] font-semibold">{title}</h3>
      {status !== 'ok' && (
        <p className="text-muted">
          {status === 'skipped' ? 'Skipped' : 'Not established'}{s?.note ? `: ${s.note}` : '.'}</p>
      )}
      {s?.payload && Object.keys(s.payload).length > 0 ? (
        <PayloadView value={s.payload} omit={omit} axis={reportYears(report)} />
      ) : status === 'ok' && (
        <p className="text-muted">The report serves no detail for this section.</p>
      )}
    </section>
  )
}

// ── the parts of the results ─────────────────────────────────────────────

export function WaccGateChip({ state, legs }: { state: GateState; legs: Array<[string, string]> }) {
  const text = state === 'consistent' ? 'WACC gate: consistent with the LP discount rate'
    : state === 'differs' ? 'WACC gate: differs from the LP discount rate'
      : 'WACC gate: not established'
  const tone = state === 'consistent' ? 'text-success border-success/40'
    : state === 'differs' ? 'text-warn border-warn/40' : 'text-muted border-border'
  return (
    <span role="status" data-testid="ic-wacc-gate" data-state={state}
          title={legs.map(([k, v]) => `${k}: ${v}`).join('; ') || undefined}
          className={`inline-block rounded border px-2 py-0.5 text-[11px] ${tone}`}>
      {text}{legs.length > 0 && <span className="text-muted"> ({legs.map(([k, v]) => `${k} ${v}`).join(', ')})</span>}
    </span>
  )
}

function Headlines({ report }: { report: InvestmentCaseReportPayload }) {
  const rows = headlines(report)
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5" data-testid="ic-headlines">
      {rows.map(h => (
        <div key={h.id} className="contents">
          <dt className="text-muted">{h.label}</dt>
          <dd data-testid={`ic-headline-${h.id}`} data-established={h.value !== null}
              className={h.value === null ? 'text-muted' : ''} title={h.note ?? undefined}>
            {fmtHeadline(h.kind, h.value)}{h.value === null && h.note ? ` (${h.note})` : ''}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function Counterfactual({ report }: { report: InvestmentCaseReportPayload }) {
  const stated = counterfactualStatement(report)
  const flags = reportFlags(report, 'project')
  return (
    <div className="space-y-1" data-testid="ic-counterfactual">
      <h3 className="text-[11px] font-semibold">Incremental against the counterfactual</h3>
      <p>
        The returns, NPV, payback and the solved PPA price are on the incremental cash: the site with
        the owner's assets against the same site, tariff and connection without them. The lifecycle-cost
        NPV is on the owner's total cash with the investment, not incremental.
      </p>
      <p className={stated ? '' : 'text-muted'} data-testid="ic-counterfactual-statement">
        {stated ?? 'The report states no counterfactual for this case.'}</p>
      {flags.length > 0 && (
        <ul className="text-muted pl-3 list-disc" data-testid="ic-project-flags">
          {flags.slice(0, 20).map(f => <li key={f}>{f}</li>)}
          {flags.length > 20 && <li>+{flags.length - 20} more</li>}
        </ul>
      )}
    </div>
  )
}

function Cashflows({ report }: { report: InvestmentCaseReportPayload }) {
  const lines = report.cashflow_lines
  const projectPayload = sectionOf(report, 'project')?.payload as Record<string, unknown> | undefined
  const pivot = cashflowPivot(lines, missingLines(report),
                              typeof projectPayload?.owner === 'string' ? projectPayload.owner : null)
  const { totals, reason: incomplete, mismatch } = cashTotals(report, pivot)
  const hasAvoided = pivot.columns.some(c => c.includes(AVOIDED_PREFIX))
  if (!pivot.years.length) {
    return (
      <section data-testid="ic-cashflows-unavailable" className="space-y-1">
        <h3 className="text-[11px] font-semibold">Cashflows by year and stream</h3>
        <p className="text-muted">{Array.isArray(lines) ? 'No cashflow lines: ' : ''}{NOT_ESTABLISHED}.</p>
      </section>
    )
  }
  const csv = () => downloadCSV('investment_case_cashflows.csv',
    ['year', 'participant', 'counterparty', 'value_stream', 'asset', 'tariff_item', 'amount', 'source',
     'source_id', 'contract_id'],
    (lines ?? []).map(l => [l.year, l.participant, l.counterparty, l.value_stream, l.asset ?? '',
                            l.tariff_item ?? '', l.amount, l.provenance?.source ?? '',
                            l.provenance?.source_id ?? '', l.provenance?.contract_id ?? '']))
  return (
    <section className="space-y-1">
      <div className="flex justify-between items-center">
        <h3 className="text-[11px] font-semibold">Cashflows by year and stream</h3>
        <button type="button" className="text-[11px] underline" onClick={csv}>Export the cashflows (CSV)</button>
      </div>
      <div className="overflow-x-auto">
        <table className="text-[11px] w-full" data-testid="ic-cashflows">
          <caption className="text-left text-muted">
            + cash in, − cash out; a dash is a stream with no line that year; Total = the post-tax equity cash
            {hasAvoided && <span data-testid="ic-cashflows-avoided">; “{AVOIDED_PREFIX}” columns are the
              supply cost the site would pay without the investment, negated (returns are on the incremental
              cash) — avoided cost, not money received</span>}
            {incomplete && <span className="text-warn" data-testid="ic-cashflows-incomplete">
              {' '}— incomplete: {incomplete}; those totals are {NOT_ESTABLISHED}</span>}
            {mismatch.length > 0 && <span className="text-warn" data-testid="ic-cashflows-mismatch">
              {' '}— the lines do not sum to the equity cash in {mismatch.join(', ')}</span>}</caption>
          <thead><tr>
            <th scope="col" className="text-left">Year</th>
            {pivot.columns.map(c => <th key={c} scope="col" className="text-right">{label(c)}</th>)}
            <th scope="col" className="text-right">Total</th>
          </tr></thead>
          <tbody>{pivot.years.map(y => (
            <tr key={y} data-testid={`ic-cashflow-${y}`}>
              <th scope="row" className="text-left font-normal">{y}</th>
              {pivot.columns.map(c => {
                const v = pivot.cells[y]?.[c]
                if (pivot.unknown[y]?.includes(c)) {
                  return <td key={c} className="text-right text-warn" data-established="false"
                             title="a line in this cell is not established">{NOT_ESTABLISHED}</td>
                }
                return <td key={c} className="text-right" title={v === undefined ? 'no line' : undefined}>
                  {v === undefined ? '–' : fmtAmount(v)}</td>
              })}
              <td className="text-right" data-established={totals[y] !== null}>
                {totals[y] === null ? NOT_ESTABLISHED : fmtAmount(totals[y] as number)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </section>
  )
}

function Report({ report }: { report: InvestmentCaseReportPayload }) {
  const rows = completenessRows(report)
  return (
    <div className="space-y-4" data-testid="ic-report">
      <CompletenessChips rows={rows} testId="ic-case-completeness" itemTestIdPrefix="ic-case-section-"
                         label="Investment case report completeness" />
      <div className="flex flex-wrap items-center gap-2">
        <WaccGateChip state={waccGate(report)} legs={gateLegs(report)} />
        <a href={financeApi.exportXlsxUrl()} download data-testid="ic-export-xlsx"
           className="text-[11px] underline">Export (xlsx)</a>
      </div>
      <section className="space-y-1">
        <h3 className="text-[11px] font-semibold">Headline returns</h3>
        <Headlines report={report} />
      </section>
      <Counterfactual report={report} />
      <Cashflows report={report} />
      <SectionBlock report={report} name="debt" title="Debt schedule and DSCR" testId="ic-debt" />
      <SectionBlock report={report} name="tax" title="Tax by layer" testId="ic-tax" />
      <details>
        <summary className="text-[11px]">More from the project section</summary>
        <SectionBlock report={report} name="project" title="Project" testId="ic-project"
                      omit={new Set([...HEADLINE_KEYS, 'flags', 'counterfactual_statement'])} />
      </details>
      {(report.case_id || report.assumptions_hash) && (
        <p className="text-[10px] text-muted">
          Case {report.case_id ?? NOT_ESTABLISHED}; assumptions {String(report.assumptions_hash ?? '').slice(0, 12)}
          {report.packs && Object.keys(report.packs).length > 0
            ? `; packs ${Object.entries(report.packs).map(([k, v]) => `${k} ${v}`).join(', ')}` : ''}</p>
      )}
    </div>
  )
}

// ── the tab ──────────────────────────────────────────────────────────────

const refetchWhileRunning = (q: { state: { data: unknown } }): number | false =>
  (q.state.data as InvestmentCaseStudy | null)?.status === 'running' ? 2000 : false

export default function InvestmentCaseView() {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const studyKey = nk(project, 'results', 'investment_case')
  const reportKey = nk(project, 'results', 'investment_case', 'report')
  const study = useQuery({ queryKey: studyKey, queryFn: () => financeApi.getInvestmentCase(),
                           refetchInterval: refetchWhileRunning })
  const s = study.data ?? null
  const running = s?.status === 'running'
  const report = useQuery({ queryKey: reportKey, queryFn: () => financeApi.getReport(), enabled: !running })
  const [blocked, setBlocked] = useState<string | null>(null)

  // A run that ends while mounted brings its report in.
  const prev = useRef<string | null>(null)
  useEffect(() => {
    const before = prev.current
    prev.current = s?.status ?? null
    if (before === 'running' && s?.status !== 'running') void qc.invalidateQueries({ queryKey: reportKey })
  }, [s?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = useMutation({
    mutationFn: () => financeApi.startInvestmentCase(),
    onMutate: () => setBlocked(null),
    onSuccess: () => void qc.invalidateQueries({ queryKey: studyKey }),
    onError: (e: unknown) => setBlocked(e instanceof SolverInFlightError
      ? 'a solve is running; run the investment case after it.' : blockerMessage(e)),
  })
  const abort = useMutation({
    mutationFn: () => financeApi.abortInvestmentCase(),
    onSuccess: () => void qc.invalidateQueries({ queryKey: studyKey }),
    onError: (e: unknown) => setBlocked(blockerMessage(e)),
  })

  // While a run is under way, the previous report describes other inputs.
  const r = running ? null : (report.data ?? null)
  const staleness = studyStale(s)
  const stale = !running && (staleness.stale || r?.stale === true)
  const fraction = progressFraction(studyProgress(s))
  const progress = progressText(studyProgress(s))
  const failure = studyFailure(s)

  return (
    <div className="space-y-3 text-[11px]" data-testid="investment-case">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" data-testid="ic-run" disabled={running || run.isPending}
                className="px-2 py-1 rounded bg-accent text-on-accent disabled:opacity-50"
                onClick={() => run.mutate()}>
          {running ? 'Running the investment case…' : 'Run the investment case'}</button>
        {running && (
          <button type="button" data-testid="ic-abort" disabled={abort.isPending}
                  className="px-2 py-1 rounded border border-border hover:border-danger hover:text-danger"
                  onClick={() => abort.mutate()}>Abort</button>
        )}
        {running && (
          <span role="status" data-testid="ic-progress" className="flex items-center gap-1 text-muted">
            {fraction !== null && <progress aria-label="Investment case progress" max={1} value={fraction} />}
            {progress ?? 'Running…'}
          </span>
        )}
        {s?.status === 'aborted' && (
          <span className="text-warn" data-testid="ic-aborted">
            Stopped: the run was aborted; the report below, if any, is from an earlier run.</span>
        )}
        {failure && <span className="text-danger" data-testid="ic-error">{failure}</span>}
        {blocked && <span className="text-warn" data-testid="ic-blocked">Blocked: {blocked}</span>}
      </div>
      {stale && (
        <p role="alert" className="text-warn" data-testid="ic-stale">
          {uncheckableReason(staleness.reason)
            ? <>Unconfirmed: {uncheckableReason(staleness.reason)}.</>
            : <>Stale: {staleness.changed.length
                ? `${staleness.changed.map(label).join(', ')} changed`
                : 'the finance inputs, the value flows, the commercial config, the solver config, the dispatch or a pack changed'}
              {' '}since this report was computed. Run the investment case again for current figures.</>}</p>
      )}
      {report.isError && !running && (
        <p className="text-warn" data-testid="ic-report-error">The report could not be loaded.</p>
      )}
      {!running && !report.isError && report.isSuccess && !r && (
        <p className="text-muted" data-testid="ic-not-run">
          No investment case has been run yet. State the finance inputs, then run it.</p>
      )}
      {r && <Report report={r} />}
    </div>
  )
}
