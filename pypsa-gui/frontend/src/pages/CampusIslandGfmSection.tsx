// Recommended grid-forming settings, in the island section (E2E demo plan
// 2026-10-14 §7.2, GFM-b; owner Q7(b)).
//
// Thin like the rest of the panel:
// - the recommendation is READ from `GET …/island/gfm-settings` with its state
//   (not run, ready, stale) and labelled heuristic screening;
// - Recommend queues gridspine's search as a campus job: the panel's busy
//   banner follows it, and the recommendation is read again when it ends;
// - a recommended result can be applied (the island data and the campus
//   file); it then says the hub must be re-solved, with the Solve button
//   (owner decision 2026-10-15: no stale marker on the hub);
// - when no setting within the bounds passes, the capacity it would take is
//   listed instead: capacity is needed, settings do not fix it.
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Play } from 'lucide-react'
import {
  campusApi, errorText, type GfmRecommendation, type GfmStatus, type GfmUnitSettings,
} from '../api/campusElectrical'
import { Btn, Tag } from '../components/PageKit'
import { useUIStore } from '../store/uiStore'
import { ISLAND_ROWS_OFF_NOTE } from './hubDesign/chapters'

export const GFM_KEY = (name: string) => ['campusElectrical', 'gfmSettings', name] as const
const STATE_KEY = (name: string) => ['campusElectrical', 'state', name] as const   // CAMPUS_KEY (the panel imports this file)

const STATUS_TEXT: Record<GfmStatus, string> = {
  recommended: 'Settings within the bounds make every case pass with margin.',
  unchanged: 'The current settings already pass with margin: nothing to change.',
  not_achievable: 'No setting within the bounds passes: capacity is needed (see below), settings alone do not fix it.',
  incomplete: 'The search stopped at its simulation cap before it could decide: recommend again.',
}
const FIELDS: [keyof GfmUnitSettings, string][] = [
  ['droop_pct', 'droop %'], ['tau_f_s', 'τ_f s'], ['H_v_s', 'H_v s'], ['i_max_pu', 'i_max pu'],
  ['H_equivalent_s', 'H equivalent s'],
]
const num = (v: number | null | undefined) =>
  v === null || v === undefined ? '' : v.toLocaleString('en-US', { maximumSignificantDigits: 3 })
const TH = 'text-left font-medium px-2'
const TD = 'px-2'

export default function CampusIslandGfmSection({ name }: { name: string }) {
  const qc = useQueryClient()
  const requestRunSolve = useUIStore(s => s.requestRunSolve)
  const gfm = useQuery({ queryKey: GFM_KEY(name), queryFn: () => campusApi.gfmSettings(name), retry: false })
  // The panel polls the campus state while a job runs; its run_job is this search when its stage says so.
  const campus = useQuery({ queryKey: STATE_KEY(name), queryFn: () => campusApi.state(name), retry: false })
  const job = campus.data?.run_job
  const searching = !!job && job.stage === 'gfm_settings' && (job.status === 'queued' || job.status === 'running')
  const busy = !!job && (job.status === 'queued' || job.status === 'running')
  const [allowIMax, setAllowIMax] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  // The poll on which the search ends re-reads the recommendation.
  const was = useRef(false)
  useEffect(() => {
    if (was.current && !searching) qc.invalidateQueries({ queryKey: GFM_KEY(name) })
    was.current = searching
  }, [searching])   // eslint-disable-line react-hooks/exhaustive-deps

  const recommend = useMutation({
    mutationFn: () => campusApi.recommendGfmSettings(name, allowIMax),
    onMutate: () => setRefusal(null),
    onSuccess: () => qc.invalidateQueries({ queryKey: STATE_KEY(name) }),
    onError: e => setRefusal(errorText(e)),
  })
  const apply = useMutation({
    mutationFn: () => campusApi.applyGfmSettings(name),
    onMutate: () => setRefusal(null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: GFM_KEY(name) })
      qc.invalidateQueries({ queryKey: STATE_KEY(name) })
      qc.invalidateQueries({ queryKey: ['campusElectrical', 'island', name] })
    },
    onError: e => setRefusal(errorText(e)),
  })

  const data = gfm.data
  const rec = data?.recommendation ?? null
  const applied = data?.applied ?? rec?.applied
  // the backend refuses a second apply of the same recommendation (409)
  const canApply = data?.state === 'ready' && rec?.status === 'recommended'

  return (
    <div className="mt-4" data-testid="gfm-section">
      <h3 className="text-[13px] font-semibold mb-1">Grid-forming settings</h3>
      {gfm.isError && <p className="text-[12px] text-danger">{errorText(gfm.error)}</p>}
      {data && data.state !== 'ready' && (
        <p className="text-[12px] text-muted mb-1" data-testid="gfm-reason">
          {data.state === 'stale' && <><Tag tone="warn">stale</Tag>{' '}</>}{data.reason}
        </p>
      )}
      {refusal && <p className="text-[12px] text-danger mb-2" data-testid="gfm-refusal">{refusal}</p>}
      <div className="flex gap-3 items-center mt-1">
        <Btn onClick={() => recommend.mutate()} disabled={busy || recommend.isPending}>
          <Play size={13} /> {searching ? 'Recommending…' : rec ? 'Recommend again' : 'Recommend GFM settings'}
        </Btn>
        <label className="text-[12px] flex items-center gap-1">
          <input type="checkbox" checked={allowIMax} onChange={e => setAllowIMax(e.target.checked)} />
          let i_max rise to 1.5 pu (hardware)
        </label>
      </div>
      {rec && <RecommendationView rec={rec} islandRowsOff={data?.island_rows_now === false} />}
      {canApply && (
        <div className="mt-2">
          <Btn onClick={() => apply.mutate()} disabled={apply.isPending || busy}>
            <Check size={13} /> Apply to the island data and the campus file
          </Btn>
        </div>
      )}
      {applied && (
        <p className="text-[12px] mt-2" data-testid="gfm-resolve" role="status">
          Applied ({applied.changes.length} setting{applied.changes.length === 1 ? '' : 's'}).{' '}
          {applied.hub_resolve_needed
            ? <>Re-solve the hub: these settings change its island rows.{' '}
                <Btn onClick={requestRunSolve}><Play size={12} /> Solve</Btn></>
            : 'Island constraints are off, so the hub solve does not read these settings.'}
        </p>
      )}
    </div>
  )
}

