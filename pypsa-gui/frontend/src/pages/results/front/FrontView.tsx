// The multi-objective front, as both results surfaces show it (DC review 01,
// UX 28): a key for every encoding, the cost-vs-reliability chart coloured by
// CO2, one table that agrees with it, and one "Use this design" per point.
// The Expert panel (FrontPanel) and the Guided section-10 tile (FrontTile)
// both render this view from the same records (Guided/Expert parity).
import { Fragment, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CartesianGrid, ErrorBar, ResponsiveContainer, Scatter, ScatterChart, Tooltip as RTooltip, XAxis, YAxis,
} from 'recharts'
import { frontApi, type FrontRecord, type FrontSource } from '../../../api/front'
import { networkApi } from '../../../api/network'
import ResultState, { StateAction } from '../../../components/ResultState'
import AssetName from '../../../components/AssetName'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { CHART_AXIS, CHART_GRID } from '../shared'
import {
  applyRefusal, ciText, co2Scale, CO2_HIGH, CO2_LOW, costScale, fmtNum, frontNotices, frontRows, frontUnits,
  frontWords, niceTicks, OUT_OF_DATE_TEXT, pickSource, SOURCE_WORDS, type FrontRow, type Refusal,
} from './frontModel'

export const frontKeys = (project: string | null) => ({
  front: nk(project, 'results', 'front'),
  eh: nk(project, 'results', 'eh_front'),
})

/** Both records, polled while the Expert study runs. */
export function useFrontData() {
  const project = useUIStore(s => s.currentProject)
  const keys = frontKeys(project)
  const front = useQuery({
    queryKey: keys.front,
    queryFn: frontApi.get,
    enabled: !!project,
    refetchInterval: q => ((q.state.data as FrontRecord | null | undefined)?.status === 'running' ? 2000 : false),
  })
  const eh = useQuery({ queryKey: keys.eh, queryFn: frontApi.getEh, enabled: !!project })
  return {
    front: front.data ?? null,
    eh: eh.data ?? null,
    pending: front.isPending || eh.isPending,
  }
}

const muted = 'text-[10px] text-muted'

/** The key: one entry per encoding the chart uses (UX 28). */
function FrontKey({ rows, unitsRel, showCi, testId }: { rows: FrontRow[]; unitsRel: string; showCi: boolean; testId: string }) {
  const scale = co2Scale(rows)
  const anyDominated = rows.some(r => r.onFront === false)
  const anyChosen = rows.some(r => r.chosen)
  return (
    <div data-testid={`${testId}-key`} className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-muted">
      <span className="inline-flex items-center gap-1.5" data-testid={`${testId}-key-co2`}>
        CO₂ (t/yr):
        {scale.min == null || scale.max == null ? 'not read' : scale.min === scale.max ? (
          // No CO2 spread: one colour, and the key says so.
          <>
            <span aria-hidden className="inline-block h-2 w-4 rounded-sm" style={{ background: CO2_LOW }} />
            <span>every point <span className="font-mono">{fmtNum(scale.min)}</span></span>
          </>
        ) : (
          <>
            <span className="font-mono">{fmtNum(scale.min)}</span>
            <span aria-hidden className="inline-block h-2 w-16 rounded-sm"
              style={{ background: `linear-gradient(90deg, ${CO2_LOW}, ${CO2_HIGH})` }} />
            <span className="font-mono">{fmtNum(scale.max)}</span>
          </>
        )}
      </span>
      <span className="inline-flex items-center gap-1">
        <svg width="10" height="10" aria-hidden><circle cx="5" cy="5" r="4" fill={CO2_LOW} /></svg>
        filled: on the front
      </span>
      <span className="inline-flex items-center gap-1" data-testid={`${testId}-key-hollow`}>
        <svg width="10" height="10" aria-hidden><circle cx="5" cy="5" r="3.5" fill="none" stroke="var(--color-muted)" strokeWidth="1.5" /></svg>
        hollow: dominated, another point is no worse on cost, reliability and CO₂, and better on one
        {anyDominated ? '' : ' (none here)'}
      </span>
      {showCi && (
        <span className="inline-flex items-center gap-1" data-testid={`${testId}-key-ci`}>
          <svg width="14" height="10" aria-hidden><path d="M1 2v6M1 5h12M13 2v6" stroke="var(--color-muted)" fill="none" /></svg>
          whiskers: 95 % confidence interval (CI) of the EUE, {unitsRel}
        </span>
      )}
      {anyChosen && (
        <span className="inline-flex items-center gap-1" data-testid={`${testId}-key-chosen`}>
          <svg width="12" height="12" aria-hidden><circle cx="6" cy="6" r="5" fill="none" stroke="var(--color-text)" strokeWidth="1.5" /></svg>
          ringed: the design you chose
        </span>
      )}
    </div>
  )
}

