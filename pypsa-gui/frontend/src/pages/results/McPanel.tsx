import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Dices } from 'lucide-react'
import { resultsApi } from '../../api/simulation'
import type { ElccRow, McMetrics, McStatus } from '../../api/simulation'
import { formatApiDetail } from '../../api/client'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import {
  basisSuffix, type AdequacyReportPayload, type CoptPayload,
} from './adequacy'

// ── The sequential Monte-Carlo adequacy study (spec §5, Phase 6) ────────────
//
// Chronological two-state sampling with storage carried through the hours, so
// it answers the question the COPT convolution structurally cannot: what a
// battery is worth when outages persist. Plus the ELCC table — the marginal,
// last-in capacity credit of one asset at a time.
//
// IA DECISION, recorded here rather than taken silently: v1 stays on the Lost
// load tab. The engines belong beside the lost-load evidence they are read
// against, and a mid-phase Results.tsx re-wiring (five coupled edits) buys no
// analysis. REVISIT CONDITION, recorded verbatim from the phase plan: "when
// the Phase-7 coupling loop or a fourth study lands, this tab has tipped and
// the adequacy surfaces split into a dedicated Results→Adequacy tab."
//
// No SVG is drawn here on purpose — the study's product is numbers with
// intervals and a table of refusals, and a chart of two scalars would be
// decoration. (Were one added: literal hex only. `var(--…)` does not resolve
// inside SVG attributes, a bug this directory has already paid for twice.)

/**
 * The 409 detail string, which NAMES the blocking study ("a frontier study is
 * running — wait for it to finish").
 *
 * A generic "busy" would be strictly worse than useless here: the mutual
 * exclusion mesh has four members (solve / sweep / frontier / MC) and the user
 * cannot act on the block without knowing which one to wait for or cancel.
 */
export function blockerMessage(err: unknown): string {
  const data = (err as { response?: { data?: { detail?: unknown } } })?.response?.data
  if (data?.detail != null) return formatApiDetail(data.detail)
  return String((err as Error)?.message ?? err)
}

/** Compact number for display: 2 dp above 1, 3 significant figures below. */
function trim(v: number): string {
  if (!isFinite(v)) return '—'
  const s = Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(3)
  return s.includes('.') ? s.replace(/0+$/, '').replace(/\.$/, '') : s
}

/**
 * A confidence interval as a RANGE — "9.11–9.54 h" — never as `mean ± half`.
 *
 * Two reasons, both load-bearing. The interval the engine reports may be
 * asymmetric, so a single half-width belongs to neither bound and quietly
 * misstates one of them. And near zero — the all-clear case this panel must
 * get right — `±` prints a negative lower bound for a quantity that cannot be
 * negative, which reads as a bug in the engine rather than a rounding artefact
 * of the display.
 */
export function ciRange(
  ci: [number, number] | null | undefined, unit = '',
): string | null {
  if (!ci || ci.length !== 2) return null
  const [lo, hi] = ci
  if (!isFinite(lo) || !isFinite(hi)) return null
  return `${trim(lo)}–${trim(hi)}${unit ? ` ${unit}` : ''}`
}

/**
 * The LOLE headline, including the all-clear case.
 *
 * Zero shortfall hours across every draw is NOT "LOLE = 0 h". It is "LOLE is
 * below what this many draws can resolve", and the engine ships the floor that
 * says how far below. Printing a bare "0 h" claims a precision the sampler
 * never had, and invites a comparison against a 3 h/yr standard that the
 * study cannot support.
 *
 * When the floor itself is null (a horizon carrying no positive weight, so
 * there is nothing to divide by) the honest statement is the value plus an
 * explicit "unknown resolution" — "< null h" would be worse than the bare
 * zero it replaced.
 */
export function loleStatement(m: McMetrics): string {
  const unit = basisSuffix(m)
  if (m.lole_hours <= 0) {
    const floor = m.resolution_floor_h
    if (floor != null && isFinite(floor) && floor > 0) return `< ${trim(floor)} ${unit}`
    return `${trim(m.lole_hours)} ${unit} — unknown resolution (this horizon `
      + 'carries no positive weight, so the sampler cannot state a floor)'
  }
  return `${trim(m.lole_hours)} ${unit}`
}

/** The credit as a share of nameplate, or nothing when there is no credit. */
export function elccShare(share: number | null | undefined): string | null {
  if (share == null || !isFinite(share)) return null
  return `${(share * 100).toFixed(1)}%`
}

const STATUS_LABEL: Record<ElccRow['status'], string> = {
  ok: 'ok',
  unidentifiable: 'unidentifiable',
  not_bracketed: 'not_bracketed',
}

