// "Dips and restarts" (grid-to-process issue 12b-3): the campus run's dip-to-trips screen and its restart
// sequence model (gridspine `campus_study.restart_campus`). One table of process units, the same in Guided and
// Expert: trips a year, restart time, the tonnes lost to grid dips, and the motor-start verdict of each unit's
// restart after a grid dip and after an island transition. A unit that reads unknown links to its missing
// field (the form's own word, then ›) through the row-target path, so Back returns here. The timeline events
// are Expert-only. Totals say "incomplete" or "lower bound" with the reason; demo dip statistics say "demo".
// A unit whose restart, ride-through or motor field changed since the run reads out of date.
import type { DipsRestart, DipsRestartField, DipsRestartInputs, DipTripRow, RestartRow } from '../api/campusElectrical'
import type { FlowTarget } from '../api/flow'
import { PageSection, Tag } from '../components/PageKit'
import { StatePill } from '../components/ResultState'
import { VerdictChip } from '../components/gridReadiness/GridReadinessScorecard'
import { openFlowTarget, openFlowTargetInPlace } from '../layout/flowNavigation'
import { useUIStore } from '../store/uiStore'
import { FIELD_LABELS } from '../utils/fieldLabels'
import { TableScroll } from './CampusInvestmentSection'

export const DIPS_TITLE = 'Dips and restarts'

type Transition = 'trip' | 'island'
const TRANSITION_LABEL: Record<Transition, string> = {
  trip: 'Restart after a grid dip', island: 'Restart after islanding',
}

/** The fields a dip verdict needs, and those a restart verdict needs, in the order the link offers them. */
const DIP_FIELDS: DipsRestartField[] = ['ride_through_curve', 'restart_time_h']
const RESTART_FIELDS: DipsRestartField[] = ['restart_time_h', 'motor_kw', 'start_method', 'i_start_pu']

/** The link's label: the form's own word for the field, then ›. */
export function fieldLinkLabel(field: DipsRestartField): string {
  return `${FIELD_LABELS[field]} ›`
}

/** The first of `fields` the run read as not set, or null. */
export function missingField(inputs: DipsRestartInputs | undefined, fields: DipsRestartField[]): DipsRestartField | null {
  if (!inputs) return null
  return fields.find(f => inputs[f] == null || inputs[f] === '') ?? null
}

export function unitTarget(unit: string, field: DipsRestartField): FlowTarget {
  return { section: 'assets', panel: null, component: 'Link', entity: unit, field }
}

/** "1 unit", "2 units". */
export function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

function fig(v: number | null | undefined, digits = 3): string {
  return v == null || !isFinite(v) ? '—' : `${Number(v.toPrecision(digits))}`
}

function figUnit(v: number | null | undefined, unit: string, digits = 3): string {
  const f = fig(v, digits)
  return f === '—' ? f : `${f} ${unit}`
}

function chipOf(verdict: string | null | undefined): 'pass' | 'fail' | 'unknown' {
  return verdict === 'pass' || verdict === 'fail' ? verdict : 'unknown'
}

/** The dip total in words: "incomplete" with why, or nothing to add. */
export function dipTotalReason(total: DipTripRow): string | null {
  const unknown = Number(total.unknown_count ?? 0)
  return total.incomplete
    ? `${count(unknown, 'unit has', 'units have')} no ride-through curve, restart time, rate or mass pull, so the sum leaves ${unknown === 1 ? 'it' : 'them'} out`
    : null
}

/** An engine note in the panel's words: "the PoC" is "the grid connection". */
export function inWords(note: string | null | undefined): string {
  return (note ?? '').replace(/\bthe PoC\b/g, 'the grid connection').replace(/\bPoC\b/g, 'grid connection')
}

/** Why a restart reads unknown when no field of the unit is missing: the note's part that is not about the
 *  unit's own inputs (the island's or the grid connection's short-circuit power), from a capital. */
