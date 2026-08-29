import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Repeat, ShieldCheck, Square } from 'lucide-react'
import { resultsApi } from '../../api/simulation'
import type {
  CouplingIteration, CouplingLoopPayload, CouplingLoopRequestBody,
  CouplingMcBlock, McStatus,
} from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { basisSuffix, type CoptPayload } from './adequacy'
import { blockerMessage, ciRange, trim } from './McPanel'

// ── The adequacy-coupled planning loop (Phase 7, plan §3 / spec §4) ──────────
//
// Solve the LP under an energy cap, run the sequential MC on the PLAN that
// solve produced, retune the cap, re-solve — until the plan meets the user's
// target on the MC's OWN LOLE rather than on the LP proxy's shed energy. The
// two are not the same standard: the LP has perfect foresight over storage and
// no outages at all, so a plan that sheds exactly its cap in the LP can lose
// load for tens of hours in the MC.
//
// Every quantity rendered here comes from the payload. In particular the
// VERDICT is a ready sentence the route composes (spec v1.3 §4) and this panel
// prints verbatim: the unreachable copy names three distinct mechanisms
// because the user's next action differs by which one is operating ([N6]), and
// a frontend paraphrase would be a second, drifting copy of a piece of
// engineering judgement that lives in the engine.
//
// No SVG is drawn on purpose: the study's product is a verdict, a cap and a
// table of iterates, and a chart of at most eight points would be decoration.
// (Were one added: literal hex only. `var(--…)` does not resolve inside SVG
// attributes, a bug this directory has already paid for twice.)

/** Compact euro figure — same ladder FrontierPanel uses for the same axis. */
function eur(v: number): string {
  if (!isFinite(v)) return '—'
  return v >= 1e9 ? `€${(v / 1e9).toFixed(2)}bn`
    : v >= 1e6 ? `€${(v / 1e6).toFixed(1)}m`
      : `€${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

/** Display number: 2 dp above 1, 2 significant figures below, zeros trimmed. */
function compact(v: number): string {
  if (!isFinite(v)) return '—'
  const s = Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(2)
  return s.includes('.') ? s.replace(/0+$/, '').replace(/\.$/, '') : s
}

/**
 * A horizon within half a percent of a year reads as annual.
 *
 * Not an exact `=== 1`: `horizon_years` is a weighted sum divided by 8760 and
 * lands on 0.9999… for an ordinary hourly year. An exact test would print the
 * dual echo for a network the user calls "a year", which is precisely the
 * distinction the echo exists NOT to invent.
 */
const ANNUAL_TOL = 0.005

/**
 * ★ [S12] The dual target echo.
 *
 * The user types **h/yr** — the unit every reliability standard is written in
 * (GB's is 3 loss-of-load hours per year) — and the study is measured over
 * whatever horizon the network actually spans. On a representative week those
 * differ by 52x, in the dangerous direction: a "3 h/yr" typed straight onto
 * the wire is a target a 168-hour study meets trivially, and the verdict then
 * certifies a plan against a standard nobody stated.
 *
 * So the panel converts and SHOWS BOTH numbers live, and when no study has
 * reported a horizon length yet it says out loud that it is assuming a year
 * rather than assuming one silently.
 */
export function targetEcho(
  hYr: number, horizonYears: number | null | undefined,
): string {
  const typed = compact(hYr)
  if (horizonYears == null || !isFinite(horizonYears) || horizonYears <= 0) {
    return `${typed} h/yr — nothing has reported this network's horizon length `
      + 'yet, so the loop is ASSUMING one year and will send this number '
      + 'unconverted. Run the COPT screening or a Monte-Carlo study to convert '
      + 'it exactly.'
  }
  if (Math.abs(horizonYears - 1) <= ANNUAL_TOL) {
    return `${typed} h/yr — the modelled horizon is one year, so the standard `
      + 'and the horizon-basis target the loop is measured against are the '
      + 'same number.'
  }
  const hours = Math.round(horizonYears * 8760)
  return `${typed} h/yr = ${compact(hYr * horizonYears)} h / ${hours} h horizon`
}

