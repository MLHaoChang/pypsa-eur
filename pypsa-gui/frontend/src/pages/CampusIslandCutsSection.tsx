// The frequency cuts, in the island section (campus island plan, I7; owner Q6:
// manual, with a preview; QA: one click runs the loop).
//
// Thin like the rest of the panel:
// - the cuts the last campus run proposes are READ from `GET …/island/cuts`,
//   each with what it bounds and by how much, and its primal estimate labelled
//   as one; a cut the hub LP cannot take says why;
// - Apply starts the loop as a background job (apply, re-solve the hub, rerun
//   the campus study, up to three trials); its record is polled while it runs,
//   and the cuts, the island report and the campus state are read again when
//   it ends;
// - the trials and the applied cuts are listed; a cut can be taken back.
// The limit is stated: bridge-energy failures get no cut.
//
// W10 R3: the cuts have two places. `mode="read"` is the Control › Island & ride-through tab
// (the proposals, the trials and the applied cuts, read-only); `mode="actions"` is Grid study
// (apply the loop, take a cut back). `all` is both, as before.
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, Square, Trash2 } from 'lucide-react'
import { campusApi, errorText, type AppliedCut, type ProposedCut, type RefreshedRating } from '../api/campusElectrical'
import { Btn, Tag } from '../components/PageKit'
import { ISLAND_REPORT_KEY } from './CampusIslandSection'

export const CUTS_KEY = (name: string) => ['campusElectrical', 'islandCuts', name] as const
export const CUTS_JOB_KEY = (name: string) => ['campusElectrical', 'islandCutsJob', name] as const
export const CUTS_POLL_MS = 1000

const num = (v: number | 'inf' | null | undefined, digits = 4) =>
  v === null || v === undefined ? '' : v === 'inf' ? '∞'
    : v.toLocaleString('en-US', { maximumSignificantDigits: digits })
const cost = (v: number | null) => (v === null ? '' : v.toLocaleString('en-US', { maximumFractionDigits: 0 }))
/** One line per refreshed rating or added unit (owner: each trial lists them). */
const refreshedText = (r: RefreshedRating) =>
  'field' in r ? `${r.unit} ${r.field} ${num(r.from)} → ${num(r.to)}`
    : r.added ? `${r.unit} added` : `${r.unit} not added: ${r.reason}`
/** Scenario ids in the user's words, as the island Scenarios checkboxes name them. */
const SCENARIO_WORDS: Record<string, string> = { ups_bridged: 'UPS-bridged', seamless: 'Seamless', planned: 'Planned' }
export const scenarioWords = (id: string) =>
  SCENARIO_WORDS[id] ?? id.replace(/_/g, ' ').replace(/^./, ch => ch.toUpperCase())

const hourWords = (period: number, hour: number) => `${period}, hour ${hour}`
const EVENT_WORDS: Record<string, string> = {
  E1: 'Grid opens (islanding)', E2: 'Load step', E3: 'Unit trip',
  E4: 'Start from a dead bus', E5: 'PV loss', E6: 'Planned islanding',
}
const eventWords = (id: string) => EVENT_WORDS[id] ?? id
const KIND_WORDS: Record<string, string> = {
  inertia: 'inertia', headroom_up: 'up headroom', headroom_down: 'down headroom', droop_gain: 'droop gain',
}
const kindWords = (id: string) => KIND_WORDS[id] ?? id

const TH = 'text-left font-medium px-2'
const TD = 'px-2'

export type CutsMode = 'all' | 'read' | 'actions'

