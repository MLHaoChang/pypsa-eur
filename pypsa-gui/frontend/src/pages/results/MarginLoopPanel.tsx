import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Gauge, ShieldCheck, Square } from 'lucide-react'
import { resultsApi } from '../../api/simulation'
import type {
  McStatus, MarginIteration, MarginLoopPayload, MarginLoopRequestBody,
} from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { basisSuffix, type CoptPayload } from './adequacy'
import { blockerMessage, trim } from './McPanel'
import {
  compact, entryHorizonYears, eur, leverSpelling, loleCell, restoreSentence,
  targetEcho, wireTarget, type LeverCopy,
} from './LoopPanel'

// ── The margin-driven planning loop (Phase 9, margin-loop spec §3) ──────────
//
// The SAME search as LoopPanel's, on the OTHER lever. The backend substitutes
// `x = 1/(1+m)` so `services/adequacy/coupling.py` — which must not be
// modified, and is not — drives a planning RESERVE MARGIN instead of an energy
// cap, and translates every row back to a margin before storing it.
//
// ★ WHY THIS IS A SIBLING AND NOT A `lever` PROP ON LoopPanel.
//
// The two records are genuinely different shapes, not one shape with a
// discriminator: `eps_permyriad`/`eps0`/`eps_star` against
// `lever_value`/`margin0`/`lever_star`, plus `probe_solves`, `margin_tight`
// and `margin_ceiling`, which have no cap-loop counterpart at all. One
// component over both needs an adapter, and the shortest adapter anyone
// writes is a nullable alias (`eps_permyriad: number | null`) — which is
// precisely the failure spec §3 forbids: `compact()` is typed `number` and
// `isFinite(null)` is TRUE in JS, so the null sails through the guard into
// `.toPrecision(2)`, throws inside `rows.map`, and React unmounts the whole
// panel mid-study. Both panels also mount on the SAME tab at the same time,
// so a shared component would need every `data-testid` parameterised.
//
// What is actually shared is PURE and already exported: the h/yr → horizon
// conversion and its dual echo ([S12]), the LOLE cell with its `< floor`
// all-clear ([N4]), the euro ladder, the number ladder, and — the one that
// mattered — `restoreSentence`, which is now driven by a `LeverCopy` instead
// of hard-coding `ens_cap_permyriad` into a sentence it renders on both modes,
// before any run, whatever the verdict says.

/**
 * ★ The margin lever's restore copy.
 *
 * `format` is NOT the badge's formatter. The badge reads "135.7%"; the config
 * field takes `1.357`, and a sentence that told the user to type a rounded
 * 1.36 would name a margin the study never certified. So the value goes in at
 * the precision the payload carries it, trailing float noise trimmed.
 */
export const MARGIN_LEVER: LeverCopy = {
  // The default field name, used only in the 204 state — the restore choice
  // is made BEFORE the run, so both sentences render with no payload at all.
  // Once a study exists, the panel takes the field from the payload's own
  // `lever` (see `leverCopy`), which is the backend's word, not this file's.
  field: 'reserve_margin',
  symbol: 'm*',
  format: leverSpelling,
}

/** The restore copy for THIS payload — field name straight off the wire. */
function leverCopy(payload: MarginLoopPayload | null): LeverCopy {
  return payload?.lever
    ? { ...MARGIN_LEVER, field: payload.lever }
    : MARGIN_LEVER
}

/**
 * A margin, in the unit the payload names it in.
 *
 * Margins are FRACTIONS on the wire (`m = 1.357`) and percentages on screen
 * ("135.7%"): 1.357 rendered raw reads as a rounding artefact, and rendered
 * with the cap loop's `‱` it reads as a number four orders of magnitude
 * smaller in a unit this lever does not have. One convention, everywhere this
 * panel prints a margin — the column, the badge, the ceiling.
 *
 * The suffix comes from `lever_unit`, so a backend that renames the unit
 * renames it here without a frontend edit; the ×100 belongs to the "%" it
 * ships with.
 */
export function leverPct(v: number, unit: string): string {
  return `${compact(v * 100)}${unit}`
}

/** Status chip colouring — the cap loop's ladder, same verdict vocabulary. */
const STATUS_CLASS: Record<string, string> = {
  running: 'bg-panel border border-border text-muted',
  met: 'bg-accent/10 text-accent',
  unreachable: 'bg-warn/10 text-warn',
  budget_exhausted: 'bg-warn/10 text-warn',
  aborted: 'bg-panel border border-border text-muted',
  failed: 'bg-danger/10 text-danger',
}