/**
 * Where the ENTRY field's conversion gets its horizon, in order of what
 * EXISTS on a live network.
 *
 * The MC's own metrics first: it is the engine the target is measured
 * against, so its idea of the horizon is the authoritative one. The COPT
 * second, because it needs no solve at all and is therefore the only source
 * that exists before anything has been run — without the fallback the first
 * loop of a session would convert against nothing.
 *
 * NULL when neither has reported one, never a fabricated 1. A silent "assume
 * a year" is exactly the [S12] failure mode: the caller is expected to say so
 * out loud (see `targetEcho`) rather than quietly convert by 1.
 */
export function entryHorizonYears(
  mc: McStatus | null | undefined,
  copt: CoptPayload | null | undefined,
): number | null {
  // COPT FIRST, and the order is the whole point. `/results/copt` is computed
  // on demand from the LIVE network on every fetch; `/results/mc` serves the
  // last STUDY RECORD, which outlives the network it was computed on. A
  // browser round caught exactly that: a stored 168 h study answered for a
  // live 48 h network, and "3 h/yr" went on the wire as 0.0575 h instead of
  // 0.0164 h — a 3.5x wrong standard, silently, both numbers plausible. For a
  // question about THIS network's horizon the on-demand surface is the
  // authority; the stored study is only for the moment before any COPT has
  // been fetched, where a stale horizon still beats assuming a year.
  const fromCopt = copt?.metrics?.horizon_years
  if (fromCopt != null && isFinite(fromCopt) && fromCopt > 0) return fromCopt
  const fromMc = mc?.result?.metrics?.horizon_years
  if (fromMc != null && isFinite(fromMc) && fromMc > 0) return fromMc
  return null
}

/**
 * ★ [S12] The number that actually goes on the wire: HORIZON-basis hours.
 *
 * The route's `target_lole_h` is compared against `mc_adequacy`'s
 * `lole_hours`, which is a sum over the modelled horizon. Sending the h/yr
 * figure unconverted is not a rounding error — on a representative week it
 * loosens the standard by the horizon's reciprocal.
 *
 * Identity when the horizon is a year or unknown: with nothing to convert
 * through, the honest conversion is none at all, and `targetEcho` says so.
 */
export function wireTarget(
  hYr: number, horizonYears: number | null | undefined,
): number {
  if (horizonYears == null || !isFinite(horizonYears) || horizonYears <= 0) {
    return hYr
  }
  return hYr * horizonYears
}

/**
 * One iterate's MC-LOLE, as a RANGE with the basis-carrying unit.
 *
 * A range and never `mean ± half`: the interval the engine reports may be
 * asymmetric, so a half-width belongs to neither bound, and near zero — the
 * all-clear case this loop exists to reach — `±` prints a negative lower
 * bound for a quantity that cannot be negative.
 *
 * A DASH for `mc: null`: an infeasible or failed iterate was never evaluated
 * (evaluating it would score the PREVIOUS plan against this ε), and "0" or a
 * blank cell would both read as a measurement that was never taken.
 *
 * And zero shortfall hours is NOT "0 h": it is "below what these draws can
 * resolve", which is what `resolution_floor_h` says.
 */
export function loleCell(
  mc: CouplingMcBlock | null | undefined,
  unit: string,
  floor: number | null | undefined,
): string {
  if (!mc) return '—'
  if (mc.lole_hours <= 0) {
    if (floor != null && isFinite(floor) && floor > 0) {
      return `< ${trim(floor)} ${unit}`
    }
    return `${trim(mc.lole_hours)} ${unit} — unknown resolution (this horizon `
      + 'carries no positive weight, so the sampler cannot state a floor)'
  }
  return ciRange(mc.lole_ci, unit) ?? `${trim(mc.lole_hours)} ${unit}`
}

/**
 * ★ [S9] What each restore mode leaves the user holding.
 *
 * The loop mutates the network once per iterate, so SOMETHING has to be
 * re-solved at the end, and which one is a real decision with no safe
 * default-by-omission: "base" hands back the user's own config (and the
 * certified plan is then only a number they must apply themselves), "final"
 * writes ε* into solver settings and re-solves there.
 *
 * One sentence each, both shown, because the choice is made BEFORE the run
 * and a user cannot pick between two options when only the selected one is
 * described. The base sentence names the exact setting to apply — once a run
 * has produced ε* it names the value, and before that it names the symbol.
 */
