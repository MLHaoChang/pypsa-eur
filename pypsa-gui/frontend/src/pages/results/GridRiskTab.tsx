// Results › Reliability › Grid risk to production (grid-to-process ticket 13), fed by
// `GET /results/grid_risk_production`: each grid risk (ride-through, island, N-1, connection curtailment) in
// t/yr, then EUR/yr, per period. One tile group in both modes; Expert adds one table with the rows, the basis,
// the flags in words and the source file with its stamp. A missing input reads "Missing: <form word> ›",
// never 0; EUR without a lost-production value reads "Unavailable: no lost-production value ›". The total is
// an upper bound: the lines may overlap. The per-unit breakdown stays in the campus results (12b-3).
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import type { FlowTarget } from '../../api/flow'
import type { GridRiskCell, GridRiskLine, GridRiskLineKey, GridRiskProduction, GridRiskTarget } from '../../api/types'
import { PageSection, Tag } from '../../components/PageKit'
import { StatePill } from '../../components/ResultState'
import { openFlowTarget, openFlowTargetInPlace, showResultsTab } from '../../layout/flowNavigation'
import { useUIStore } from '../../store/uiStore'
import { FIELD_LABELS } from '../../utils/fieldLabels'
import { money } from '../../utils/money'
import { nk } from '../../utils/queryKeys'
import { DIPS_TITLE, count } from '../CampusDipsRestartSection'
import { Seg } from './shared'

export const GRID_RISK_TITLE = 'Grid risk to production'
const NBSP = ' '

/** The places a missing input can be that are not a form field, in the app's words. */
const PLACE_WORDS: Record<string, string> = {
  campus_run: 'campus study run',
  site_transformer: 'Site transformer',
  grid_import: 'Grid connection',
  run: 'Run',
}

/** The form's own word for a missing input: its field label, else the place's name. */
export function missingWord(word: string | null): string {
  if (!word) return 'input'
  return FIELD_LABELS[word] ?? PLACE_WORDS[word] ?? word.replace(/_/g, ' ')
}

/** Where each line comes from, in Guided's words (Expert names the file). */
export const SOURCE_WORDS: Record<GridRiskLineKey, string> = {
  ride_through: 'from the campus study',
  island: 'from the campus study',
  n_1: 'from the N-1 screen',
  connection: 'from the solve',
}

const FLAG_WORDS: Record<string, string> = {
  no_inventory_credit: 'no inventory credit, so it is conservative',
  lower_bound: 'some units are not known, so this is a lower bound',
  stale: 'out of date',
  lost_production_value_missing: 'no lost-production value',
  several_shortfall_products: 'several products, so the EUR cannot be given one value',
  outage_longer_than_period: 'the outage is longer than the period, so this is a lower bound',
  feedstock_inventory_not_credited: 'feedstock tanks are not credited',
  mttr_missing: 'no mean time to repair',
  lost_production_capture_stale: 'the network changed since the solve',
  role_from_name: 'the shortfall is read from its name',
}

/** A line that rests on demo dip statistics (GK A 12b-3: the demo label stays visible). */
export function isDemo(line: GridRiskLine): boolean {
  return line.flags.includes('dip_stats_source:demo')
}

/** The Expert table's status, in the tile's words (never a raw key). */
export function statusWords(line: GridRiskLine): string {
  if (line.stale) return 'out of date'
  return line.status === 'missing' ? 'missing' : line.status === 'partial' ? 'partial' : 'complete'
}

/** A flag in plain words, never a key. */
export function flagWords(flag: string): string {
  if (flag.startsWith('dip_stats_source:')) {
    const src = flag.split(':')[1]
    return src === 'demo' ? 'demo dip statistics' : src === 'user' ? "the operator's dip statistics" : 'dip statistics of unknown source'
  }
  if (flag.startsWith('tier_unset:')) return `no criticality tier on ${flag.split(':')[1]}`
  return FLAG_WORDS[flag] ?? flag.replace(/_/g, ' ')
}

/** Tonnes per year: "1.2 kt/yr", "350 t/yr", with non-breaking spaces. */
export function fmtTonnesYear(t: number): string {
  const a = Math.abs(t)
  const [v, u] = a >= 1e6 ? [t / 1e6, 'Mt'] : a >= 1e3 ? [t / 1e3, 'kt'] : [t, 't']
  return `${Number(v.toPrecision(3))}${NBSP}${u}/yr`
}