export default function CampusIslandCutsSection({ name, mode = 'all' }: { name: string; mode?: CutsMode }) {
  const read = mode !== 'actions'
  const act = mode !== 'read'
  const qc = useQueryClient()
  const cuts = useQuery({ queryKey: CUTS_KEY(name), queryFn: () => campusApi.islandCuts(name), retry: false })
  const job = useQuery({
    queryKey: CUTS_JOB_KEY(name), queryFn: () => campusApi.islandCutsStatus(name), retry: false,
    refetchInterval: q => (q.state.data?.state === 'running' ? CUTS_POLL_MS : false),
  })
  const [refusal, setRefusal] = useState<string | null>(null)
  const refresh = () => {
    qc.invalidateQueries({ queryKey: CUTS_KEY(name) })
    qc.invalidateQueries({ queryKey: ISLAND_REPORT_KEY(name) })
    qc.invalidateQueries({ queryKey: ['campusElectrical', 'state', name] })   // CAMPUS_KEY (the panel imports this file)
  }
  // The poll on which the record leaves `running` re-reads what the loop changed.
  const last = useRef<string | undefined>(undefined)
  const state = job.data?.state
  useEffect(() => {
    if (last.current === 'running' && state && state !== 'running') refresh()
    last.current = state
  }, [state])   // eslint-disable-line react-hooks/exhaustive-deps

  const apply = useMutation({
    mutationFn: () => campusApi.applyIslandCuts(name),
    onMutate: () => setRefusal(null),
    onSuccess: rec => qc.setQueryData(CUTS_JOB_KEY(name), rec),
    onError: e => setRefusal(errorText(e)),
  })
  const cancel = useMutation({ mutationFn: () => campusApi.cancelIslandCuts(name) })
  const remove = useMutation({
    mutationFn: (c: AppliedCut) => campusApi.removeIslandCut(name, { period: c.period, hour: c.hour, kind: c.kind }),
    onSuccess: d => qc.setQueryData(CUTS_KEY(name), d),
    onError: e => setRefusal(errorText(e)),
  })
  const clear = useMutation({
    mutationFn: () => campusApi.clearIslandCuts(name),
    onSuccess: d => qc.setQueryData(CUTS_KEY(name), d),
    onError: e => setRefusal(errorText(e)),
  })

  const data = cuts.data
  const isRunning = state === 'running'
  const applicable = (data?.proposed ?? []).some(c => c.applicable)

  return (
    <div className="mt-4" data-testid="cuts-section">
      {/* The Control tab's section title already says "Frequency cuts". */}
      {act && <h3 className="text-[13px] font-semibold mb-1">Frequency cuts</h3>}
      {cuts.isError && <p className="text-[12px] text-danger">{errorText(cuts.error)}</p>}
      {data && <p className="text-[11.5px] text-muted mb-2" data-testid="cuts-limit">{data.limit}</p>}
      {refusal && <p className="text-[12px] text-danger mb-2" data-testid="cuts-refusal">{refusal}</p>}

      {data && read && (
        <>
          <ProposedTable rows={data.proposed} />
          <p className="text-[11px] text-muted italic mt-1" data-testid="cuts-estimate-note">
            Each estimate is primal: the cheapest extendable unit that meets the cut, at its capital cost.
            It is not a solve. Inertia and headroom cuts are heuristic: the units' response is held at
            the current solution.
          </p>
        </>
      )}
      {data && act && (
        <>
          {!read && <p className="text-[12px] text-muted" data-testid="cuts-where">
            {data.proposed.length} cut{data.proposed.length === 1 ? '' : 's'} proposed by the last campus run.
            Review them in Results › Control › Island & ride-through.
          </p>}
          <div className="flex gap-2 items-center mt-2">
            <Btn onClick={() => apply.mutate()} disabled={!applicable || isRunning || apply.isPending}>
              <Play size={13} /> Apply cuts (up to {data.max_trials} trials)
            </Btn>
            {isRunning && (
              <Btn onClick={() => cancel.mutate()} disabled={cancel.isPending}>
                <Square size={13} /> Cancel
              </Btn>
            )}
          </div>
        </>
      )}
      {job.data && (
        <p className="text-[12px] mt-2" data-testid="cuts-job">
          {isRunning
            ? `Applying: trial ${Math.max(job.data.trials, 1)} of ${job.data.max_trials} (re-solving the hub, then the campus study)…`
            : job.data.state === 'failed'
              ? `The loop failed: ${job.data.error ?? ''}`
              : `The loop ${job.data.state === 'cancelled' ? 'was cancelled' : 'ended'}: ${job.data.stopped ?? ''}`}
        </p>
      )}

      {data && read && data.trials.length > 0 && (
        <div className="mt-3">
          <h4 className="text-[12.5px] font-semibold">Trials</h4>
          <table className="text-[12px] whitespace-nowrap" data-testid="cuts-trials">
            <thead><tr><th className={TH}>trial</th><th className={TH}>cuts added</th>
              <th className={TH}>hub objective</th><th className={TH}>campus sizes refreshed</th>
              <th className={TH}>verdict</th></tr></thead>
            <tbody>
              {data.trials.map(t => (
                <tr key={t.trial}>
                  <td className={TD}>{t.trial}</td>
                  <td className={TD}>{t.added.map(c => `${kindWords(c.kind)} ${hourWords(c.period, c.hour)} ≥ ${num(c.required)}`).join('; ')}</td>
                  <td className={TD}>{cost(t.objective)}</td>
                  <td className={TD}>{(t.refreshed ?? []).map(refreshedText).join('; ') || 'none'}</td>
                  <td className={TD}>{t.verdict}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && data.applied.length > 0 && (
        <div className="mt-3" data-testid="cuts-applied">
          <h4 className="text-[12.5px] font-semibold">Applied cuts (the hub solve reads these)</h4>
          <table className="text-[12px] whitespace-nowrap">
            <tbody>
              {data.applied.map(c => (
                <tr key={`${c.period}-${c.hour}-${c.kind}`}>
                  <td className={TD}>{kindWords(c.kind)}</td>
                  <td className={TD}>{hourWords(c.period, c.hour)}</td>
                  <td className={TD}>≥ {num(c.required)}</td>
                  {act && <td className={TD}>
                    <Btn onClick={() => remove.mutate(c)} disabled={isRunning || remove.isPending}>
                      <Trash2 size={12} /> Remove
                    </Btn>
                  </td>}
                </tr>
              ))}
            </tbody>
          </table>
          {act && <Btn onClick={() => { if (window.confirm('Take every cut back, and the trials with them?')) clear.mutate() }}
               disabled={isRunning || clear.isPending}>
            <Trash2 size={12} /> Clear all cuts
          </Btn>}
        </div>
      )}
    </div>
  )
}

function ProposedTable({ rows }: { rows: ProposedCut[] }) {
  if (rows.length === 0) {
    return <p className="text-[12px] text-muted">The last campus run proposes no cut: nothing it can fix fails.</p>
  }
  return (
    <div className="max-h-[320px] overflow-auto border border-border rounded">
      <table className="text-[12px] whitespace-nowrap" data-testid="cuts-proposed">
        <thead className="sticky top-0 bg-bg-2 z-10">
          <tr>
            {['Hour', 'Scenario', 'Event', 'Cut', 'Now', 'Needed', 'Limit reached', 'Cheapest unit', 'Added (MW)',
              'Estimated cost'].map(h => <th key={h} className={TH}>{h}</th>)}
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map(c => (
            <tr key={`${c.period}-${c.hour}-${c.scenario}-${c.kind}`} data-applicable={c.applicable ? 'yes' : 'no'}>
              <td className={TD}>{hourWords(c.period, c.hour)}</td>
              <td className={TD}>{scenarioWords(c.scenario)}</td>
              <td className={TD} title={c.event}>{eventWords(c.event)}</td>
              <td className={TD}>{kindWords(c.kind)} ({c.unit})</td>
              <td className={TD}>{num(c.current)}</td>
              <td className={TD}>{num(c.required)}</td>
              <td className={`${TD} max-w-[260px] truncate`} title={c.binding ?? ''}>{c.binding}</td>
              {c.applicable ? (
                <>
                  <td className={TD}>{c.candidate ?? '—'}</td>
                  <td className={TD}>{num(c.added_mw)}</td>
                  <td className={TD} title={c.label}>{cost(c.estimate)}</td>
                </>
              ) : (
                <td className={`${TD} text-muted`} colSpan={3}>{c.reason}</td>
              )}
              <td className={TD}>{c.heuristic && <Tag tone="warn">heuristic</Tag>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