function RecommendationView({ rec, islandRowsOff }: { rec: GfmRecommendation; islandRowsOff: boolean }) {
  const units = Object.entries(rec.settings)
  return (
    <div className="mt-2" data-testid="gfm-recommendation">
      <p className="text-[11.5px] text-muted italic" data-testid="gfm-label">{rec.label}</p>
      <p className="text-[12px] mt-1" data-testid="gfm-status">
        <Tag tone={rec.status === 'not_achievable' ? 'err' : rec.status === 'incomplete' ? 'warn' : 'ok'}>
          {rec.status.replace('_', ' ')}
        </Tag>{' '}
        {STATUS_TEXT[rec.status]} ({rec.simulations} of at most {rec.max_simulations} simulations,
        margin {num(100 * rec.margin)} %)
      </p>
      {rec.status === 'not_achievable' && islandRowsOff && (
        <p className="text-[12px] mt-1 text-warn" data-testid="gfm-island-rows-off">{ISLAND_ROWS_OFF_NOTE}</p>
      )}
      {units.length > 0 && (
        <table className="text-[12px] whitespace-nowrap mt-1" data-testid="gfm-settings">
          <thead><tr><th className={TH}>unit</th><th className={TH}>control</th>
            {FIELDS.map(([, label]) => <th key={label} className={TH}>{label}</th>)}</tr></thead>
          <tbody>
            {units.map(([unit, s]) => (
              <tr key={unit}>
                <td className={TD}>{unit}</td><td className={TD}>{s.model}</td>
                {FIELDS.map(([f, label]) => {
                  const a = s.current[f], b = s.recommended?.[f]
                  // null: the engine wrote a non-finite value (an unrated unit, say)
                  const cell = (v: number | null | undefined) => (v === null ? 'not stated' : num(v))
                  return <td key={label} className={TD}>
                    {a === undefined ? '' : b === undefined || b === a ? cell(a) : `${cell(a)} → ${cell(b)}`}
                  </td>
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {rec.cases.length > 0 && (
        <table className="text-[12px] whitespace-nowrap mt-2" data-testid="gfm-cases">
          <thead><tr><th className={TH}>case</th><th className={TH}>now</th><th className={TH}>recommended</th></tr></thead>
          <tbody>
            {rec.cases.map(c => (
              <tr key={c.key}>
                <td className={TD}>{c.key}</td>
                <td className={TD}>{c.ok_before ? 'passes' : `fails: ${c.binding_before}`}</td>
                <td className={TD}>{c.ok_after === null ? c.binding_after
                  : c.ok_after ? 'passes' : `fails: ${c.binding_after}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {rec.capacity.length > 0 && (
        <div className="mt-2" data-testid="gfm-capacity">
          <h4 className="text-[12.5px] font-semibold">Capacity needed instead</h4>
          <ul className="text-[12px] list-disc ml-5">
            {rec.capacity.map((c, i) => (
              <li key={i}>{c.case}: {c.delta == null ? c.binding : `${c.kind} +${num(c.delta)} ${c.unit} (${c.binding})`}
                {c.feasible ? '' : `: ${c.note}`}</li>
            ))}
          </ul>
        </div>
      )}
      <details className="mt-2 text-[11.5px] text-muted">
        <summary>What the search assumed</summary>
        <ul className="list-disc ml-5" data-testid="gfm-ledger">
          {rec.ledger.map(l => <li key={l}>{l}</li>)}
          {rec.excluded.map(l => <li key={l}>excluded: {l}</li>)}
          {rec.notes.map(l => <li key={l}>{l}</li>)}
        </ul>
      </details>
    </div>
  )
}
