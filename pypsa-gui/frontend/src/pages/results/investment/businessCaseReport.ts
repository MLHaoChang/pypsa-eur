// The business-case report in words (spec §7; issue 10): the P&L rows in order, why a
// row or KPI is not established, and the stream groups. Pure, so the tests pin it.
//
// GK B F1: every total shown is a `pnl.rows` value; nothing here sums streams.
// GK B F3: there is no Interest line; net income is before financing.
// GK B F4: a None row shows its reason, never 0.
import type {
  BusinessCaseKpi, BusinessCasePnl, BusinessCaseStudy, YearSeries,
} from '../../../api/businessCase'
import { fmtAmount } from './valueFlows'

const words = (id: string) => id.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())

export interface PnlRowSpec {
  key: string
  label: string
  /** Shown as a % of 1 (the margin), else an amount. */
  pct?: boolean
  /** A total line (bold). */
  total?: boolean
  /** Hidden only when every year is exactly 0 (F4: terminal). */
  hideWhenZero?: boolean
}

export interface PnlSection { title: string; note?: string; rows: PnlRowSpec[] }

/** The P&L and the cash bridge, in reading order. Gross and net of pass-through are
 *  separate labelled lines, never one total. */
export const PNL_SECTIONS: PnlSection[] = [
  {
    title: 'Profit and loss',
    rows: [
      { key: 'revenue', label: 'Revenue', total: true },
      { key: 'opex', label: 'Operating cost', total: true },
      { key: 'ebitda', label: 'EBITDA', total: true },
      { key: 'pass_through', label: 'Pass-through (in revenue)' },
      { key: 'revenue_net_of_pass_through', label: 'Revenue net of pass-through' },
      { key: 'ebitda_net_of_pass_through', label: 'EBITDA net of pass-through' },
      { key: 'ebitda_margin', label: 'EBITDA margin (gross)', pct: true },
      { key: 'terminal', label: 'Terminal value', hideWhenZero: true },
      { key: 'depreciation', label: 'Depreciation and amortisation' },
      { key: 'ebit', label: 'EBIT', total: true },
      { key: 'tax', label: 'Tax (project, unlevered)' },
      { key: 'net_income', label: 'Net income', total: true },
    ],
  },
  {
    title: 'Project cash',
    rows: [
      { key: 'capex', label: 'Capital cost' },
      { key: 'stage_capex', label: 'Staged capital cost' },
      { key: 'replacement', label: 'Replacements' },
      { key: 'grant', label: 'Grants' },
      { key: 'credits', label: 'Tax credits' },
      { key: 'project_pre_tax', label: 'Project cash before tax', total: true },
      { key: 'project_post_tax', label: 'Project cash after tax', total: true },
      { key: 'working_capital_change', label: 'Working capital change' },
      { key: 'project_after_working_capital', label: 'Project cash after working capital', total: true },
    ],
  },
  {
    title: 'Equity cash',
    note: 'Debt service is shown as the debt section serves it.',
    rows: [
      { key: 'fees', label: 'Debt fees' },
      { key: 'debt_service', label: 'Debt service' },
      { key: 'draws', label: 'Debt draws' },
      { key: 'reserve_interest', label: 'Reserve interest' },
      { key: 'dsra_funding', label: 'Debt service reserve funding' },
      { key: 'tax_equity', label: 'Tax (equity, levered)' },
      { key: 'equity_pre_tax', label: 'Equity cash before tax', total: true },
      { key: 'equity_post_tax', label: 'Equity cash after tax', total: true },
      { key: 'equity_after_working_capital', label: 'Equity cash after working capital', total: true },
    ],
  },
]

export const NET_INCOME_NOTE = 'Net income is before financing (unlevered).'

