// The business-case report in words (spec §7; issue 10): the P&L rows in order, why a
// row or KPI is not established, and the stream groups. Pure, so the tests pin it.
//
// GK B F1: every total shown is a `pnl.rows` value; nothing here sums streams.
// GK B F3: there is no Interest line; net income is before financing.
// GK B F4: a None row shows its reason, never 0.
import type {
  BusinessCaseKpi, BusinessCasePnl, BusinessCaseRowReasonItem, BusinessCaseStudy, YearSeries,
} from '../../../api/businessCase'
import { displayName } from '../../../utils/displayName'
import { parseField, refusalWords } from './businessCaseModel'
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

/** What each P&L row is built from (GK B W1): a statement of its inputs, not a diagnosis
 *  of which one is missing. Shown only for a report stored before the report served a
 *  per-row reason (`pnl.row_reason`, BC 17). Its keys equal `businessCasePnlRows.json`,
 *  which a backend test pins to the rows the report serves. */
export const ROW_NEEDS: Record<string, string> = {
  revenue: 'the operating section',
  opex: 'the operating section',
  ebitda: 'the operating section',
  pass_through: 'the operating section',
  revenue_net_of_pass_through: 'revenue',
  ebitda_net_of_pass_through: 'EBITDA',
  ebitda_margin: 'EBITDA and revenue',
  terminal: 'the terminal value',
  depreciation: 'the tax section',
  ebit: 'EBITDA, the terminal value and depreciation',
  tax: 'the tax section',
  tax_equity: 'the levered tax section',
  net_income: 'EBIT and tax',
  capex: 'the operating section',
  stage_capex: 'the operating section',
  replacement: 'the operating section',
  grant: 'the incentives section',
  credits: 'the incentives section',
  fees: 'the debt section',
  debt_service: 'the debt section',
  draws: 'the debt section',
  reserve_interest: 'the debt section',
  dsra_funding: 'the debt section',
  debt_established: 'the debt section',
  project_pre_tax: 'EBITDA, the terminal value and the capital rows',
  project_post_tax: 'project cash before tax, tax and credits',
  equity_pre_tax: 'project cash before tax and the debt section',
  equity_post_tax: 'equity cash before tax, the levered tax and credits',
  working_capital_change: 'revenue and receivable days',
  project_after_working_capital: 'project cash after tax and the working capital change',
  equity_after_working_capital: 'equity cash after tax and the working capital change',
}

/** The section a served row-reason item belongs to, as the sentence's subject. */
const REASON_SECTION: Record<string, string> = {
  operating: 'the operating section',
  capex: 'the capex section',
  terminal: 'the terminal value section',
  tax: 'the tax section',
  debt: 'the debt section',
  incentives: 'the incentives section',
  cfads: 'the CFADS section',
  inputs: 'the business case',
}

const plain = (s: string | undefined) => (s ?? '').replace(/[_:]/g, ' ').trim()
// An asset or line in its display name (RAW-NAMES-1): `genset_1` reads "Genset 1".
const forThe = (a: string[]) => (a[0] ? ` for ${displayName(a[0])}` : '')

/** Why a linked output was not read (`linked_outputs.NotEvaluated.reason`), in words. */
const LINKED_STATE: Record<string, string> = {
  not_own_solve: 'not from a current solve of this project (solve the model again)',
  stale: 'out of date',
  not_finite: 'out of range',
}

/** Words for each code family the report can serve (BC 17, GK B C3). Each is a noun
 *  phrase: what the section needs. Its keys equal `businessCaseRowReasonCodes.json`,
 *  which a backend test pins to every reason code the engine writes. */