export function restoreSentence(
  mode: 'base' | 'final', epsStar: number | null | undefined,
): string {
  const eps = epsStar != null && isFinite(epsStar) ? compact(epsStar) : 'ε*'
  if (mode === 'final') {
    return 'Keep the certified plan: ε* is written into your solver settings '
      + `(ens_cap_permyriad = ${eps}) and the closing re-solve runs at that `
      + 'cap, so the network you are left holding IS the plan the verdict is '
      + 'about. Applied only on a met verdict — anything else falls back.'
  }
  return 'Leave your settings alone: the closing re-solve uses your original '
    + 'config, so the network you are left holding is NOT the certified plan '
    + `— to keep that plan, set ens_cap_permyriad = ${eps} and re-solve.`
}

/** Status chip colouring — a verdict is not a mood, but it is a signal. */
const STATUS_CLASS: Record<string, string> = {
  running: 'bg-panel border border-border text-muted',
  met: 'bg-accent/10 text-accent',
  unreachable: 'bg-warn/10 text-warn',
  budget_exhausted: 'bg-warn/10 text-warn',
  aborted: 'bg-panel border border-border text-muted',
  failed: 'bg-danger/10 text-danger',
}

export function LoopPanel() {
  const currentProject = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  // Empty by default, deliberately: the target is the ONE required field and
  // the loop is up to eight full capacity expansions. A pre-filled 3 would
  // make "run" a one-click commitment to a standard the user never typed.
  const [target, setTarget] = useState('')
  const [draws, setDraws] = useState('')
  const [restore, setRestore] = useState<'base' | 'final'>('base')
  const [blocked, setBlocked] = useState<string | null>(null)

  const { data } = useQuery({
    queryKey: nk(currentProject, 'results', 'coupling_loop'),
    queryFn: () => resultsApi.getCouplingLoop(),
    // The record's `iterations` list grows between polls ([S6]) — the table
    // below reads straight off the query cache so each landed iterate appears
    // as it lands, rather than after a run that takes tens of minutes.
    refetchInterval: (q) =>
      (q.state.data as CouplingLoopPayload | null)?.status === 'running'
        ? 2000 : false,
  })
  const payload = (data ?? null) as CouplingLoopPayload | null
  const running = payload?.status === 'running'

  // Same query keys McPanel and AdequacyTab use, so this reads the cache the
  // tab already populated rather than issuing its own round-trips.
  const { data: mc } = useQuery({
    queryKey: nk(currentProject, 'results', 'mc'),
    queryFn: () => resultsApi.getMc(),
  })
  const { data: copt } = useQuery({
    queryKey: nk(currentProject, 'results', 'copt'),
    queryFn: () => resultsApi.getCopt(),
  })

  // The MC then the COPT (see `entryHorizonYears`), then — only if neither has
  // run in this session — a finished loop's own record, which carries the
  // horizon it was itself measured over. When none of the three has an answer
  // the conversion is the identity and `targetEcho` says so out loud.
  const horizonYears =
    entryHorizonYears(mc as McStatus | null, copt as CoptPayload | null)
    ?? payload?.horizon_years
    ?? null

  // The unit every rendered LOLE carries. From the finished study's own basis
  // when there is one — a payload's numbers must be labelled by the payload —
  // otherwise from whatever horizon the conversion found.
  const unit = basisSuffix(
    payload
      ? { time_basis: payload.basis, horizon_years: payload.horizon_years }
      : { horizon_years: horizonYears },
  )

  const typed = Number(target)
  const targetValid = target.trim() !== '' && isFinite(typed) && typed > 0

  const run = useMutation({
    mutationFn: () => {
      // `target_lole_h` is horizon-basis and `restore` is an explicit user
      // choice, so both always ride. `draws` is omitted when blank: the
      // engine's default is 500 and inventing a frontend one would fork it.
      const body: CouplingLoopRequestBody = {
        target_lole_h: wireTarget(typed, horizonYears),
        restore,
      }
      const d = Number(draws)
      if (draws.trim() !== '' && isFinite(d) && d > 0) body.draws = d
      return resultsApi.startCouplingLoop(body)
    },
    onSuccess: () => {
      setBlocked(null)
      void qc.invalidateQueries({
        queryKey: nk(currentProject, 'results', 'coupling_loop') })
    },
    // The server's own words: the 409 detail NAMES which of the five mesh
    // members holds the surface, which is the only actionable part of the
    // refusal, and the 422 detail explains a refusal the user can fix.
    onError: (e: unknown) => setBlocked(blockerMessage(e)),
  })

  const abort = useMutation({
    mutationFn: () => resultsApi.abortCouplingLoop(),
    onSuccess: () => void qc.invalidateQueries({
      queryKey: nk(currentProject, 'results', 'coupling_loop') }),
  })

  const rows: CouplingIteration[] = payload?.iterations ?? []
  const floor = payload?.resolution_floor_h ?? null

  return (
    <section className="border border-border rounded" data-testid="loop-panel">
      <button
        onClick={() => setOpen(o => !o)}
        data-testid="loop-toggle"
        className="w-full flex items-center gap-2 px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted hover:text-accent"
      >
        <Repeat size={11} /> Reliability-targeted planning loop {open ? '▾' : '▸'}
      </button>
      {open && (
        <div className="p-3 flex flex-col gap-3">
          <p className="text-[11px] text-muted">
            Solves the plan under an energy cap, runs the sequential MC on the
            plan that solve produced, retunes the cap and re-solves — until the
            plan meets your target on the <strong>MC's own LOLE</strong> rather
            than on the LP proxy's shed energy. Each iterate is a full capacity
            expansion plus a sampling run, so a study is minutes to tens of
            minutes; it holds the network for its whole run.
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
              data-testid="loop-run"
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
                data-testid="loop-abort"
                className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[10px] text-muted hover:border-danger hover:text-danger"
                title="Stops between iterates — the iterate already in flight finishes, and the closing restore still runs."
              >
                <Square size={9} /> Abort
              </button>
            )}
            {blocked && (
              <span className="text-[10px] text-warn" data-testid="loop-blocked">
                Blocked: {blocked}
              </span>
            )}
            {payload?.error && (
              <span className="text-[10px] text-danger" data-testid="loop-error">
                {payload.error}
              </span>
            )}
          </div>

          {targetValid && (
            <span
              className="text-[10px] text-muted font-mono"
              data-testid="loop-target-echo"
            >
              {targetEcho(typed, horizonYears)}
            </span>
          )}

          {/* ── ★ [S9] the restore choice, both modes described ─────────── */}
          <div className="flex flex-col gap-1">
            <label className="text-[10px] text-muted flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                data-testid="loop-restore-toggle"
                checked={restore === 'final'}
                onChange={e => setRestore(e.target.checked ? 'final' : 'base')}
              />
              Leave me holding the certified plan (restore at ε*)
            </label>
            <div
              className="flex flex-col gap-0.5 text-[10px]"
              data-testid="loop-restore-explain"
            >
              <span className={restore === 'base' ? 'text-text' : 'text-muted'}>
                <span className="font-semibold">base</span>
                {' — '}{restoreSentence('base', payload?.eps_star)}
              </span>
              <span className={restore === 'final' ? 'text-text' : 'text-muted'}>
                <span className="font-semibold">final</span>
                {' — '}{restoreSentence('final', payload?.eps_star)}
              </span>
            </div>
          </div>

          {/* 204 before any run. "Not run" is a different statement from a
              result of zero — the same discipline the engine-comparison table
              keeps for its own empty cells — so it is said, not implied by an
              empty box. */}
          {!payload && (
            <p className="text-[10px] text-muted" data-testid="loop-not-run">
              No coupling loop has been run in this session. Nothing below is a
              result of zero: there is no result. A run costs up to{' '}
              {'max_solves'} full capacity expansions plus one sampling study
              each, and it holds the network for its whole duration.
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
                  data-testid="loop-status"
                >
                  {payload.status}
                </span>
                {/* The target the study was actually RUN against, in the
                    study's own basis — the same conversion the entry field
                    did on the way in, legible on the way out. Without it the
                    user cannot check that the standard they typed is the
                    standard that was tested. */}
                <span
                  className="px-2 py-0.5 rounded bg-panel border border-border text-[10px] font-mono"
                  data-testid="loop-target"
                >
                  target {trim(payload.target_lole_h)} {unit}
                </span>
                {payload.eps_star != null && (
                  <span
                    className="px-2 py-0.5 rounded bg-panel border border-border text-[10px] font-mono"
                    data-testid="loop-eps-star"
                  >
                    ε* {compact(payload.eps_star)}‱
                  </span>
                )}
                {/* `confident` is the 95% CI upper bound clearing the target —
                    a DRAWS property, reported and never iterated for. A badge
                    on a false value would certify a claim the study did not
                    make, so it renders only on true. */}
                {payload.confident && (
                  <span
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-accent/10 text-accent text-[10px]"
                    data-testid="loop-confident"
                    title="The 95% CI upper bound also clears the target, not just the mean — the verdict survives the sampling error at these draws."
                  >
                    <ShieldCheck size={10} /> confident
                  </span>
                )}
                <span
                  className="text-[10px] text-muted font-mono"
                  data-testid="loop-solves-used"
                >
                  {payload.solves_used} solve{payload.solves_used === 1 ? '' : 's'}
                </span>
                {/* The closing re-solve is wrapped in try/finally precisely
                    because it CAN fail, and a false here means the network on
                    screen is the LAST ITERATE's plan rather than the one the
                    verdict describes — the single most misleading state this
                    study has, and invisible unless it is said. */}
                <span
                  className={'text-[10px] font-mono '
                    + (payload.base_restored ? 'text-muted' : 'text-danger')}
                  data-testid="loop-restored"
                >
                  {payload.base_restored
                    ? `restored (${payload.restore})`
                    : 'NOT restored — the closing re-solve failed, so the '
                      + 'network you are holding is the last iterate\'s plan, '
                      + 'not the plan this verdict is about'}
                </span>
              </div>
              {payload.verdict && (
                <p className="text-[11px] text-text" data-testid="loop-verdict">
                  {payload.verdict}
                </p>
              )}
              {payload.final && (
                <span className="text-[10px] text-muted font-mono">
                  final MC-LOLE{' '}
                  <span data-testid="loop-final-lole">
                    {loleCell(payload.final.mc, unit, floor)}
                  </span>
                </span>
              )}
            </div>
          )}

          {payload?.warning && (
            <p
              className="inline-flex items-start gap-1 text-[10px] text-warn border border-warn/40 rounded px-2 py-1"
              data-testid="loop-warning"
            >
              <AlertTriangle size={11} className="mt-[1px] shrink-0" />
              <span>{payload.warning}</span>
            </p>
          )}

          {/* ── the iterates ───────────────────────────────────────────── */}
          {rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-[10px]" data-testid="loop-iterations">
                <thead className="text-muted">
                  <tr>
                    <th className="text-right font-medium py-1 pr-3">ε ‱</th>
                    <th className="text-left  font-medium py-1 pr-3">Solve</th>
                    <th className="text-left  font-medium py-1 pr-3">Bound by</th>
                    <th className="text-right font-medium py-1 pr-3">Cost</th>
                    <th className="text-left  font-medium py-1 pr-3">MC-LOLE (95% CI)</th>
                    <th className="text-left  font-medium py-1">Note</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {rows.map((r, i) => (
                    <tr key={`${i}:${r.eps_permyriad}`}
                        className="border-t border-border/50 align-top"
                        data-testid={`loop-iter-${i}`}>
                      <td className="py-0.5 pr-3 text-right">{compact(r.eps_permyriad)}</td>
                      <td className="py-0.5 pr-3 font-sans">
                        {r.solve_status}
                        {r.condition && r.condition !== r.solve_status
                          ? ` (${r.condition})` : ''}
                      </td>
                      <td className="py-0.5 pr-3 font-sans">{r.binding ?? '—'}</td>
                      <td className="py-0.5 pr-3 text-right"
                          data-testid={`loop-iter-${i}-cost`}>
                        {r.cost_eur != null ? eur(r.cost_eur) : '—'}
                      </td>
                      <td className="py-0.5 pr-3" data-testid={`loop-iter-${i}-lole`}>
                        {loleCell(r.mc, unit, floor)}
                      </td>
                      {/* A plateau iterate's metrics were REUSED, not sampled:
                          the plan hash repeated, so the MC would have been
                          bit-identical. Marking it is the difference between
                          "measured twice" and "measured once". */}
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