// The one sentence that keeps the comparison honest: the three engines are
// not measuring three different things.
const ALIGNMENT_TIP =
  'The rows align on two shared quantities, they are not apples to oranges. '
  + 'ENS (LP proxy) ↔ EUE (COPT / MC): the same unserved megawatt-hours under '
  + 'three engines. shed-hours (LP proxy) ↔ LOLE (COPT / MC): the LP\'s '
  + 'deterministic shed-hour count is the analogue of the probabilistic '
  + 'loss-of-load expectation. What differs is the engine behind each number, '
  + 'not the question asked.'

const COPT_STORAGE_TIP =
  'Structural, not a setting: the COPT is a convolution over independent unit '
  + 'capacity states with no chronology at all, so storage — whose state of '
  + 'charge only exists across hours — has nowhere to live in it. The cell is '
  + 'a dash rather than a "no" because there is no version of this engine that '
  + 'could answer it.'

const NOT_RUN_TIP =
  'not run — this engine has produced no result in the current session, which '
  + 'is a different statement from a result of zero.'

interface EngineRow {
  engine: 'lp_proxy' | 'copt' | 'mc'
  label: string
  metric: string
  /** null = this engine has not produced a result in this session. */
  value: string | null
  fidelity: string
  storage: { text: string; title?: string; structural?: boolean }
  dsr: string
  foresight: string
  basis: string | null
}

/**
 * The three-engine answer in one table (spec §5).
 *
 * The point of the table is the CONTRAST, not the numbers: same system, three
 * engines, and the columns say what each one can and cannot see. A user who
 * reads the MC's LOLE beside the COPT's without the storage-aware column is
 * being invited to conclude the sampler is wrong.
 */