export const REASON_CODE_WORDS: Record<string, (args: string[]) => string> = {
  asset_carrier_missing: a => `a carrier${forThe(a)}`,
  book_value_needs_tax_basis: () => 'a tax basis for the book value',
  capex_not_established: () => 'the capex section',
  cfads_not_established: () => 'the CFADS section',
  contingency_share_missing: () => 'a contingency share',
  debt_exceeds_uses: () => 'debt no larger than its uses',
  debt_not_established: () => 'the debt section',
  debt_tenor_beyond_analysis: () => 'a debt tenor inside the analysis period',
  degradation_missing: a => `a degradation rate${forThe(a)}`,
  // An escalation class, not an asset: its id in words.
  escalation_missing: a => `an escalation rate${a[0] ? ` for ${plain(a[0])}` : ''}`,
  feoc_flag_missing: () => 'the FEOC flag',
  generation_missing: () => 'the generation',
  grace_not_inside_tenor: () => 'a grace period inside the tenor',
  grace_with_sculpting: () => 'no grace period on a sculpted tranche',
  grant_exceeds_cost: () => 'a grant no larger than the cost',
  grant_exceeds_itc_base: () => 'a grant no larger than the ITC base',
  incentive_expressed_elsewhere: () => 'the incentive stated once',
  incentive_rate_missing: () => 'an incentive rate',
  incentive_technology_unclassified: () => 'a technology class for the incentive',
  incentives_not_established: () => 'the incentives section',
  input_missing: a => plain(a[0]) || 'an input',
  itc_and_ptc_same_asset: () => 'either the ITC or the PTC on each asset',
  itc_twice_same_asset: () => 'one ITC per asset',
  line_not_established: a => `the line “${a.join(':')}”`,
  // The line names it in the user's words; the output id stays out of the text (UX 1).
  linked_output_not_evaluated: a => `its linked output, which is ${LINKED_STATE[a[a.length - 1] ?? ''] ?? (plain(a.slice(1).join(' ')) || 'not evaluated')}`,
  not_established: () => 'an input',
  not_finite: a => `the ${rowLabel(a[0] ?? '')} figure in range`,
  operating_not_established: () => 'the operating section',
  overnight_cost_missing: a => `an overnight cost${forThe(a)}`,
  pack_rule: () => 'a pack rule',
  part_lifetime_missing: a => `a part lifetime${forThe(a)}`,
  ptc_currency_mismatch: () => 'a PTC in the case currency',
  ptc_factor_unpublished: () => 'a published PTC factor',
  ptc_needs_a_pack_rule: () => 'a pack rule for the PTC',
  ptc_twice_same_asset: () => 'one PTC per asset',
  rate_list_length: () => 'one debt rate per tenor year',
  replacement_outside_axis: () => 'replacements inside the analysis period',
  tax_basis_invalid: () => 'a valid tax basis',
  tax_input_missing: a => plain(a[0]) || 'a tax input',
  tax_layers_missing: () => 'the tax layers',
  tax_not_established: () => 'the tax section',
  tax_pack_missing: () => 'a tax pack',
  terminal_needs_operating: () => 'the operating section',
  terminal_needs_part_lifetimes: a => `part lifetimes${forThe(a)}`,
  terminal_not_established: () => 'the terminal value',
  // `<asset or line>:<part>`: the remaining-life rule values each part by its cost and life.
  // The remaining-life rule cannot price this part: another terminal-value method can.
  terminal_part_unknown: a => `a terminal-value method that can price ${displayName(a[0] ?? '') || 'each part'}${a[1] ? ` (its ${plain(a[1])} part)` : ''}`,
  terminal_value_missing: () => 'a terminal value',
  upfront_only_from_capital_cost: a => `an upfront cost${forThe(a)}`,
}

function rowLabel(key: string): string {
  for (const sec of PNL_SECTIONS) for (const r of sec.rows) {
    if (r.key === key) return r.label.replace(/^[A-Z](?=[a-z])/, c => c.toLowerCase())
  }
  return plain(key)
}

/** One served code in words; a family without words reads with its punctuation
 *  removed, never raw. */
export function reasonCodeWords(code: string): string {
  const [fam, ...args] = code.split(':')
  const f = REASON_CODE_WORDS[fam]
  return f ? f(args) : plain(code)
}

const joinAnd = (a: string[]) =>
  a.length <= 1 ? (a[0] ?? '') : `${a.slice(0, -1).join(', ')} and ${a[a.length - 1]}`

/** The served items of one section in words: one family over several items (a
 *  degradation rate for six assets) reads once with its items joined (walk 03). */