interface ChartDatum { x: number; y: number; err?: [number, number]; row: FrontRow; colour: string }

function PointShape(props: { cx?: number; cy?: number; payload?: ChartDatum }) {
  const { cx, cy, payload } = props
  if (cx == null || cy == null || !payload) return <g />
  const hollow = payload.row.onFront === false
  return (
    <g>
      {payload.row.chosen && <circle cx={cx} cy={cy} r={8} fill="none" stroke="var(--color-text)" strokeWidth={1.5} />}
      <circle cx={cx} cy={cy} r={4.5}
        fill={hollow ? 'none' : payload.colour} stroke={payload.colour} strokeWidth={hollow ? 1.75 : 1} />
    </g>
  )
}

function FrontChart({ rows, relHeader, relUnit, testId }: { rows: FrontRow[]; relHeader: string; relUnit: string; testId: string }) {
  const scale = co2Scale(rows)
  const cost = costScale(rows)
  const data: ChartDatum[] = rows
    .filter(r => r.ok && r.rel != null && r.cost != null)
    .map(r => ({
      x: r.rel as number, y: r.cost as number, row: r, colour: scale.colour(r.co2),
      err: r.relCi ? [Math.max(0, (r.rel as number) - r.relCi[0]), Math.max(0, r.relCi[1] - (r.rel as number))] : undefined,
    }))
  if (data.length === 0) return null
  // The axes hold every whisker, not only the points (Recharts fits the
  // points), and end on round ticks. Fitted, not zero-based: the spread on a
  // front is a few per cent of the cost (FrontierPanel's reason).
  const xs = data.flatMap(d => [d.x - (d.err?.[0] ?? 0), d.x + (d.err?.[1] ?? 0)])
  const ys = data.map(d => d.y)
  const xt = niceTicks(Math.max(0, Math.min(...xs)), Math.max(...xs))
  const yt = niceTicks(Math.min(...ys), Math.max(...ys))
  return (
    <figure data-testid={`${testId}-chart`} className="m-0">
      <figcaption className="text-[10px] font-semibold text-text">Cost ({cost.unit}) against reliability</figcaption>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 10, right: 16, bottom: 26, left: 8 }}>
            <CartesianGrid {...CHART_GRID} />
            <XAxis {...CHART_AXIS} dataKey="x" type="number" name={relHeader} domain={[xt[0], xt[xt.length - 1]]} ticks={xt}
              tickFormatter={(v: number) => fmtNum(v)}
              label={{ value: `${relHeader}, lower is more reliable`, position: 'insideBottom', offset: -16, style: { fontSize: 10, fill: 'var(--color-muted)' } }} />
            <YAxis {...CHART_AXIS} dataKey="y" type="number" name="Cost" domain={[yt[0], yt[yt.length - 1]]} ticks={yt} width={80}
              tickFormatter={cost.tick}
              label={{ value: `Cost (${cost.unit})`, angle: -90, position: 'insideLeft', offset: 0, style: { fontSize: 10, fill: 'var(--color-muted)' } }} />
            <RTooltip cursor={false} contentStyle={{ fontSize: 10 }}
              content={({ payload }) => {
                const d = payload?.[0]?.payload as ChartDatum | undefined
                if (!d) return null
                return (
                  <div className="rounded border border-border bg-bg px-2 py-1 text-[10px] text-text">
                    {d.row.label} · {cost.cell(d.row.cost)} {cost.unit} · {fmtNum(d.row.rel)} {relUnit} · {fmtNum(d.row.co2)} t/yr CO₂
                  </div>
                )
              }} />
            <Scatter data={data} shape={PointShape} isAnimationActive={false}>
              <ErrorBar dataKey="err" direction="x" width={4} stroke="var(--color-muted)" strokeWidth={1} />
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </figure>
  )
}