export function unknownReason(note: string | null | undefined): string | null {
  const parts = inWords(note).split('; ').map(p => p.trim()).filter(Boolean)
  const cause = parts.find(p => !p.startsWith('no ride-through curve') && !p.startsWith('no input')
    && !/^motor start on the (grid connection|island) unknown$/.test(p)) ?? parts[0]
  return cause ? cause[0].toUpperCase() + cause.slice(1) : null
}

/** A restart total in words: "lower bound" with why, or null. The unknown restarts are split by cause: a unit's
 *  own input missing, or no short-circuit power to screen the start on (named from the units' notes). */
export function restartTotalReason(total: RestartRow, units: RestartRow[] = [],
  inputs: Record<string, DipsRestartInputs> = {}): string | null {
  if (!total.lower_bound) return null
  const parts: string[] = []
  const fails = Number(total.fail_count ?? 0)
  const unknown = Number(total.unknown_count ?? 0)
  if (fails) parts.push(`${count(fails, 'motor start fails', 'motor starts fail')} the voltage-change limit`)
  if (unknown) {
    const unknownUnits = units.filter(u => u.verdict === 'unknown')
    const missing = unknownUnits.filter(u => missingField(inputs[u.unit ?? ''], RESTART_FIELDS) != null)
    const other = unknownUnits.filter(u => missingField(inputs[u.unit ?? ''], RESTART_FIELDS) == null)
    const why: string[] = []
    if (missing.length) why.push(`${count(missing.length, 'unit is', 'units are')} missing an input`)
    if (other.length) {
      const cause = unknownReason(other[0].note)
      why.push(`${count(other.length, 'has', 'have')} ${cause ? `no verdict: ${cause[0].toLowerCase()}${cause.slice(1)}` : 'no verdict'}`)
    }
    parts.push(`${count(unknown, 'restart is', 'restarts are')} not known${why.length ? ` (${why.join('; ')})` : ''}`)
  }
  return parts.length ? parts.join('; ') : 'a unit is missing its restart time, rate or mass pull'
}

function FixLink({ unit, field, mode }: { unit: string; field: DipsRestartField; mode: 'guided' | 'expert' }) {
  const target = unitTarget(unit, field)
  return (
    <button type="button" data-testid={`dips-fix-${unit}-${field}`}
      onClick={() => (mode === 'guided' ? openFlowTargetInPlace(target) : openFlowTarget(target))}
      title={`Open ${unit}, where its ${FIELD_LABELS[field].toLowerCase()} is set`}
      className="text-[11px] text-accent hover:underline whitespace-nowrap">
      {fieldLinkLabel(field)}
    </button>
  )
}

function RestartCell({ row, unit, inputs, mode }: {
  row: RestartRow | undefined; unit: string; inputs: DipsRestartInputs | undefined; mode: 'guided' | 'expert'
}) {
  if (!row) return <span className="text-muted">does not restart</span>
  const v = chipOf(row.start_verdict)
  const field = v === 'unknown' ? missingField(inputs, RESTART_FIELDS) : null
  // No field of the unit is missing: the cause is elsewhere (no short-circuit power to screen on), said in words.
  const reason = v === 'unknown' && !field ? unknownReason(row.note) : null
  return (
    <span className="inline-flex flex-col gap-0.5">
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <VerdictChip verdict={v} />
        {row.du_pct != null && <span className="tabular-nums">{figUnit(row.du_pct, '% ΔU')}</span>}
        {field && <FixLink unit={unit} field={field} mode={mode} />}
      </span>
      {reason && <span data-testid={`dips-reason-${row.transition}-${unit}`} className="text-[11px] text-muted max-w-[16rem]">{reason}</span>}
    </span>
  )
}

function TotalLine({ testId, label, value, tag, reason }: {
  testId: string; label: string; value: string; tag: string | null; reason: string | null
}) {
  return (
    <div data-testid={testId} className="flex flex-wrap items-baseline gap-1.5 text-[12px]">
      <span className="text-muted">{label}:</span>
      <span className="text-text tabular-nums">{value}</span>
      {tag && <Tag tone="warn">{tag}</Tag>}
      {reason && <span className="text-[11px] text-muted">{reason}</span>}
    </div>
  )
}