function familyWords(codes: string[]): string[] {
  const fams = new Map<string, string[][]>()
  for (const c of codes) {
    const [fam, ...args] = c.split(':')
    fams.set(fam, [...(fams.get(fam) ?? []), args])
  }
  const out: string[] = []
  for (const [fam, argsList] of fams) {
    const f = REASON_CODE_WORDS[fam]
    const firsts = argsList.map(a => a[0]).filter((a): a is string => !!a)
    // Merge only a family that names one item each (its first argument).
    if (f && argsList.length > 1 && argsList.every(a => a.length === 1) && firsts.length === argsList.length) {
      out.push(f([joinAnd([...new Set(firsts.map(displayName))])]))
    } else {
      for (const a of argsList) out.push(f ? f(a) : plain([fam, ...a].join(':')))
    }
  }
  return [...new Set(out)]
}

/** The served items in a sentence (UX on BC 17): grouped by section in served order,
 *  "Not established: the tax section needs a tax pack." No nested colons. */
export function servedReasonText(items: BusinessCaseRowReasonItem[]): string {
  const groups = new Map<string, string[]>()
  const loose: string[] = []
  for (const it of items) {
    if (it.code === 'not_established') continue
    if (it.section === null) {
      loose.push(it.code.startsWith('not_finite:')
        ? `the ${rowLabel(it.code.slice('not_finite:'.length))} figure is out of range`
        : reasonCodeWords(it.code))
      continue
    }
    groups.set(it.section, [...(groups.get(it.section) ?? []), it.code])
  }
  const parts = [...groups].map(([sec, codes]) =>
    `${REASON_SECTION[sec] ?? `the ${plain(sec)} section`} needs ${joinAnd(familyWords(codes))}`)
  const all = [...parts, ...loose]
  return all.length ? `Not established: ${all.join('; ')}.` : 'Not established.'
}

/** Where a served item is fixed: the finance inputs, or the business-case lines. */
const LINE_FAMILIES = new Set(['line_not_established', 'linked_output_not_evaluated', 'not_finite'])
function fixPlace(it: BusinessCaseRowReasonItem): 'finance' | 'lines' {
  const fam = it.code.split(':', 1)[0]
  return LINE_FAMILIES.has(fam) || (it.section === 'inputs' && fam !== 'input_missing') ? 'lines' : 'finance'
}

/** The first blocker among served items (walk 03, UX 2): the first item that is a
 *  cause, not a section that only waits on another (`capex_not_established` in tax),
 *  with its family merged over items, and where it is set. */
function firstBlocker(items: BusinessCaseRowReasonItem[]): { phrase: string; place: 'finance' | 'lines' } | null {
  const real = items.filter(it => it.code !== 'not_established' && it.section !== null)
  if (!real.length) return null
  // A section that only waits on another, or the generic "a terminal value" beside the
  // part that causes it, yields to a more specific cause.
  const generic = (code: string) => {
    const fam = code.split(':', 1)[0]
    return /_not_established$/.test(fam) || (fam === 'terminal_value_missing'
      && real.some(it => it.code.startsWith('terminal_part_unknown:')))
  }
  const first = real.find(it => !generic(it.code)) ?? real[0]
  const fam = first.code.split(':', 1)[0]
  const same = real.filter(it => it.code.split(':', 1)[0] === fam && it.section === first.section)
  return { phrase: joinAnd(familyWords(same.map(it => it.code))), place: fixPlace(first) }
}

/** What to do first for the KPIs that are not established (walk 03, UX 2): one
 *  blocker, as an action ("Set a degradation rate for Genset 1"), and where it is set.
 *  Null when every KPI is established or no reason is served. */
export function firstFix(
  kpis: Record<string, BusinessCaseKpi | undefined>, pnl: BusinessCasePnl | undefined,
): { action: string; place: 'finance' | 'lines' } | null {
  for (const spec of KPI_ORDER) {
    const k = kpis[spec.key]
    if (k?.status !== 'not_established' || !sectionReason(k.reason)) continue
    const b = firstBlocker(pnl?.row_reason?.[KPI_ROW[spec.key]] ?? [])
    if (b) return { action: `${b.place === 'lines' ? 'Fix' : 'Set'} ${b.phrase}`, place: b.place }
  }
  return null
}