export function EngineComparison({ adequacy, copt, mc }: {
  adequacy: AdequacyReportPayload | null
  copt: CoptPayload | null
  mc: McStatus | null
}) {
  const m = mc?.result?.metrics ?? null
  const mcValue = m
    ? `EUE ${trim(m.eue_mwh)} MWh`
      + (ciRange(m.eue_ci) ? ` (${ciRange(m.eue_ci)})` : '')
      + ` · LOLE ${loleStatement(m)}`
      + (ciRange(m.lole_ci) ? ` (${ciRange(m.lole_ci)})` : '')
    : null

  const rows: EngineRow[] = [
    {
      engine: 'lp_proxy', label: 'LP proxy', metric: 'ENS / shed-hours',
      value: adequacy
        ? `ENS ${trim(adequacy.metrics.ens_mwh)} MWh · shed-hours `
          + `${trim(adequacy.metrics.shed_hours)} h`
        : null,
      fidelity: adequacy?.fidelity ?? 'deterministic_scenario',
      storage: { text: 'yes', title: 'The LP dispatches storage explicitly.' },
      dsr: 'yes — DSR slacks serve demand in the LP',
      foresight: 'perfect (one deterministic realisation)',
      basis: adequacy ? 'modelled horizon' : null,
    },
    {
      engine: 'copt', label: 'COPT', metric: 'EUE / LOLE',
      value: copt
        ? `EUE ${trim(copt.metrics.eue_mwh)} MWh · LOLE `
          + `${trim(copt.metrics.lole_hours)} ${basisSuffix(copt.metrics)}`
        : null,
      fidelity: copt?.fidelity ?? 'analytic_convolution',
      storage: { text: '—', title: COPT_STORAGE_TIP, structural: true },
      dsr: 'not modelled',
      foresight: 'n/a — no chronology to look along',
      basis: copt ? copt.metrics.time_basis : null,
    },
    {
      engine: 'mc', label: 'Sequential MC', metric: 'EUE / LOLE',
      value: mcValue,
      fidelity: mc?.result?.fidelity ?? 'sequential_mc',
      storage: {
        text: 'yes (greedy, chronological)',
        title: 'State of charge evolves hour by hour and is reset at each '
          + 'period boundary.',
      },
      dsr: 'not modelled — excluded as a resource',
      foresight: 'none — greedy, chronological dispatch',
      basis: m ? m.time_basis : null,
    },
  ]

  const cell = (r: EngineRow, key: string, text: string | null, title?: string) => (
    <td
      className="py-1 pr-3 align-top"
      data-testid={`cmp-${key}-${r.engine}`}
      title={text == null ? NOT_RUN_TIP : title}
    >
      {text ?? '—'}
    </td>
  )

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[10px]" data-testid="engine-comparison">
        <thead className="text-muted">
          <tr>
            <th className="text-left font-medium py-1 pr-3">Engine</th>
            <th
              className="text-left font-medium py-1 pr-3"
              data-testid="cmp-metric-header" title={ALIGNMENT_TIP}
            >
              Metric
            </th>
            <th
              className="text-left font-medium py-1 pr-3"
              data-testid="cmp-value-header" title={ALIGNMENT_TIP}
            >
              Value (+CI)
            </th>
            <th className="text-left font-medium py-1 pr-3">Fidelity</th>
            <th className="text-left font-medium py-1 pr-3">Storage-aware?</th>
            <th className="text-left font-medium py-1 pr-3">DSR-aware?</th>
            <th className="text-left font-medium py-1 pr-3">Foresight</th>
            <th className="text-left font-medium py-1 pr-3">Time basis</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.engine} className="border-t border-border/50 align-top"
                data-testid={`cmp-row-${r.engine}`}>
              <td className="py-1 pr-3 font-medium">{r.label}</td>
              <td className="py-1 pr-3" data-testid={`cmp-metric-${r.engine}`}
                  title={ALIGNMENT_TIP}>{r.metric}</td>
              {cell(r, 'value', r.value)}
              <td className="py-1 pr-3 font-mono">{r.fidelity}</td>
              <td
                className="py-1 pr-3"
                data-testid={`cmp-storage-${r.engine}`}
                title={r.storage.title}
              >
                {r.storage.text}
              </td>
              <td className="py-1 pr-3" data-testid={`cmp-dsr-${r.engine}`}>{r.dsr}</td>
              <td className="py-1 pr-3" data-testid={`cmp-foresight-${r.engine}`}>
                {r.foresight}
              </td>
              {cell(r, 'basis', r.basis)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function McPanel() {
  const currentProject = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [draws, setDraws] = useState('500')
  const [blocked, setBlocked] = useState<string | null>(null)

  const { data } = useQuery({
    queryKey: nk(currentProject, 'results', 'mc'),
    queryFn: () => resultsApi.getMc(),
    refetchInterval: (q) =>
      (q.state.data as McStatus | null)?.status === 'running' ? 2000 : false,
  })
  const payload = (data ?? null) as McStatus | null
  const running = payload?.status === 'running'

  // Same query keys LostLoadTab uses, so the comparison table reads the
  // cache the tab already populated rather than issuing its own round-trips.
  const { data: adequacy } = useQuery({
    queryKey: nk(currentProject, 'results', 'adequacy'),
    queryFn: () => resultsApi.getAdequacy(),
  })
  const { data: copt } = useQuery({
    queryKey: nk(currentProject, 'results', 'copt'),
    queryFn: () => resultsApi.getCopt(),
  })

  const run = useMutation({
    // A bare `{}` when the field is empty or unparseable: every field has an
    // engine-side default, and inventing a frontend one would fork it.
    mutationFn: () => {
      const n = Number(draws)
      return resultsApi.startMc(
        draws.trim() !== '' && isFinite(n) && n > 0 ? { draws: n } : {})
    },
    onSuccess: () => {
      setBlocked(null)
      void qc.invalidateQueries({ queryKey: nk(currentProject, 'results', 'mc') })
    },
    // The server's own words, not ours: the detail names WHICH study holds
    // the mesh, which is the only actionable part of the refusal.
    onError: (e: unknown) => setBlocked(blockerMessage(e)),
  })

  const result = payload?.result ?? null
  const m = result?.metrics ?? null
  const warning = result?.warning ?? m?.warning ?? null
  const loleCi = m ? ciRange(m.lole_ci, basisSuffix(m)) : null
  const eueCi = m ? ciRange(m.eue_ci, 'MWh') : null
  const elcc = result?.elcc ?? []

  return (
    <section className="border border-border rounded" data-testid="mc-panel">
      <button
        onClick={() => setOpen(o => !o)}
        data-testid="mc-toggle"
        className="w-full flex items-center gap-2 px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted hover:text-accent"
      >
        <Dices size={11} /> Sequential Monte Carlo {open ? '▾' : '▸'}
      </button>
      {open && (
        <div className="p-3 flex flex-col gap-3">
          <p className="text-[11px] text-muted">
            Chronological two-state sampling of unit outages with storage carried
            through the hours — the question the COPT convolution structurally
            cannot answer. Solves nothing and never mutates the network, so it
            is safe to run beside an editing user. No VoLL required: the metrics
            are hours and megawatt-hours, not euros.
          </p>

          <div className="flex flex-wrap items-center gap-2">
            <label className="text-[10px] text-muted flex items-center gap-1">
              Draws
              <input
                type="number" min={1} value={draws} aria-label="draws"
                onChange={e => setDraws(e.target.value)}
                className="w-20 px-1 py-0.5 border border-border rounded bg-bg text-[10px] font-mono"
              />
            </label>
            <button
              onClick={() => run.mutate()} disabled={running}
              className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent disabled:opacity-50"
              title={blocked ?? undefined}
            >
              {running ? 'Sampling…' : 'Run study'}
            </button>
            {blocked && (
              <span className="text-[10px] text-warn" data-testid="mc-blocked">
                Blocked: {blocked}
              </span>
            )}
            {payload?.error && (
              <span className="text-[10px] text-danger" data-testid="mc-error">
                {payload.error}
              </span>
            )}
          </div>

          {warning && (
            <p
              className="inline-flex items-start gap-1 text-[10px] text-warn border border-warn/40 rounded px-2 py-1"
              data-testid="mc-warning"
            >
              <AlertTriangle size={11} className="mt-[1px] shrink-0" />
              <span>{warning}</span>
            </p>
          )}

          {m && (
            <div className="flex flex-wrap gap-4" data-testid="mc-metrics">
              <div className="flex flex-col">
                <span className="text-[10px] text-muted">LOLE</span>
                <span className="text-[12px] font-mono" data-testid="mc-lole">
                  {loleStatement(m)}
                </span>
                {loleCi && (
                  <span className="text-[10px] text-muted font-mono" data-testid="mc-lole-ci">
                    95% CI {loleCi} · n={m.n_samples}
                  </span>
                )}
              </div>
              <div className="flex flex-col">
                <span className="text-[10px] text-muted">EUE</span>
                <span className="text-[12px] font-mono" data-testid="mc-eue">
                  {trim(m.eue_mwh)} MWh
                </span>
                {eueCi && (
                  <span className="text-[10px] text-muted font-mono" data-testid="mc-eue-ci">
                    95% CI {eueCi} · n={m.n_samples}
                  </span>
                )}
              </div>
              {m.converged === false && (
                <span className="text-[10px] text-warn self-end">
                  draw cap reached before the target coefficient of variation
                </span>
              )}
            </div>
          )}

          <div className="flex flex-col gap-1">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-muted">
              Cross-engine comparison
            </span>
            <EngineComparison
              adequacy={(adequacy ?? null) as AdequacyReportPayload | null}
              copt={(copt ?? null) as CoptPayload | null}
              mc={payload}
            />
          </div>

          {elcc.length > 0 && (
            <div className="flex flex-col gap-1">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                Effective load-carrying capability
              </span>
              <div className="overflow-x-auto">
                <table className="w-full text-[10px]" data-testid="elcc-table">
                  <thead className="text-muted">
                    <tr>
                      <th className="text-left font-medium py-1 pr-3">Asset</th>
                      <th className="text-left font-medium py-1 pr-3">Kind</th>
                      <th className="text-right font-medium py-1 pr-3">Nameplate</th>
                      <th className="text-left font-medium py-1 pr-3">Credit</th>
                      <th className="text-right font-medium py-1 pr-3">Baseline LOLE</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {elcc.map(r => (
                      <tr key={`${r.kind}:${r.name}`} className="border-t border-border/50 align-top"
                          data-testid={`elcc-row-${r.name}`}>
                        <td className="py-1 pr-3">{r.name}</td>
                        <td className="py-1 pr-3 font-sans">{r.kind}</td>
                        <td className="py-1 pr-3 text-right">{trim(r.nameplate_mw)} MW</td>
                        {/* A refusal is DATA. The bisection either resolved a
                            credit or it did not, and when it did not the engine
                            says why — rendering that as an empty cell or a bare
                            dash would delete the only informative half of the
                            answer and leave the user guessing at a bug. */}
                        <td className="py-1 pr-3" data-testid={`elcc-verdict-${r.name}`}>
                          {r.status === 'ok' && r.elcc_mw != null ? (
                            <>
                              {trim(r.elcc_mw)} MW
                              {elccShare(r.elcc_share) && (
                                <span className="text-muted"> ({elccShare(r.elcc_share)})</span>
                              )}
                            </>
                          ) : (
                            <span className="font-sans text-warn">
                              <span className="font-semibold">{STATUS_LABEL[r.status]}</span>
                              {r.reason ? ` — ${r.reason}` : ''}
                            </span>
                          )}
                        </td>
                        <td className="py-1 pr-3 text-right">
                          {trim(r.baseline_lole_h)}
                          <span className="text-muted">
                            {' '}({trim(r.baseline_lole_ci[0])}–{trim(r.baseline_lole_ci[1])})
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-[10px] text-muted" data-testid="elcc-non-additivity">
                Credits are marginal, last-in — each is measured against the same
                baseline with every other asset present, so they do not sum to a
                fleet credit. Adding two 300 MW credits does not buy 600 MW of
                firm capacity.
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