/** Euros in the shared `money()` notation (UX-RULES-1107). */
export const moneyForGridRisk = (eur: number) => money(eur, 'EUR')

function toFlow(t: GridRiskTarget): FlowTarget {
  return { ...t, section: t.section as FlowTarget['section'] }
}

function GoLink({ target, label, testId, mode }: {
  target: GridRiskTarget | null; label: string; testId: string; mode: 'guided' | 'expert'
}) {
  if (!target) return <span className="text-[11px] text-muted">{label}</span>
  const t = toFlow(target)
  return (
    <button type="button" data-testid={testId}
      onClick={() => (mode === 'guided' ? openFlowTargetInPlace(t)
        // Expert: the campus results are a Results tab. `openFlowTarget` would route through the campusResults alias,
        // whose own panel switch drops the Back; `showResultsTab` keeps it (the Control tabs' links).
        : t.panel === 'campusResults' ? showResultsTab('grid_code') : openFlowTarget(t))}
      className="self-start text-[11px] text-accent hover:underline whitespace-nowrap">
      {label} ›
    </button>
  )
}

const CAMPUS_LINK: GridRiskTarget = { section: 'results', panel: 'campusResults', component: null, entity: null, field: null }

function EurLine({ cell, eurTarget, testId, mode }: {
  cell: GridRiskCell | undefined; eurTarget: GridRiskTarget | null; testId: string; mode: 'guided' | 'expert'
}) {
  if (!cell || cell.t_per_year == null) return null
  if (cell.eur_per_year != null) {
    return <span data-testid={testId} className="text-[12px] text-text tabular-nums">{moneyForGridRisk(cell.eur_per_year)}/yr</span>
  }
  const several = cell.eur_flag === 'several_shortfall_products'
  return (
    <span data-testid={testId} className="inline-flex flex-wrap items-baseline gap-1 text-[11px] text-muted">
      Unavailable:
      {several
        ? <span>several products, so the EUR cannot be given one value</span>
        : <GoLink target={eurTarget} label="no lost-production value" testId={`${testId}-fix`} mode={mode} />}
    </span>
  )
}

function LineTile({ line, period, eurTarget, mode }: {
  line: GridRiskLine; period: string; eurTarget: GridRiskTarget | null; mode: 'guided' | 'expert'
}) {
  const cell = line.by_period[period]
  const id = `grid-risk-${line.key}`
  return (
    <div data-testid={id} className="flex flex-col gap-1 rounded-[8px] border border-border bg-bg px-3 py-2 min-w-[11rem]">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold text-text">{line.label}</span>
        {line.stale && <span data-testid={`${id}-stale`}><StatePill state="stale" /></span>}
      </div>
      {line.status === 'missing' || !cell || cell.t_per_year == null ? (
        <div className="flex flex-col gap-0.5">
          {(line.missing_inputs.length ? line.missing_inputs : [{ word: null, target: null }]).map((m, i) => (
            <span key={i} data-testid={`${id}-missing`} className="inline-flex flex-wrap items-baseline gap-1 text-[11px] text-muted">
              Missing:
              <GoLink target={m.target} label={missingWord(m.word)} testId={`${id}-missing-${i}`} mode={mode} />
            </span>
          ))}
        </div>
      ) : (
        <>
          <span data-testid={`${id}-t`} className="text-[16px] font-semibold text-text tabular-nums">
            {fmtTonnesYear(cell.t_per_year)}
          </span>
          <EurLine cell={cell} eurTarget={eurTarget} testId={`${id}-eur`} mode={mode} />
          {line.status === 'partial' && (
            <span className="inline-flex flex-wrap items-baseline gap-1.5">
              <Tag tone="warn">lower bound</Tag>
              <span className="text-[11px] text-muted">some units are not known</span>
            </span>
          )}
        </>
      )}
      <span className="text-[11px] text-muted">{SOURCE_WORDS[line.key]}</span>
      {isDemo(line) && <span data-testid={`${id}-demo`}><Tag tone="warn">demo</Tag></span>}
      {/* One link per destination: a missing line's "Missing: campus study run ›" already goes there. */}
      {(line.key === 'ride_through' || line.key === 'island') && line.status !== 'missing' && (
        <GoLink target={CAMPUS_LINK} label={DIPS_TITLE} testId={`${id}-dips`} mode={mode} />
      )}
    </div>
  )
}

