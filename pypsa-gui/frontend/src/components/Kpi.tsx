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
import { kpiFigure, splitValueUnit } from '../utils/format'

export type KpiTone = 'good' | 'warn' | 'bad'

const TONE: Record<KpiTone, string> = {
  good: 'text-success',
  warn: 'text-warn',
  bad: 'text-danger',
}

export default function Kpi({
  label, value, unit, basis, hint, delta, deltaTone, headline, testId,
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
}) {
  const raw = unit !== undefined ? { value, unit } : splitValueUnit(value)
  const parts = kpiFigure(raw.value, raw.unit)
  return (
    <div
      data-testid={testId}
      data-headline={headline ? 'true' : undefined}
      title={hint}
      className={`@container flex h-full min-w-0 flex-col gap-1 rounded-[8px] border px-3 py-2.5
        ${headline ? 'border-border-2 bg-panel-2' : 'border-border bg-bg-2'}`}
    >
      <div className="text-[12px] leading-snug text-muted [overflow-wrap:anywhere]">{label}</div>
      <div
        data-kpi-value={parts.unit ? `${parts.value} ${parts.unit}` : parts.value}
        className="flex items-baseline gap-1 whitespace-nowrap"
      >
        <span
          className={`font-semibold leading-none tabular-nums text-text
            ${headline ? 'text-[24px] @max-[170px]:text-[19px]' : 'text-[22px] @max-[170px]:text-[18px]'}`}
        >
          {parts.value}
        </span>
        {parts.unit && <span className="text-[13px] text-muted">{parts.unit}</span>}
      </div>
      {delta && (
        <div className={`text-[11px] font-medium tabular-nums ${deltaTone ? TONE[deltaTone] : 'text-muted'}`}>
          {delta}
        </div>
      )}
      {basis && <div className="mt-auto text-[11px] leading-snug text-muted">{basis}</div>}
    </div>
  )
}