function FrontTable({ record, rows, onUse, useDisabled, pendingIndex, testId }: {
  record: FrontRecord
  rows: FrontRow[]
  onUse: (i: number) => void
  useDisabled: boolean
  pendingIndex: number | null
  testId: string
}) {
  const u = frontUnits(record)
  const scale = co2Scale(rows)
  const cost = costScale(rows)
  const th = 'text-left font-medium py-1 pr-2 align-bottom'
  // Numeric columns: a left gutter (cells too), so neighbouring figures
  // never touch at the Guided card's width.
  const thNum = `${th} pl-3 text-right`
  // Columns after Point, level and CO2 limit, for a failed point's one cell.
  const rest = 4 + (u.basis === 'mc' ? 1 : 0) + (record.applicable ? 1 : 0)
  return (
    <div className="overflow-x-auto">
      <table data-testid={`${testId}-table`} className="w-full text-[10px]">
        <thead className="text-muted">
          <tr>
            <th className={th}>Point</th>
            <th className={th}>{u.levelHeader}</th>
            <th className={th}>CO₂ limit (t/yr)</th>
            <th className={thNum}>Cost ({cost.unit})</th>
            <th className={thNum}>{u.relHeader} · CI</th>
            {u.basis === 'mc' && <th className={thNum}>LOLE ({u.loleUnit}) · CI</th>}
            <th className={thNum}>CO₂ (t/yr)</th>
            <th className={th}>Front</th>
            {record.applicable && <th className={th}><span className="sr-only">Use</span></th>}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (<Fragment key={r.index}>
            <tr data-testid={`${testId}-row-${r.index}`} data-chosen={r.chosen || undefined}
              className={`border-t border-border/50 align-top ${r.chosen ? 'bg-accent/5' : ''}`}>
              <td className="py-1 pr-2 whitespace-nowrap">
                <span className="inline-flex items-center gap-1">
                  {r.ok && (
                    <svg width="10" height="10" aria-hidden>
                      <circle cx="5" cy="5" r="3.5" fill={r.onFront === false ? 'none' : scale.colour(r.co2)}
                        stroke={scale.colour(r.co2)} strokeWidth="1.5" />
                    </svg>
                  )}
                  {r.label}
                  {r.chosen && <span className="ml-1 rounded-full bg-text/10 px-1.5 text-[9px] font-semibold text-text">chosen</span>}
                </span>
              </td>
              <td className="pr-2 font-mono">{r.level}</td>
              <td className="pr-2 font-mono">{r.co2Cap}</td>
              {r.ok ? (
                <>
                  <td className="pl-3 pr-2 text-right font-mono whitespace-nowrap">
                    {cost.cell(r.cost)}
                  </td>
                  <td className="pl-3 pr-2 text-right font-mono whitespace-nowrap">
                    {fmtNum(r.rel)}{r.relCi && <span className="text-muted"> · {ciText(r.relCi)}</span>}
                  </td>
                  {u.basis === 'mc' && (
                    <td className="pl-3 pr-2 text-right font-mono whitespace-nowrap">
                      {fmtNum(r.lole)}{r.loleCi && <span className="text-muted"> · {ciText(r.loleCi)}</span>}
                    </td>
                  )}
                  <td className="pl-3 pr-2 text-right font-mono whitespace-nowrap">{fmtNum(r.co2)}</td>
                  <td className="pr-2 whitespace-nowrap">{frontWords(r)}</td>
                  {record.applicable && (
                    <td className="whitespace-nowrap">
                      <button type="button" data-testid={`${testId}-use-${r.index}`}
                        disabled={useDisabled || pendingIndex != null}
                        onClick={() => onUse(r.index)}
                        className="rounded border border-border px-2 py-0.5 text-[10px] font-semibold text-text hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-40">
                        {pendingIndex === r.index ? 'Applying…' : 'Use this design'}
                      </button>
                    </td>
                  )}
                </>
              ) : (
                <td colSpan={rest} className="text-warn">{r.failure}: shown, not drawn.</td>
              )}
            </tr>
            {r.ok && (
              // Under its row: the cost split and what the point builds, in the sized-design table's asset words.
              <tr data-testid={`${testId}-built-${r.index}`} className={r.chosen ? 'bg-accent/5' : undefined}>
                <td />
                <td colSpan={2 + rest} className="pb-1 pr-2 text-muted">
                  Capex {cost.cell(r.capex)} · opex {cost.cell(r.opex)} {cost.unit} · built: {r.built.map((b, k) => (
                    <span key={b.key} className="whitespace-nowrap">
                      {k > 0 && ' · '}{b.kind} <AssetName id={b.name} /> {b.size}
                    </span>
                  ))}
                </td>
              </tr>
            )}
          </Fragment>))}
        </tbody>
      </table>
    </div>
  )
}