function TotalTile({ data, period, eurTarget, mode }: {
  data: GridRiskProduction; period: string; eurTarget: GridRiskTarget | null; mode: 'guided' | 'expert'
}) {
  const total = data.total!
  const cell = total.by_period[period]
  const missing = (data.lines ?? []).filter(l => l.status === 'missing').length
  return (
    <div data-testid="grid-risk-total" className="flex flex-col gap-1 rounded-[8px] border border-border bg-bg-2 px-3 py-2 min-w-[11rem]">
      <span className="text-[11px] font-semibold text-text">Total</span>
      <span data-testid="grid-risk-total-t" className="text-[16px] font-semibold text-text tabular-nums">
        {cell && cell.t_per_year != null ? fmtTonnesYear(cell.t_per_year) : '—'}
      </span>
      <EurLine cell={cell} eurTarget={eurTarget} testId="grid-risk-total-eur" mode={mode} />
      <span data-testid="grid-risk-total-bound" className="inline-flex flex-wrap items-baseline gap-1.5">
        <Tag tone="warn">upper bound</Tag>
        <span className="text-[11px] text-muted">the lines may overlap</span>
      </span>
      {(data.lines ?? []).some(l => l.status !== 'missing' && isDemo(l)) && (
        <span data-testid="grid-risk-total-demo" className="inline-flex flex-wrap items-baseline gap-1.5">
          <Tag tone="warn">demo</Tag>
          <span className="text-[11px] text-muted">includes demo dip statistics</span>
        </span>
      )}
      {total.incomplete && (
        <span data-testid="grid-risk-total-incomplete" className="inline-flex flex-wrap items-baseline gap-1.5">
          <Tag tone="warn">incomplete</Tag>
          <span className="text-[11px] text-muted">{count(missing, 'line is', 'lines are')} missing</span>
        </span>
      )}
    </div>
  )
}

function cellText(c: Partial<GridRiskCell> | undefined): [string, string] {
  if (!c || c.t_per_year == null) return ['—', '—']
  return [fmtTonnesYear(c.t_per_year), c.eur_per_year != null ? `${moneyForGridRisk(c.eur_per_year)}/yr` : 'Unavailable']
}

function stampWords(stamp: Record<string, string>): string {
  const parts = []
  if (stamp.run_at) parts.push(`run ${stamp.run_at.replace('T', ' ').replace('+00:00', ' UTC')}`)
  if (stamp.solved_at) parts.push(`solved ${stamp.solved_at.replace('T', ' ')}`)
  if (stamp.network_sha256) parts.push(`network ${stamp.network_sha256}`)
  if (stamp.revision) parts.push(`revision ${stamp.revision}`)
  return parts.join(', ')
}

/** A sub-row's flags, once each. A bound from an outage longer than the period is not one from unknown units, so
 *  the generic lower-bound words are left out beside it (the data keeps both). */
export function subRowFlags(flags: string[]): string[] {
  const out = [...new Set(flags)]
  return out.includes('outage_longer_than_period') ? out.filter(f => f !== 'lower_bound') : out
}