export function MarginLoopPanel() {
  const currentProject = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  // Empty by default, exactly as the cap loop: the target is the ONE required
  // field and the study is up to eight full capacity expansions plus a probe.
  const [target, setTarget] = useState('')
  const [draws, setDraws] = useState('')
  const [restore, setRestore] = useState<'base' | 'final'>('base')
  const [blocked, setBlocked] = useState<string | null>(null)

  const { data } = useQuery({
    queryKey: nk(currentProject, 'results', 'margin_loop'),
    queryFn: () => resultsApi.getMarginLoop(),
    // `iterations` GROWS between polls — the table reads straight off the
    // query cache so each landed iterate appears as it lands.
    refetchInterval: (q) =>
      (q.state.data as MarginLoopPayload | null)?.status === 'running'
        ? 2000 : false,
  })
  const payload = (data ?? null) as MarginLoopPayload | null
  const running = payload?.status === 'running'

  // The same query keys the rest of the tab uses, so this reads the cache the
  // tab already populated rather than issuing its own round-trips.
  const { data: mc } = useQuery({
    queryKey: nk(currentProject, 'results', 'mc'),
    queryFn: () => resultsApi.getMc(),
  })
  const { data: copt } = useQuery({
    queryKey: nk(currentProject, 'results', 'copt'),
    queryFn: () => resultsApi.getCopt(),
  })

  const horizonYears =
    entryHorizonYears(mc as McStatus | null, copt as CoptPayload | null)
    ?? payload?.horizon_years
    ?? null

  const unit = basisSuffix(
    payload
      ? { time_basis: payload.basis, horizon_years: payload.horizon_years }
      : { horizon_years: horizonYears },
  )
  // The lever's own unit, from the payload — never the cap loop's `‱`.
  const leverUnit = payload?.lever_unit ?? '%'
  const lever = leverCopy(payload)

  const typed = Number(target)
  const targetValid = target.trim() !== '' && isFinite(typed) && typed > 0

  const run = useMutation({
    mutationFn: () => {
      // No `m0`: the starting margin is a MEASUREMENT the route takes with a
      // probing solve (spec §2.3), and the request schema refuses to take one.
      const body: MarginLoopRequestBody = {
        target_lole_h: wireTarget(typed, horizonYears),
        restore,
      }
      const d = Number(draws)
      if (draws.trim() !== '' && isFinite(d) && d > 0) body.draws = d
      return resultsApi.startMarginLoop(body)
    },
    onSuccess: () => {
      setBlocked(null)
      void qc.invalidateQueries({
        queryKey: nk(currentProject, 'results', 'margin_loop') })
    },
    // The server's own words: the 409 detail NAMES which of the six mesh
    // members holds the surface, and the 422 detail explains a refusal the
    // user can act on — the unreachable margin ceiling and the unpriceable
    // assets both say which they are.
    onError: (e: unknown) => setBlocked(blockerMessage(e)),
  })

  const abort = useMutation({
    mutationFn: () => resultsApi.abortMarginLoop(),
    onSuccess: () => void qc.invalidateQueries({
      queryKey: nk(currentProject, 'results', 'margin_loop') }),
  })

  const rows: MarginIteration[] = payload?.iterations ?? []
  const floor = payload?.resolution_floor_h ?? null

  return (
    <section className="border border-border rounded" data-testid="margin-loop-panel">
      <button
        onClick={() => setOpen(o => !o)}
        data-testid="margin-loop-toggle"
        className="w-full flex items-center gap-2 px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted hover:text-accent"
      >
        <Gauge size={11} /> Reliability-targeted reserve margin loop{' '}
        {open ? '▾' : '▸'}
      </button>
      {open && (
        <div className="p-3 flex flex-col gap-3">
          <p className="text-[11px] text-muted">
            The same coupled search as the loop above, driving the{' '}
            <strong>planning reserve margin</strong> instead of an energy cap:
            solve the plan at a margin, run the sequential MC on the plan that
            solve produced, raise the margin and re-solve — until the plan
            meets your target on the <strong>MC's own LOLE</strong>. Your ENS
            cap, if you set one, is left in force for every iterate and is
            never rewritten. One probing solve measures where the search should
            start; it is reported separately from the solve budget.
          </p>

          {/* ── target entry: h/yr in, horizon-basis on the wire ────────── */}
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-[10px] text-muted flex items-center gap-1">
              Target LOLE
              <input
                type="number" min={0} step="any" value={target}
                aria-label="target LOLE in hours per year"
                onChange={e => setTarget(e.target.value)}
                className="w-24 px-1 py-0.5 border border-border rounded bg-bg text-[10px] font-mono"
              />
              h/yr
            </label>
            <label className="text-[10px] text-muted flex items-center gap-1">
              Draws
              <input
                type="number" min={1} value={draws} aria-label="draws"
                placeholder="500"
                onChange={e => setDraws(e.target.value)}
                className="w-20 px-1 py-0.5 border border-border rounded bg-bg text-[10px] font-mono"
              />
            </label>
            <button
              onClick={() => run.mutate()}
              disabled={running || !targetValid}
              data-testid="margin-loop-run"
              className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent disabled:opacity-50"
              title={blocked ?? (targetValid ? undefined
                : 'Type a reliability target first — it is the standard the '
                  + 'loop searches for a plan against.')}
            >
              Run loop
            </button>
            {running && (
              <button
                onClick={() => abort.mutate()}
                data-testid="margin-loop-abort"
                className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[10px] text-muted hover:border-danger hover:text-danger"
                title="Stops between iterates — the iterate already in flight finishes, and the closing restore still runs."
              >
                <Square size={9} /> Abort
              </button>
            )}
            {blocked && (
              <span className="text-[10px] text-warn" data-testid="margin-loop-blocked">
                Blocked: {blocked}
              </span>
            )}
            {payload?.error && (
              <span className="text-[10px] text-danger" data-testid="margin-loop-error">
                {payload.error}
              </span>
            )}
          </div>

          {targetValid && (
            <span
              className="text-[10px] text-muted font-mono"
              data-testid="margin-loop-target-echo"
            >
              {targetEcho(typed, horizonYears)}
            </span>
          )}

          {/* ── ★ [S9] the restore choice, both modes described ─────────
              The field name is the PAYLOAD's `lever`, not this file's memory
              of which loop it belongs to: this block renders unconditionally,
              on both modes, before any run — which is exactly where a
              hard-coded `ens_cap_permyriad` used to tell a margin user to set
              the energy cap. */}
          <div className="flex flex-col gap-1">
            <label className="text-[10px] text-muted flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                data-testid="margin-loop-restore-toggle"
                checked={restore === 'final'}
                onChange={e => setRestore(e.target.checked ? 'final' : 'base')}
              />
              Leave me holding the certified plan (restore at m*)
            </label>
            <div
              className="flex flex-col gap-0.5 text-[10px]"
              data-testid="margin-loop-restore-explain"
            >
              <span className={restore === 'base' ? 'text-text' : 'text-muted'}>
                <span className="font-semibold">base</span>
                {' — '}{restoreSentence('base', payload?.lever_star, lever)}
              </span>
              <span className={restore === 'final' ? 'text-text' : 'text-muted'}>
                <span className="font-semibold">final</span>
                {' — '}{restoreSentence('final', payload?.lever_star, lever)}
              </span>
            </div>
          </div>

          {/* 204 before any run. "Not run" is a different statement from a
              result of zero, so it is said rather than implied. */}
          {!payload && (
            <p className="text-[10px] text-muted" data-testid="margin-loop-not-run">
              No reserve-margin loop has been run in this session. Nothing below
              is a result of zero: there is no result. A run costs one probing
              solve plus up to {'max_solves'} full capacity expansions with a
              sampling study each, and it holds the network throughout.
            </p>
          )}

          {/* ── the verdict ────────────────────────────────────────────── */}
          {payload && (
            <div className="flex flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={'px-2 py-0.5 rounded text-[10px] font-semibold '
                    + (STATUS_CLASS[payload.status]
                      ?? 'bg-panel border border-border')}
                  data-testid="margin-loop-status"
                >
                  {payload.status}
                </span>
                <span
                  className="px-2 py-0.5 rounded bg-panel border border-border text-[10px] font-mono"
                  data-testid="margin-loop-target"
                >
                  target {trim(payload.target_lole_h)} {unit}
                </span>
                {payload.lever_star != null && (
                  <span
                    className="px-2 py-0.5 rounded bg-panel border border-border text-[10px] font-mono"
                    data-testid="margin-loop-lever-star"
                    title="The certified planning reserve margin — a fraction of peak demand, shown as a percentage."
                  >
                    m* {leverPct(payload.lever_star, leverUnit)}
                  </span>
                )}
                {/* ★ The search is BOUNDED ABOVE by what the candidate set can
                    physically reach. Without this an `unreachable` verdict
                    reads as "no margin works" rather than "no margin under
                    this ceiling works", which are different next actions. */}
                <span
                  className="px-2 py-0.5 rounded bg-panel border border-border text-[10px] font-mono"
                  data-testid="margin-loop-ceiling"
                  title="The largest margin this candidate set can reach; above it no plan exists at all and the margin is refused by the same preflight the solver runs."
                >
                  ceiling {payload.margin_ceiling != null
                    ? leverPct(payload.margin_ceiling, leverUnit)
                    : 'unbounded'}
                </span>
                {payload.confident && (
                  <span
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-accent/10 text-accent text-[10px]"
                    data-testid="margin-loop-confident"
                    title="The 95% CI upper bound also clears the target, not just the mean — the verdict survives the sampling error at these draws."
                  >
                    <ShieldCheck size={10} /> confident
                  </span>
                )}
                {/* The probe is OUTSIDE the budget (amendment v1.1(5)): the
                    budget is a promise about the SEARCH, and a user timing the
                    run must be able to account for every solve it made. */}
                <span
                  className="text-[10px] text-muted font-mono"
                  data-testid="margin-loop-solves-used"
                >
                  {payload.solves_used} solve
                  {payload.solves_used === 1 ? '' : 's'}
                  {' + '}{payload.probe_solves} probe solve
                  {payload.probe_solves === 1 ? '' : 's'}
                </span>
                <span
                  className={'text-[10px] font-mono '
                    + (payload.base_restored ? 'text-muted' : 'text-danger')}
                  data-testid="margin-loop-restored"
                >
                  {payload.base_restored
                    ? `restored (${payload.restore})`
                    : 'NOT restored — the closing re-solve failed, so the '
                      + 'network you are holding is the last iterate\'s '
                      + 'margin, not the plan this verdict is about'}
                </span>
              </div>
              {payload.verdict && (
                <p className="text-[11px] text-text" data-testid="margin-loop-verdict">
                  {payload.verdict}
                </p>
              )}
              {/* Where the search STARTED and why — both measured, neither
                  chosen. `margin_tight` is the smallest margin at which the
                  incumbent plan is already tight; the start overshoots it,
                  because at exactly that value nothing moves. */}
              {payload.margin0 != null && (
                <span
                  className="text-[10px] text-muted font-mono"
                  data-testid="margin-loop-start"
                >
                  start {leverPct(payload.margin0, leverUnit)}
                  {payload.margin_tight != null && (
                    <> (measured tight at{' '}
                      {leverPct(payload.margin_tight, leverUnit)})</>
                  )}
                </span>
              )}
              {payload.final && (
                <span className="text-[10px] text-muted font-mono">
                  final MC-LOLE{' '}
                  <span data-testid="margin-loop-final-lole">
                    {loleCell(payload.final.mc, unit, floor)}
                  </span>
                </span>
              )}
            </div>
          )}

          {payload?.warning && (
            <p
              className="inline-flex items-start gap-1 text-[10px] text-warn border border-warn/40 rounded px-2 py-1"
              data-testid="margin-loop-warning"
            >
              <AlertTriangle size={11} className="mt-[1px] shrink-0" />
              <span>{payload.warning}</span>
            </p>
          )}

          {/* ── the iterates ───────────────────────────────────────────── */}
          {payload && rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-[10px]" data-testid="margin-loop-iterations">
                <thead className="text-muted">
                  <tr>
                    {/* The header is the payload's own words: this column
                        holds margins, and "ε ‱" would mislabel every cell. */}
                    <th className="text-right font-medium py-1 pr-3">
                      {payload.lever_label} {payload.lever_unit}
                    </th>
                    <th className="text-left  font-medium py-1 pr-3">Solve</th>
                    <th className="text-left  font-medium py-1 pr-3">Bound by</th>
                    <th className="text-right font-medium py-1 pr-3">Cost</th>
                    <th className="text-left  font-medium py-1 pr-3">MC-LOLE (95% CI)</th>
                    <th className="text-left  font-medium py-1">Note</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {rows.map((r, i) => (
                    <tr key={`${i}:${r.lever_value}`}
                        className="border-t border-border/50 align-top"
                        data-testid={`margin-loop-iter-${i}`}>
                      {/* `lever_value` is a NUMBER, always — see
                          MarginIteration. Nothing nullable reaches
                          `leverPct`/`compact` from this row. */}
                      <td className="py-0.5 pr-3 text-right">
                        {leverPct(r.lever_value, leverUnit)}
                      </td>
                      <td className="py-0.5 pr-3 font-sans">
                        {r.solve_status}
                        {r.condition && r.condition !== r.solve_status
                          ? ` (${r.condition})` : ''}
                      </td>
                      <td className="py-0.5 pr-3 font-sans">{r.binding ?? '—'}</td>
                      <td className="py-0.5 pr-3 text-right"
                          data-testid={`margin-loop-iter-${i}-cost`}>
                        {r.cost_eur != null ? eur(r.cost_eur) : '—'}
                      </td>
                      <td className="py-0.5 pr-3" data-testid={`margin-loop-iter-${i}-lole`}>
                        {loleCell(r.mc, unit, floor)}
                      </td>
                      <td className="py-0.5 font-sans text-muted">
                        {r.plateau ? 'plateau (metrics reused)' : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