export function rowReason(key: string, pnl: BusinessCasePnl): string {
  if ((pnl.not_finite ?? []).includes(key)) return 'Not established. The figure is out of range.'
  // A stream is not a P&L row: the report serves no row reason for it.
  if (key.startsWith('stream:')) return 'Not established. It needs the stream\'s lines.'
  // A report that serves reasons (BC 17) is never read through ROW_NEEDS, even for a
  // row it gives no reason for; only a report stored before it falls back.
  if (pnl.row_reason) return servedReasonText(pnl.row_reason[key] ?? [])
  return ROW_NEEDS[key] ? `Not established. It needs ${ROW_NEEDS[key]}.` : 'Not established.'
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

/** The P&L row each KPI is computed from (`services/finance/business_case.kpis`):
 *  a KPI not established for a section reads that row's served reasons (walk 03). */
const KPI_ROW: Record<string, string> = {
  project_npv: 'project_post_tax',
  project_irr: 'project_post_tax',
  equity_npv: 'equity_post_tax',
  equity_irr: 'equity_post_tax',
  payback_years: 'equity_post_tax',
  min_dscr: 'debt_service',
  avg_dscr: 'debt_service',
  ebitda_margin_gross: 'ebitda',
  ebitda_margin_net: 'ebitda',
  revenue_per_capacity_gross: 'ebitda',
  revenue_per_capacity_net: 'ebitda',
}

/** A KPI reason that only names a section (`operating_not_established…`), not a cause. */
const sectionReason = (r: string | null | undefined) =>
  !!r && (r === 'not_established' || /^[a-z]+_not_established(:|$)/.test(r))

/** A KPI in words (UX 6): never 0 for a missing value. With the report's P&L, a KPI
 *  that only names a section reads the cause the engine served for its row (walk 03). */
export function kpiText(
  k: BusinessCaseKpi | undefined, fmt: (typeof KPI_ORDER)[number]['fmt'], pnl?: BusinessCasePnl, key?: string,
): string {
  if (!k) return 'Not established: not in this report'
  if (k.status === 'not_established') {
    const b = sectionReason(k.reason) ? firstBlocker(pnl?.row_reason?.[KPI_ROW[key ?? ''] ?? ''] ?? []) : null
    return b ? `Not established: needs ${b.phrase}` : `Not established: ${reasonWords(k.reason)}`
  }
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

/** What to do about each refusal kind, as an action (UX 2: "Guarantees of origin: set
 *  an indexation rate"). A kind without one reads its cell words. */
export const REFUSAL_ACTION: Record<string, string> = {
  name_duplicate: 'give it a name no other line has',
  category_not_in_kind: 'pick a category of its kind',
  unit_not_allowed: 'pick a unit its quantity source offers',
  quantity_missing: 'give a quantity, or fill every year',
  share_out_of_range: 'give a share between 0 and 1',
  share_cycle: 'take the share of a line that is not a share',
  share_base_empty: 'add a line to take the share of, or type the quantity',
  end_before_start: 'set an end year after the start year',
  outside_horizon: 'set years inside the investment case',
  year_out_of_range: 'keep each year between its start and end',
  indexation_missing: 'set an indexation rate',
  indexation_out_of_range: 'keep the indexation between −20 and +30 %/yr',
  life_missing: 'set a life in years',
  rate_missing: 'set a declining-balance rate',
  price_not_established: 'set a price',
  cost_not_established: 'set a price',
  capex_missing: 'add a capex line of the category it needs',
  it_capex_missing: 'add an IT hardware capex line',
  facility_capex_missing: 'add a facility capex line',
  capex_not_owned: 'remove it, or pick a capex the project owns',
  pass_through_price_includes_energy: 'remove the cost-recovery line; the price already includes energy',
  linked_output_missing: 'pick another output; this model has no such output',
  double_count: 'type the quantity; the energy model already prices this output',
  default_unit_mismatch: 'set the price; the cost default is in another unit',
}

/** A refused run in words (walk 05, UX 2): `business_case_refused:<kind>: lines[<i>].<column>`
 *  reads "<line name>: <action>", the line by its saved name (or its number past the
 *  saved list), never the index. Any other text is kept as served. */
export function refusedRunText(error: string, lineNames: string[]): string {
  const m = /^business_case_refused:([a-z_]+)(?::\s*(.*))?$/.exec(error.trim())
  if (!m) return error
  const [, kind, field] = m
  const action = REFUSAL_ACTION[kind] ?? refusalWords(kind, field)
  const at = parseField(field)
  if (!at) return action.charAt(0).toUpperCase() + action.slice(1)
  return `${lineNames[at.line] ?? `Line ${at.line + 1}`}: ${action}`
}