function ExpertTable({ lines, period }: { lines: GridRiskLine[]; period: string }) {
  const th = 'px-2 py-1 text-left text-[10.5px] font-semibold text-muted whitespace-nowrap'
  const td = 'px-2 py-1 align-top text-[11px] text-text'
  const rows: Array<{ key: string; line: GridRiskLine; label: string; cell: Partial<GridRiskCell> | undefined; flags: string[]; sub: boolean }> = []
  for (const line of lines) {
    rows.push({ key: line.key, line, label: line.label, cell: line.by_period[period], flags: line.flags, sub: false })
    // The island's two rows, and one row per N-1 site transformer, in the backend's order; never under a missing line.
    if ((line.key === 'island' || line.key === 'n_1') && line.status !== 'missing') {
      for (const r of line.rows) {
        const cell = r.by_period[period]
        const periodFlags = Array.isArray(cell?.flags) ? (cell.flags as string[]) : []
        rows.push({ key: `${line.key}-${r.key}`, line, label: r.label, cell: cell as Partial<GridRiskCell> | undefined,
          flags: subRowFlags([...r.flags, ...periodFlags]), sub: true })
      }
    }
  }
  return (
    <div className="overflow-x-auto">
      <table data-testid="grid-risk-table" className="w-full border-collapse">
        <thead>
          <tr className="border-b border-border">
            <th className={th}>Line</th><th className={th}>t/yr</th><th className={th}>EUR/yr</th>
            <th className={th}>Status</th><th className={th}>Basis</th><th className={th}>Notes</th><th className={th}>Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ key, line, label, cell, flags, sub }) => {
            const [t, eur] = cellText(cell)
            return (
              <tr key={key} data-testid={`grid-risk-row-${key}`} className="border-b border-border/60">
                <td className={`${td} ${sub ? 'pl-5 text-muted' : 'font-medium'}`}>{label}</td>
                <td className={`${td} tabular-nums whitespace-nowrap`}>{t}</td>
                <td className={`${td} tabular-nums whitespace-nowrap`}>{eur}</td>
                <td className={td}>{sub ? '' : statusWords(line)}</td>
                <td className={`${td} max-w-[22rem] text-muted`}>{sub ? '' : (line.basis || line.missing.join('; '))}</td>
                <td className={`${td} text-muted`}>{flags.filter(f => f !== 'stale').map(flagWords).join('; ')}</td>
                <td className={`${td} text-muted`}>
                  {sub ? '' : <>{line.source_files.join(', ')}{Object.keys(line.stamp).length > 0 &&
                    <span className="block">{stampWords(line.stamp)}</span>}</>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** The EUR input to link when the value is missing: the first product whose value is not set. */
function eurTargetOf(data: GridRiskProduction): GridRiskTarget | null {
  const inputs = data.eur_inputs ?? []
  return (inputs.find(i => !i.set) ?? inputs[0])?.target ?? null
}

export function GridRiskBody({ data, mode }: { data: GridRiskProduction; mode: 'guided' | 'expert' }) {
  const periods = data.periods ?? ['_']
  const [picked, setPicked] = useState<string>(periods[0])
  const period = periods.includes(picked) ? picked : periods[0]
  const eurTarget = eurTargetOf(data)
  const lines = data.lines ?? []
  return (
    <div className="flex flex-col gap-3">
      {periods.length > 1 && (
        <div data-testid="grid-risk-period" className="flex items-center gap-2">
          <span className="text-[11px] font-medium text-text">Period</span>
          <Seg value={period} onChange={setPicked} options={periods.map(p => ({ value: p, label: p }))} />
          <span className="text-[11px] text-muted">Showing {period}, per year</span>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {lines.map(l => <LineTile key={l.key} line={l} period={period} eurTarget={eurTarget} mode={mode} />)}
        <TotalTile data={data} period={period} eurTarget={eurTarget} mode={mode} />
      </div>
      {mode === 'expert' && <ExpertTable lines={lines} period={period} />}
    </div>
  )
}

export default function GridRiskTab() {
  const project = useUIStore(s => s.currentProject)
  const mode = useUIStore(s => s.uiMode)
  const { data, isLoading } = useQuery({
    queryKey: nk(project, 'results', 'grid_risk_production'), queryFn: resultsApi.getGridRiskProduction, retry: false,
  })
  return (
    <div className="p-3">
      <PageSection title={GRID_RISK_TITLE}
        hint="Each grid risk as lost tonnes, then EUR, per year. Expected values, not a forecast.">
        <div className="px-4 py-3" data-testid="grid-risk-section">
          {isLoading ? <span className="text-[11px] text-muted">Loading…</span>
            : !data || !data.available || !data.lines
              ? <span data-testid="grid-risk-empty" className="text-[11px] text-muted">
                  {data?.omitted === 'no_process_unit' ? 'The network has no process unit.' : 'Run a solve to see the grid risks.'}
                </span>
              : <GridRiskBody data={data} mode={mode === 'guided' ? 'guided' : 'expert'} />}
        </div>
      </PageSection>
    </div>
  )
}
