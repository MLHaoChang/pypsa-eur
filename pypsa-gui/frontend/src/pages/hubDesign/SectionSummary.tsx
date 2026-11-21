import { useState } from 'react'
import type { FlowHeld, FlowSectionId } from '../../api/flow'
import { ChartFrame, carrierColour } from '../../components/charts/chartTheme'
import { fixedCharts, helperName, holdSummary, isHelper, type FixedChart } from './holdSummary'

// The answer above a Guided section's list (guided-results 03, spec §3): one
// summary line, and in section 5 one small chart of fixed capacity per
// carrier, one chart per unit. Display only (GK B H2): nothing here feeds a
// state, count, verdict, acknowledgement or fingerprint.

const number = (v: number): string => v.toLocaleString('en-GB', { maximumFractionDigits: 2 })
/** More names than this fold behind "(N more) Show all" (the banner-list rule, UX 2). */
export const NAMES_SHOWN = 5

/** A line of names: the first five, then a button that shows the rest. */
function Names({ testId, lead, rows, word = n => n }: {
  testId: string; lead: string; rows: FlowHeld[]; word?: (name: string) => string
}) {
  const [all, setAll] = useState(false)
  const shown = all || rows.length <= NAMES_SHOWN ? rows : rows.slice(0, NAMES_SHOWN)
  const more = rows.length - shown.length
  return (
    <p data-testid={testId} className="text-[11px] text-muted">
      {lead}: {shown.map(h => word(h.entity)).join(', ')}
      {more > 0 && (
        <>
          {' '}
          <button type="button" data-testid={`${testId}-show-all`} onClick={() => setAll(true)}
            className="text-muted underline hover:text-accent">
            ({number(more)} more) Show all
          </button>
        </>
      )}
    </p>
  )
}

function Bars({ chart, capNote }: { chart: FixedChart; capNote: string | null }) {
  const max = Math.max(...chart.bars.map(b => Math.abs(b.value)), 0) || 1
  const text = chart.bars.map(b => `${b.carrier} ${number(b.value)} ${chart.unit}`).join(', ')
  return (
    <figure data-testid="assets-chart" data-unit={chart.unit} className="m-0">
      <ChartFrame title={`Fixed capacity by carrier (${chart.unit})`} hint={capNote ?? undefined} bodyClassName="px-3 py-2">
        <ul aria-hidden="true" className="flex flex-col gap-1">
          {chart.bars.map((b, i) => (
            <li key={b.carrier} className="grid grid-cols-[6rem_1fr_auto] items-center gap-2 text-[11px]">
              <span className="truncate text-muted" title={b.carrier}>{b.carrier}</span>
              <span className="h-2.5 rounded-sm" style={{ width: `${(Math.abs(b.value) / max) * 100}%`,
                background: carrierColour(b.carrier, i) }} />
              <span className="font-mono tabular-nums text-text">{number(b.value)}</span>
            </li>
          ))}
        </ul>
        <p data-testid="assets-chart-axis" aria-hidden="true"
          className="mt-1 flex justify-between pl-[6.5rem] font-mono text-[10px] text-muted">
          <span>0</span><span>{number(max)} {chart.unit}</span>
        </p>
        <p data-testid="assets-chart-text" className="sr-only">Fixed capacity by carrier: {text}.</p>
      </ChartFrame>
    </figure>
  )
}

function AssetsChart({ holds, total }: { holds: FlowHeld[]; total?: number }) {
  const charts = fixedCharts(holds)
  const own = holds.filter(h => !isHelper(h))
  const helpers = holds.filter(isHelper)
  const sized = own.filter(h => h.extendable)
  const anyFixed = own.some(h => !h.extendable && h.value !== null && h.unit)
  const capNote = total !== undefined && total > holds.length
    ? `from the first ${number(holds.length)} of ${number(total)} assets` : null
  return (
    <div className="flex flex-col gap-1.5">
      {charts.length > 0 ? charts.map(c => <Bars key={c.unit} chart={c} capNote={capNote} />) : (
        <p data-testid="assets-no-chart" className="text-[11px] text-muted">
          No chart: {anyFixed ? 'the fixed capacity is all on one carrier.' : 'no asset has a fixed size.'}
        </p>
      )}
      {sized.length > 0 && (
        <Names testId="assets-sized" lead="Sized by the study, not drawn" rows={sized} />
      )}
      {helpers.length > 0 && (
        <Names testId="assets-helpers" lead="Added by the model, left out of the sums and the chart"
          rows={helpers} word={helperName} />
      )}
    </div>
  )
}

export function SectionSummary({ id, holds, total }: { id: FlowSectionId; holds: FlowHeld[]; total?: number }) {
  const line = holdSummary(id, holds, total)
  if (!line) return null
  return (
    <div className="flex flex-col gap-2">
      <p data-testid="section-held-line" className="text-[12px] font-medium text-text">{line}</p>
      {id === 'assets' && <AssetsChart holds={holds} total={total} />}
    </div>
  )
}
