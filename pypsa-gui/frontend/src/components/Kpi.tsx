// The one KPI tile (UX overhaul S1, docs/superpowers/plans/2026-10-14-ux-overhaul.md
// §3 "KPI card standard"). Anatomy, top to bottom: a sentence-case label (at
// most two lines), the value with its unit on the same unbreakable line, an
// optional semantic delta, and a short basis line saying what the figure
// covers. A headline tile is set apart by fill and weight, never by an accent
// rail; a delta takes a semantic colour, never the accent. Labels wrap rather
// than clip; keep them short enough for two lines at 170 px.
//
// `value` may arrive pre-formatted with its unit ("136.64 €/MWh"); without an
// explicit `unit` the tile splits it (utils/format.ts::splitValueUnit) so the
// unit renders smaller and can never wrap under the number.
// The figure is then rounded to the tile standard (utils/format.ts::kpiFigure:
// 3 significant figures, one decimal for prices, % and h/yr).
//
// `state` (components/ResultState.tsx) replaces the figure with a pill, a
// one-sentence `reason` and an optional `action` whenever there is no
// computed value — the tile never shows 0 or "—" for a result that was not
// run, not modelled or does not apply. `stale` keeps the old figure, dimmed.
// `fromStudy` keeps a figure the reliability study produced and marks it "From
// the study" while the project has no solve of its own (owner decision G5);
// `study_only` stays for a figure that does not exist without that solve.
import { kpiFigure, splitValueUnit } from '../utils/format'
import { StateAction, StatePill, type ResultAction, type ResultStateKind } from './ResultState'

export type KpiTone = 'good' | 'warn' | 'bad'

const TONE: Record<KpiTone, string> = {
  good: 'text-success',
  warn: 'text-warn',
  bad: 'text-danger',
}

export default function Kpi({
  label, value, unit, basis, hint, delta, deltaTone, headline, testId,
  state = 'ready', reason, action, fromStudy,
}: {
  label: string
  value: string
  unit?: string
  /** What the figure covers: "annualised · whole horizon", "vs 3 h/yr goal". */
  basis?: string
  /** Longer explanation (a formula, a caveat), shown on hover. */
  hint?: string
  delta?: string
  deltaTone?: KpiTone
  headline?: boolean
  testId?: string
  state?: ResultStateKind
  /** One plain sentence saying why, for any state but ready / zero. */
  reason?: string
  action?: ResultAction
  /** The figure is the study's; the project itself is not solved (G5). Keeps the figure, adds the pill. */
  fromStudy?: boolean
}) {
  const raw = unit !== undefined ? { value, unit } : splitValueUnit(value)
  const parts = kpiFigure(raw.value, raw.unit)
  const showsFigure = state === 'ready' || state === 'zero' || state === 'stale'
  return (
    <div
      data-testid={testId}
      data-headline={headline ? 'true' : undefined}
      data-state={state}
      title={hint}
      className={`@container flex h-full min-w-0 flex-col gap-1 rounded-[8px] border px-3 py-2.5
        ${headline ? 'border-border-2 bg-panel-2' : 'border-border bg-bg-2'}`}
    >
      <div className="text-[12px] leading-snug text-muted [overflow-wrap:anywhere]">{label}</div>
      {showsFigure ? (
        <div
          data-kpi-value={parts.unit ? `${parts.value} ${parts.unit}` : parts.value}
          className={`flex items-baseline gap-1 whitespace-nowrap ${state === 'stale' ? 'opacity-60' : ''}`}
        >
          <span
            className={`font-semibold leading-none tabular-nums text-text
              ${headline ? 'text-[24px] @max-[170px]:text-[19px]' : 'text-[22px] @max-[170px]:text-[18px]'}`}
          >
            {parts.value}
          </span>
          {parts.unit && <span className="text-[13px] text-muted">{parts.unit}</span>}
        </div>
      ) : null}
      {state !== 'ready' && state !== 'zero' && <StatePill state={state} />}
      {fromStudy && showsFigure && <StatePill state="study_only" />}
      {delta && showsFigure && (
        <div className={`text-[11px] font-medium tabular-nums ${deltaTone ? TONE[deltaTone] : 'text-muted'}`}>
          {delta}
        </div>
      )}
      {reason && state !== 'ready' && state !== 'zero' && (
        <div className="text-[11.5px] leading-snug text-muted">{reason}</div>
      )}
      {basis && showsFigure && <div className="mt-auto text-[11px] leading-snug text-muted">{basis}</div>}
      {action && state !== 'ready' && state !== 'zero' && (
        <div className="mt-auto"><StateAction action={action} /></div>
      )}
    </div>
  )
}