const EVENT_LABEL: Record<string, string> = {
  trip: 'trips', island: 'islanding', pickup: 'picked up', full_rate: 'back at full rate',
}

function Timeline({ transition, events }: { transition: Transition; events: RestartRow[] }) {
  return (
    <div data-testid={`dips-timeline-${transition}`} className="flex flex-col gap-1">
      <div className="text-[11px] font-semibold text-text">Timeline: {TRANSITION_LABEL[transition].toLowerCase()}</div>
      <TableScroll>
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-left text-muted">
              <th className="font-normal pr-3">Time</th><th className="font-normal pr-3">Unit</th>
              <th className="font-normal pr-3">Event</th><th className="font-normal pr-3">Voltage change</th>
              <th className="font-normal pr-3">Verdict</th><th className="font-normal">Note</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e, i) => (
              <tr key={i} data-testid="dips-event" className="border-t border-border">
                <td className="py-1 pr-3 align-top whitespace-nowrap tabular-nums">{figUnit(e.t_s, 's', 6)}</td>
                <td className="pr-3 align-top whitespace-nowrap">{e.unit}</td>
                <td className="pr-3 align-top whitespace-nowrap">{EVENT_LABEL[e.event ?? ''] ?? e.event}</td>
                <td className="pr-3 align-top whitespace-nowrap tabular-nums">{e.du_pct == null ? '—' : figUnit(e.du_pct, '% ΔU')}</td>
                <td className="pr-3 align-top">{e.verdict ? <VerdictChip verdict={chipOf(e.verdict)} /> : '—'}</td>
                <td className="align-top text-[11px] text-muted">{inWords(e.note)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </div>
  )
}

export default function CampusDipsRestartSection({ dips }: { dips: DipsRestart | null | undefined }) {
  const mode = useUIStore(s => s.uiMode)
  if (dips == null) {
    return (
      <PageSection title={DIPS_TITLE}>
        <p data-testid="dips-not-computed" className="px-4 py-3 text-[12px] text-muted">
          Not computed for this run. Rerun the study to add the dip and restart screens.
        </p>
      </PageSection>
    )
  }
  if (dips.error !== undefined) {
    return (
      <PageSection title={DIPS_TITLE}>
        <p data-testid="dips-error" className="px-4 py-3 text-[12px] text-warn">
          The dip and restart screens could not run for this campus: {dips.error}. The sizing and checks above
          still stand.
        </p>
      </PageSection>
    )
  }
  const dipUnits = dips.dip_trips.filter(r => r.level === 'unit')
  const dipTotal = dips.dip_trips.find(r => r.level === 'total')
  const restartOf = (t: Transition) => dips.restart.filter(r => r.transition === t)
  const units = [...new Set([...dipUnits.map(r => r.unit!),
    ...dips.restart.filter(r => r.level === 'unit').map(r => r.unit!)])]
  const outOfDate = new Set(dips.out_of_date)
  const demo = dips.dip_trips.some(r => r.dip_stats_source === 'demo')
  const rowFor = (t: Transition, u: string) => restartOf(t).find(r => r.level === 'unit' && r.unit === u)
  const totalFor = (t: Transition) => restartOf(t).find(r => r.level === 'total')

  return (
    <PageSection title={DIPS_TITLE}
      hint={`${count(units.length, 'process unit', 'process units')} · screening, not dynamics`}>
      <div className="px-4 py-3 flex flex-col gap-2">
        <div className="flex flex-col gap-1">
          {dipTotal && (
            <TotalLine testId="dips-total" label="Lost to grid dips"
              value={figUnit(dipTotal.lost_t_per_year, 't/yr', 4)}
              tag={dipTotal.incomplete ? 'incomplete' : null} reason={dipTotalReason(dipTotal)} />
          )}
          {(['trip', 'island'] as Transition[]).map(t => {
            const tot = totalFor(t)
            return tot ? (
              <TotalLine key={t} testId={`dips-restart-total-${t}`} label={`${TRANSITION_LABEL[t]}, lost per event`}
                value={figUnit(tot.lost_t, 't', 4)} tag={tot.lower_bound ? 'lower bound' : null}
                reason={restartTotalReason(tot, restartOf(t).filter(r => r.level === 'unit'), dips.inputs)} />
            ) : null
          })}
          {demo && (
            <div data-testid="dips-demo" className="flex items-baseline gap-1.5 text-[11px] text-muted">
              <Tag tone="warn">demo</Tag>
              <span>The dip counts are demo statistics, not this site's. Ask the grid operator for this site's dip statistics.</span>
            </div>
          )}
        </div>
        <TableScroll>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-muted">
                <th className="font-normal pr-3">Unit</th><th className="font-normal pr-3">Trips a year</th>
                <th className="font-normal pr-3">Restart time</th><th className="font-normal pr-3">Lost to dips</th>
                <th className="font-normal pr-3">{TRANSITION_LABEL.trip}</th>
                <th className="font-normal">{TRANSITION_LABEL.island}</th>
              </tr>
            </thead>
            <tbody>
              {units.map(u => {
                const d = dipUnits.find(r => r.unit === u)
                const inputs = dips.inputs[u]
                const stale = outOfDate.has(u)
                const dipField = d?.verdict === 'unknown' ? missingField(inputs, DIP_FIELDS) : null
                return (
                  <tr key={u} data-testid={`dips-unit-${u}`} data-stale={stale || undefined}
                    className="border-t border-border">
                    <td className="py-1 pr-3 align-top whitespace-nowrap">
                      <span className="inline-flex items-center gap-1.5">
                        {u}{stale && <span data-testid={`dips-stale-${u}`}><StatePill state="stale" /></span>}
                      </span>
                    </td>
                    <td className={`pr-3 align-top tabular-nums ${stale ? 'opacity-60' : ''}`}>
                      {d?.verdict === 'unknown' ? '—' : fig(d?.trips_per_year)}
                    </td>
                    <td className={`pr-3 align-top tabular-nums whitespace-nowrap ${stale ? 'opacity-60' : ''}`}>
                      {figUnit(d?.restart_time_h, 'h')}
                    </td>
                    <td className={`pr-3 align-top whitespace-nowrap ${stale ? 'opacity-60' : ''}`}>
                      {d?.verdict === 'unknown'
                        ? <span className="inline-flex items-center gap-1.5">
                          <VerdictChip verdict="unknown" />
                          {dipField && <FixLink unit={u} field={dipField} mode={mode} />}
                        </span>
                        : <span className="tabular-nums">{figUnit(d?.lost_t_per_year, 't/yr', 4)}</span>}
                    </td>
                    {(['trip', 'island'] as Transition[]).map(t => (
                      <td key={t} data-testid={`dips-${t}-${u}`}
                        className={`pr-3 align-top ${stale ? 'opacity-60' : ''}`}>
                        <RestartCell row={rowFor(t, u)} unit={u} inputs={inputs} mode={mode} />
                      </td>
                    ))}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </TableScroll>
        {outOfDate.size > 0 && (
          <p data-testid="dips-stale-note" className="text-[11px] text-warn">
            {count(outOfDate.size, 'unit has', 'units have')} a restart, ride-through or motor field changed since
            this run. Run the study again to update {outOfDate.size === 1 ? 'its row' : 'their rows'}.
          </p>
        )}
        {mode === 'expert' && (['trip', 'island'] as Transition[]).map(t => {
          const events = restartOf(t).filter(r => r.level === 'event')
          return events.length ? <Timeline key={t} transition={t} events={events} /> : null
        })}
        <details className="text-[11px] text-muted">
          <summary className="cursor-pointer">Basis: {dips.dip_label}; {dips.restart_label}</summary>
          <ul className="list-disc pl-4 mt-1">
            {dips.basis.map(b => <li key={b}>{b}</li>)}
          </ul>
        </details>
      </div>
    </PageSection>
  )
}