/** Why a None row is not established, from what the engine needs for it. */
const ROW_NEEDS: Record<string, string> = {
  revenue: 'the operating section is not established',
  opex: 'the operating section is not established',
  ebitda: 'the operating section is not established',
  revenue_net_of_pass_through: 'needs revenue',
  ebitda_net_of_pass_through: 'needs EBITDA',
  ebitda_margin: 'needs EBITDA',
  terminal: 'the terminal value is not established',
  depreciation: 'the tax section is not established',
  ebit: 'needs EBITDA, the terminal value and depreciation',
  tax: 'the tax section is not established',
  tax_equity: 'the levered tax is not established',
  net_income: 'needs EBIT and tax',
  project_pre_tax: 'needs EBITDA, the terminal value and the capital rows',
  project_post_tax: 'needs project cash before tax, tax and credits',
  equity_pre_tax: 'the debt section is not established',
  equity_post_tax: 'needs equity cash before tax and the levered tax',
  working_capital_change: 'no receivable days are set',
  project_after_working_capital: 'needs project cash after tax and working capital',
  equity_after_working_capital: 'needs equity cash after tax and working capital',
}

export function rowReason(key: string, pnl: BusinessCasePnl): string {
  if ((pnl.not_finite ?? []).includes(key)) return 'Not established: the figure is out of range'
  return `Not established: ${ROW_NEEDS[key] ?? 'not served by this report'}`
}

export function series(pnl: BusinessCasePnl, key: string): YearSeries {
  const v = pnl.rows[key]
  return Array.isArray(v) ? v : null
}

/** F4: hidden only when every year is exactly 0; a None terminal shows its reason. */
export function hidden(spec: PnlRowSpec, s: YearSeries): boolean {
  return !!spec.hideWhenZero && Array.isArray(s) && s.length > 0 && s.every(v => v === 0)
}

/** A year's figure; null for a year with none (the view shows "n/a" and the reason).
 *  Amounts of 1,000 or more read as whole numbers (UX nit on BC 10). */
export function fmtYear(v: number | null, pct = false): string | null {
  if (v === null || !Number.isFinite(v)) return null
  if (pct) return `${(v * 100).toFixed(1)} %`
  return Math.abs(v) >= 1000
    ? v.toLocaleString(undefined, { maximumFractionDigits: 0 })
    : fmtAmount(v)
}

/** Why one year of an otherwise established row has no figure. */
export function yearReason(key: string): string {
  return key === 'ebitda_margin' ? 'there is no revenue this year' : 'this year is not established'
}

// ── streams (F1, F2) ────────────────────────────────────────────────────────

export interface StreamGroup { id: string; title: string; streams: string[] }

const SOURCE_TITLE: Record<string, string> = {
  'line:revenue': 'Revenue lines',
  'line:opex': 'Operating-cost lines',
  'line:capex': 'Capital-cost lines',
  energy_model: 'From the energy model',
}
const SOURCE_ORDER = ['line:revenue', 'energy_model', 'line:opex', 'line:capex']

/** The streams grouped by where they come from, no subtotal (F1). A report without
 *  `stream_source` puts every stream under "Other", never a guessed source (F2). */
export function streamGroups(pnl: BusinessCasePnl): StreamGroup[] {
  const names = Object.keys(pnl.streams).sort()
  const src = pnl.stream_source
  if (!src) return names.length ? [{ id: 'other', title: 'Other', streams: names }] : []
  const by = new Map<string, string[]>()
  for (const n of names) {
    const g = src[n] ?? 'other'
    by.set(g, [...(by.get(g) ?? []), n])
  }
  const order = [...SOURCE_ORDER, ...[...by.keys()].filter(k => !SOURCE_ORDER.includes(k)).sort()]
  return order.filter(k => by.has(k)).map(k => ({
    id: k, title: SOURCE_TITLE[k] ?? 'Other', streams: by.get(k)!,
  }))
}

// ── KPIs ───────────────────────────────────────────────────────────────────

export const KPI_ORDER: { key: string; label: string; fmt: 'pct' | 'amount' | 'years' | 'ratio' }[] = [
  { key: 'project_npv', label: 'Project NPV', fmt: 'amount' },
  { key: 'project_irr', label: 'Project IRR', fmt: 'pct' },
  { key: 'equity_npv', label: 'Equity NPV', fmt: 'amount' },
  { key: 'equity_irr', label: 'Equity IRR', fmt: 'pct' },
  { key: 'payback_years', label: 'Payback', fmt: 'years' },
  { key: 'min_dscr', label: 'Minimum DSCR', fmt: 'ratio' },
  { key: 'avg_dscr', label: 'Average DSCR', fmt: 'ratio' },
  { key: 'ebitda_margin_gross', label: 'EBITDA margin (gross)', fmt: 'pct' },
  { key: 'ebitda_margin_net', label: 'EBITDA margin (net of pass-through)', fmt: 'pct' },
  { key: 'revenue_per_capacity_gross', label: 'Revenue per capacity (gross)', fmt: 'amount' },
  { key: 'revenue_per_capacity_net', label: 'Revenue per capacity (net of pass-through)', fmt: 'amount' },
]

