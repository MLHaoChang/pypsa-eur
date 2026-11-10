// ── Plan vs operation (DC grid readiness 07, S4) ────────────────────────────
// The operational (rolling) run against a full-horizon dispatch on the same
// sizes, read from `/results/operational_gap` (S3). Shown on Results › Summary,
// in both modes, only after an operational run: the flag `missing` renders
// nothing, and so does a solve in flight (no table from an earlier run).
//
// The numbers are the route's own: gap and gap % are never recomputed here,
// and a side the network cannot give reads "—" with its reason, never 0.
// The card's words come from OP_GAP_WORDS; the route's engine definitions
// sit only under "Source" (UX: engine identifiers only in a Source column).
import { useQuery } from '@tanstack/react-query'
import {
  operationalGapApi, type OperationalGapPayload, type OperationalGapRow,
} from '../../api/operationalGap'
import { simulationApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { money } from '../../utils/money'
import { fmtEnergy } from '../../utils/format'
import { openSolveStrategy } from '../../layout/flowNavigation'
import ResultState from '../../components/ResultState'
import { ROLLING_WORDS } from '../solver/rollingWords'

/** The card's words per metric; its keys equal the service's DEFINITIONS keys
 *  (`tests/test_operational_gap_words.py` fails when they drift). */
export const OP_GAP_WORDS: Record<string, {
  label: string; unit: 'eur' | 'mwh'; definition: string; notModelled: string
}> = {
  opex_eur: {
    label: 'Operating cost',
    unit: 'eur',
    definition: 'Running and storage costs over the whole horizon. Capital costs are left out: the sizes are the same on both sides.',
    notModelled: 'No dispatch on this side.',
  },
  unserved_energy_mwh: {
    label: 'Unserved energy',
    unit: 'mwh',
    definition: 'Demand the dispatch could not serve. Curtailed data-centre load is not in it.',
    notModelled: 'Not modelled: no value of lost load is set, so the solve could not shed load.',
  },
  curtailed_energy_mwh: {
    label: 'Curtailed data-centre load',
    unit: 'mwh',
    definition: 'Data-centre cooling load given up at its curtailment price, kept apart from unserved energy.',
    notModelled: 'Not modelled: no curtailment price is set for the data-centre load.',
  },
  storage_discharge_mwh: {
    label: 'Storage discharged',
    unit: 'mwh',
    definition: 'Energy taken out of batteries and stores over the horizon.',
    notModelled: 'No storage in the network.',
  },
  storage_end_mwh: {
    label: 'Storage left at the end',
    unit: 'mwh',
    definition: 'Energy left in batteries and stores at the last time step. Neither side has to end where it started.',
    notModelled: 'No storage in the network.',
  },
}

/** The route's reason for a metric it did not record (not the network lacking it). */
const NOT_ESTABLISHED = 'not established'
const NOT_RECORDED_WORDS = 'Not recorded for this run.'

/** The full-horizon dispatch's termination condition in plain words (UX condition 5). */
export const REFERENCE_CONDITION_WORDS: Record<string, string> = {
  infeasible: 'has no feasible answer',
  infeasible_or_unbounded: 'has no feasible answer',
  unbounded: 'has no bounded answer',
  time_limit: 'hit the time limit',
  iteration_limit: 'hit the iteration limit',
  suboptimal: 'did not converge',
  numeric: 'did not converge',
}

/** A solve's termination condition in the words every source line uses: "solved", never a solver term. */
export const solveWords = (condition: string | null | undefined): string =>
  condition === 'optimal' ? 'solved' : (condition && REFERENCE_CONDITION_WORDS[condition]) || 'did not solve'
const REFERENCE_DEFAULT_WORDS = 'did not solve'

export const SIGN_WORDS = 'Gap: operation minus full horizon. Positive means operation costs more.'
const BACK_LABEL = 'Results summary'

const MINUS = '−'

function magnitude(v: number, unit: 'eur' | 'mwh'): string {
  return unit === 'eur' ? money(Math.abs(v), 'EUR') : fmtEnergy(Math.abs(v))
}

/** A side's value: unsigned notation with a leading minus when negative. */
export function valueText(v: number | null, unit: 'eur' | 'mwh'): string {
  if (v == null || !Number.isFinite(v)) return '—'
  return `${v < 0 ? MINUS : ''}${magnitude(v, unit)}`
}

/** A gap: always signed (+/−), 0 unsigned. */
export function gapText(v: number | null, unit: 'eur' | 'mwh'): string {
  if (v == null || !Number.isFinite(v)) return '—'
  if (v === 0) return magnitude(0, unit)
  return `${v > 0 ? '+' : MINUS}${magnitude(v, unit)}`
}

export function pctText(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return '—'
  const a = Math.abs(v).toFixed(1)
  if (Number(a) === 0) return '0.0 %'
  return `${v > 0 ? '+' : MINUS}${a} %`
}

/** The failed window, 1-based, only when the route names it (never "NaN of N"). */
export function failedWindowWords(w: OperationalGapPayload['windows']): string {
  const i = w?.failed_index
  if (typeof i === 'number' && Number.isFinite(i) && typeof w?.total === 'number') {
    return `Window ${i + 1} of ${w.total} did not solve`
  }
  return 'A window did not solve'
}

/** Gap % with no value although both sides have one: the full horizon is 0. */
export const PCT_ZERO_REFERENCE = 'No percentage: the full horizon is 0.'

/** Why a side reads "—", in the card's words. */
function reasonWords(key: string, reason: string | undefined): string {
  if (reason === NOT_ESTABLISHED) return NOT_RECORDED_WORDS
  return OP_GAP_WORDS[key]?.notModelled ?? NOT_RECORDED_WORDS
}

export function rowReason(key: string, row: OperationalGapRow): string | null {
  const parts: string[] = []
  if (row.operational == null) parts.push(`Operation: ${reasonWords(key, row.operational_reason)}`)
  if (row.reference == null) parts.push(`Full horizon: ${reasonWords(key, row.reference_reason)}`)
  if (parts.length === 2 && reasonWords(key, row.operational_reason) === reasonWords(key, row.reference_reason)) {
    return reasonWords(key, row.operational_reason)
  }
  return parts.length ? parts.join(' ') : null
}

export default function OperationalGap() {
  const project = useUIStore(s => s.currentProject)
  const { data: status } = useQuery({ queryKey: nk(project, 'simulationStatus'), queryFn: simulationApi.getStatus })
  const { data, isFetching } = useQuery({
    queryKey: nk(project, 'results', 'operational_gap'),
    queryFn: operationalGapApi.get,
    retry: false,
    refetchOnWindowFocus: false,
  })
  // A solve in flight answers 409; while it runs, or while the card refetches
  // after it, show nothing rather than an earlier run's table (UX condition 7).
  if (!data || isFetching || status?.running || data.flag === 'missing') return null
  return <OperationalGapCard payload={data} />
}

export function OperationalGapCard({ payload }: { payload: OperationalGapPayload }) {
  const requestRunSolve = useUIStore(s => s.requestRunSolve)
  const w = payload.windows
  const settings = typeof payload.horizon === 'number' && typeof payload.overlap === 'number'
    ? ` (rolling, ${payload.horizon} ${ROLLING_WORDS.horizon}, ${payload.overlap} ${ROLLING_WORDS.overlap})`
    : ''
  return (
    <section data-testid="summary-operational-gap" data-flag={payload.flag}
      className="rounded-[10px] border border-border bg-bg-2 px-4 py-3 flex flex-col gap-2 min-w-0">
      <h3 className="text-[12px] font-semibold text-muted">Plan vs operation</h3>
      <p className="text-[11px] text-muted" data-testid="operational-gap-lead">
        Operation{settings} against a full-horizon dispatch on the same sizes
        {w ? ` · ${w.solved} of ${w.total} windows solved` : ''}
      </p>
      {payload.flag === 'stale' ? (
        <ResultState state="stale"
          reason="The inputs, the window settings or the weightings changed since the operational run. Run it again to update the gap."
          action={{ label: 'Run the operational dispatch again', onClick: requestRunSolve }} />
      ) : payload.flag === 'operational_window_failed' ? (
        <div data-testid="operational-gap-window-failed"
          className="rounded-[8px] border border-border bg-bg px-3 py-2.5 text-[12px] text-muted">
          {failedWindowWords(w)}, so the run stopped there and there is no gap to show.{' '}
          <button type="button" data-testid="operational-gap-open-strategy"
            onClick={() => openSolveStrategy(BACK_LABEL)}
            className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline">
            Solver settings ›
          </button>
        </div>
      ) : payload.flag === 'reference_not_solved' ? (
        <p data-testid="operational-gap-reference-failed"
          className="rounded-[8px] border border-border bg-bg px-3 py-2.5 text-[12px] text-muted">
          The full-horizon dispatch {REFERENCE_CONDITION_WORDS[payload.reference?.condition ?? ''] ?? REFERENCE_DEFAULT_WORDS}, so there is no gap to show.
        </p>
      ) : payload.flag === 'fresh' && payload.metrics ? (
        <GapTable metrics={payload.metrics} />
      ) : (
        // A flag this app does not know (or fresh with no metrics): no table, fail closed.
        <ResultState state="unavailable" reason="The gap of this run cannot be read here." />
      )}
      <p className="text-[10.5px] text-muted" data-testid="operational-gap-claim">{payload.claim_limit}</p>
      <details className="text-[10px] text-muted" data-testid="operational-gap-source">
        <summary className="cursor-pointer">Source</summary>
        <p className="mt-1">{payload.opex_basis}.</p>
        <p className="mt-1">Left out of the operating cost: {payload.excluded_terms.join('; ')}.</p>
        <p className="mt-1">{payload.gap_definition}</p>
        {payload.metrics && Object.entries(payload.metrics).map(([k, row]) => (
          <p key={k} className="mt-1">{OP_GAP_WORDS[k]?.label ?? k}: {row.definition}</p>
        ))}
      </details>
    </section>
  )
}

function GapTable({ metrics }: { metrics: Record<string, OperationalGapRow> }) {
  const keys = Object.keys(OP_GAP_WORDS).filter(k => k in metrics)
  return (
    <>
      <p className="text-[10.5px] text-muted" data-testid="operational-gap-sign">{SIGN_WORDS}</p>
      <table className="w-full text-[11px]" data-testid="operational-gap-table">
        <thead>
          <tr className="text-[9.5px] text-muted text-left">
            <th className="font-normal">Measure</th>
            <th className="font-normal text-right">Operation</th>
            <th className="font-normal text-right">Full horizon</th>
            <th className="font-normal text-right">Gap</th>
            <th className="font-normal text-right">Gap %</th>
          </tr>
        </thead>
        <tbody>
          {keys.map(k => {
            const row = metrics[k]
            const words = OP_GAP_WORDS[k]
            const reason = rowReason(k, row)
            return (
              <tr key={k} data-testid={`operational-gap-row-${k}`} className="align-top">
                <td className="py-0.5 pr-2">
                  <span className="text-text">{words.label}</span>
                  <span className="block text-[10px] text-muted">{words.definition}</span>
                  {reason && (
                    <span className="block text-[10px] text-warn" data-testid={`operational-gap-reason-${k}`}>{reason}</span>
                  )}
                  {!reason && row.gap_pct == null && row.gap != null && (
                    <span className="block text-[10px] text-muted" data-testid={`operational-gap-pct-why-${k}`}>{PCT_ZERO_REFERENCE}</span>
                  )}
                </td>
                <td className="py-0.5 text-right font-mono tabular-nums">{valueText(row.operational, words.unit)}</td>
                <td className="py-0.5 text-right font-mono tabular-nums">{valueText(row.reference, words.unit)}</td>
                <td className="py-0.5 text-right font-mono tabular-nums">{gapText(row.gap, words.unit)}</td>
                <td className="py-0.5 text-right font-mono tabular-nums"
                  data-testid={`operational-gap-pct-${k}`}>{pctText(row.gap_pct)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </>
  )
}