/** The front of one or two records, with the source switch when both exist. */
export function FrontView({ front, eh, testId, runStudy }: {
  front: FrontRecord | null
  eh: FrontRecord | null
  testId: string
  /** What "Run again" means here when the study's own figures are out of date. */
  runStudy?: { label: string; onClick: () => void }
}) {
  const project = useUIStore(s => s.currentProject)
  const requestRunSolve = useUIStore(s => s.requestRunSolve)
  const qc = useQueryClient()
  const [picked, setPicked] = useState<FrontSource | null>(null)
  const [refusal, setRefusal] = useState<(Refusal & { index: number }) | null>(null)
  const record = pickSource(front, eh, picked)
  const keys = frontKeys(project)

  const apply = useMutation({
    mutationFn: async ({ index, fresh }: { index: number; fresh: boolean }) => {
      // The revision the user last saw (the 3 s undo/info sample); a retry reads it again.
      const cached = qc.getQueryData<{ network_revision?: number }>(nk(project, 'undoInfo'))?.network_revision
      const rev = !fresh && typeof cached === 'number' ? cached : (await networkApi.undoInfo()).network_revision
      if (typeof rev !== 'number') throw new Error('no revision')
      return frontApi.apply(index, rev)
    },
    onMutate: () => setRefusal(null),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.front })
      void qc.invalidateQueries({ queryKey: nk(project, 'undoInfo') })
      void qc.invalidateQueries({ queryKey: nk(project, 'simulationStatus') })
    },
    onError: (e, v) => setRefusal({ ...applyRefusal(e), index: v.index }),
  })

  if (!record) return null
  const both = !!front && !!eh
  const res = record.result
  const rows = frontRows(record)
  const u = frontUnits(record)
  const notices = frontNotices(record)
  const edited = record.edited_since_study === true
  const appliedHere = record.applied != null

  return (
    <div className="flex flex-col gap-2" data-testid={testId} data-source={record.source}>
      {both && (
        <div role="tablist" aria-label="Which study" className="flex items-center gap-1 text-[10px]">
          {(['front', 'eh_study'] as const).map(s => (
            <button key={s} type="button" role="tab" aria-selected={record.source === s}
              data-testid={`${testId}-source-${s}`} onClick={() => setPicked(s)}
              className={`rounded border px-2 py-0.5 ${record.source === s ? 'border-text text-text font-semibold' : 'border-border text-muted hover:text-text'}`}>
              {SOURCE_WORDS[s]}
            </button>
          ))}
        </div>
      )}

      {record.status === 'running' ? (
        <ResultState state="running" reason="The front study is running: its points appear here when it finishes." />
      ) : record.status === 'failed' || !res ? (
        <p className="text-[11px] text-warn" data-testid={`${testId}-failed`}>
          The last front study failed before it finished, so it has no points to show. Run it again.
        </p>
      ) : (
        <>
          <p className={muted}>
            Each point re-optimises the whole design at one
            {res.lever === 'ens_cap' ? ' unserved-energy (ENS) target' : ' reserve margin'}
            {(res.co2_caps_t_per_yr ?? []).some(c => c != null) ? ' and one CO₂ limit' : ''}.
            {u.basis === 'mc'
              ? ' Reliability is the expected unserved energy (EUE) and the loss-of-load expectation (LOLE) of the built fleet under sampled outages, each with its 95 % confidence interval (CI).'
              : ' Reliability is the unserved energy (ENS) of the dispatch model.'}
            {' '}Cost is the annual cost without shed cost, split into capex and opex.
          </p>

          {edited && (
            <div role="status" data-testid={`${testId}-out-of-date`}
              className="flex flex-wrap items-center gap-2 rounded border border-warn/50 bg-warn/10 px-3 py-1.5 text-[11px] text-warn">
              <span>{OUT_OF_DATE_TEXT}</span>
              {appliedHere
                ? <StateAction action={{ label: 'Run the solve', onClick: requestRunSolve }} />
                : runStudy && <StateAction action={runStudy} />}
            </div>
          )}

          {notices.map(t => (
            <p key={t} data-testid={`${testId}-notice`}
              className="rounded border border-warn/40 px-2 py-1 text-[10px] text-warn">{t}</p>
          ))}

          {record.status === 'aborted' && (
            <p className="text-[10px] text-warn" data-testid={`${testId}-aborted`}>
              Stopped: these are the points solved before the study was stopped, not the whole front.
            </p>
          )}

          <FrontKey rows={rows} unitsRel={u.relUnit} showCi={rows.some(r => r.relCi)} testId={testId} />
          <div className={edited ? 'opacity-60' : undefined}>
            <FrontChart rows={rows} relHeader={u.relHeader} relUnit={u.relUnit} testId={testId} />
          </div>

          {refusal && (
            <p role="status" data-testid={`${testId}-refusal`} className="flex flex-wrap items-center gap-2 text-[11px] text-warn">
              <span>{refusal.text}</span>
              {refusal.retry && (
                <button type="button" data-testid={`${testId}-retry`}
                  onClick={() => apply.mutate({ index: refusal.index, fresh: true })}
                  className="text-[11px] font-semibold text-text underline">Try again</button>
              )}
            </p>
          )}
          {record.applicable && edited && !appliedHere && (
            <p className={muted}>The designs cannot be used until the study runs again on the edited network.</p>
          )}
          <div className={edited ? 'opacity-60' : undefined}>
            <FrontTable record={record} rows={rows} testId={testId}
              onUse={i => apply.mutate({ index: i, fresh: false })}
              useDisabled={edited} pendingIndex={apply.isPending ? (apply.variables?.index ?? null) : null} />
          </div>
          {!record.applicable && (
            <p className={muted} data-testid={`${testId}-read-only`}>
              The Energy Hub study solved these designs on a copy of the project with its template's settings,
              so they cannot be put on the network from here. Run the front study to get designs you can use.
            </p>
          )}
        </>
      )}
    </div>
  )
}