const KPI_REASON: Record<string, string> = {
  no_revenue: 'there is no revenue',
  capacity_not_stated: 'the capacity is not stated',
  never_recovers: 'the cash never recovers the investment',
  no_debt: 'there is no debt',
  not_finite: 'the figure is out of range',
  wacc_nominal_missing: 'the WACC is not set',
  cost_of_equity_missing: 'the cost of equity is not set',
  irr_not_established: 'the cash flows have no IRR',
  not_established: 'an input section is not established',
  investment_case_missing: 'there is no current investment case',
  current_assumptions_not_established: 'the current inputs cannot be read now',
}

/** "operating_not_established: x, y" → "the operating section is not established (x, y)". */
export function reasonWords(reason: string | null | undefined): string {
  if (!reason) return 'no reason given'
  if (KPI_REASON[reason]) return KPI_REASON[reason]
  const m = /^([a-z]+)_not_established(?::\s*(.*))?$/.exec(reason)
  if (m) return `the ${m[1]} section is not established${m[2] ? ` (${m[2]})` : ''}`
  return words(reason).toLowerCase()
}

/** A KPI in words (UX 6): never 0 for a missing value. */
export function kpiText(k: BusinessCaseKpi | undefined, fmt: (typeof KPI_ORDER)[number]['fmt']): string {
  if (!k) return 'Not established: not in this report'
  if (k.status === 'not_established') return `Not established: ${reasonWords(k.reason)}`
  if (k.status === 'n/a' || k.value === null) return `n/a: ${reasonWords(k.reason)}`
  const v = k.value
  switch (fmt) {
    case 'pct': return `${(v * 100).toFixed(2)} %`
    case 'years': return `${v.toFixed(1)} years`
    case 'ratio': return `${v.toFixed(2)}×`
    default: return k.unit ? `${fmtAmount(v)} ${k.unit}` : fmtAmount(v)
  }
}

// ── the run ────────────────────────────────────────────────────────────────

const STAGE_WORDS: Record<string, string> = {
  queued: 'Waiting to start',
  rebuild_ic_case: 'Rebuilding the investment case',
  read_outputs: 'Reading the linked outputs',
  build: 'Building the business case',
  run_engine: 'Running the finance engine',
  break_even: 'Finding the break-even price',
  store: 'Storing the report',
}

/** Stage words only (UX 8): the clock sits outside the status region. */
export function stageText(s: BusinessCaseStudy | null): string {
  const stage = s?.stage ?? 'queued'
  const n = s?.stages?.length
  const i = s?.stages?.indexOf(stage) ?? -1
  const w = STAGE_WORDS[stage] ?? words(stage)
  return n && i >= 0 ? `${w} (step ${i + 1} of ${n})` : w
}

const BREAK_EVEN_REASON: Record<string, string> = {
  break_even_line_not_revenue: 'the chosen line is not a revenue line',
  break_even_line_not_established: 'the chosen line has no established price',
  break_even_not_converged: 'the search did not converge',
  break_even_not_bracketed: 'no price in the searched range breaks even',
}

export function breakEvenReason(status: string): string {
  return BREAK_EVEN_REASON[status] ?? words(status).toLowerCase()
}

const STALE_PART: Record<string, string> = {
  business_case_inputs: 'the business-case lines',
  investment_case: 'the investment case',
}

/** One out-of-date line (UX 7), or null when the report is current. */
export function staleText(s: BusinessCaseStudy | null): string | null {
  const st = s?.staleness
  if (!s?.report || !st) return null
  if (st.stale === null) return `Unconfirmed: ${reasonWords(st.reason ?? null)}.`
  if (!st.stale) return null
  const parts = (st.changed ?? []).map(c => STALE_PART[c] ?? words(c).toLowerCase())
  return `Out of date: ${parts.length ? `${parts.join(' and ')} changed` : reasonWords(st.reason ?? null)}`
    + ' since this report was computed. Run the business case again for current figures.'
}
