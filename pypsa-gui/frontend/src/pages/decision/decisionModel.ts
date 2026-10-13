// Pure predicates of the decision study's guided flow (plan S8): section
// status, entry state, maturity label, verdict tone, figure text — everything a
// page decides that can be decided without rendering, so it is unit-tested in
// `decisionModel.test.ts`. No React, no fetching, no wording of its own: the
// words come from `utils/decisionVocabulary.ts`.
import type {
  DecisionReport, DecisionStudy, Figure, Findings, LedgerPayload, LedgerRow, RunRecord,
  StudyIntake, StudyMaturity, TornadoRow, ValueStreamsBasis, Verdict,
} from '../../api/decisionStudies'
import {
  ERROR_COPY, MATURITY_LABELS, MATURITY_NOT_ESTABLISHED, NOT_ESTABLISHED,
  type ChipState, type HubSectionId,
} from '../../utils/decisionVocabulary'

// ── the intake ────────────────────────────────────────────────────────────

/** The template's mandatory inputs the intake has not answered — the SAME rule
 * as `backend/services/study/packs.py::missing_inputs` (creation and the run
 * refuse `intake_incomplete` on it). */
export function missingInputs(intake: StudyIntake | null | undefined): Array<'site' | 'connection_limit' | 'load'> {
  const out: Array<'site' | 'connection_limit' | 'load'> = []
  const site = intake?.site ?? {}
  if (typeof site.zone !== 'string' || !site.zone.trim()) out.push('site')
  const conn = Number(site.connection_mw)
  if (!(Number.isFinite(conn) && conn > 0)) out.push('connection_limit')
  const load = intake?.load
  if (!load || typeof load !== 'object' || !load.source) out.push('load')
  return out
}

/** Whether this record runs the question pack (M0 created its own base project). */
export function runsPack(study: Pick<DecisionStudy, 'pack_project' | 'base_project'>): boolean {
  return study.pack_project != null && study.pack_project === study.base_project
}

/** Plan S8: an incomplete study opens on its intake, a complete one on the hub. */
export function entryState(study: DecisionStudy): 'intake' | 'hub' {
  return runsPack(study) && missingInputs(study.intake).length > 0 ? 'intake' : 'hub'
}

/** Leap years are refused by the pack (8760 hourly steps of one non-leap year). */
export function isLeapYear(year: number): boolean {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0
}

// ── the ledger ────────────────────────────────────────────────────────────

/** Gate S2 carry: PV rows are unused when PV is off, and the other PV kind's
 * rows are unused when it is on (the pack reads `pv_<kind>_*` only). */
export function isPvRowUnused(row: Pick<LedgerRow, 'key'>, intake: StudyIntake | null | undefined): boolean {
  if (!row.key.startsWith('pv_')) return false
  const pv = intake?.pv
  if (!pv?.enabled) return true
  const kind = pv.kind ?? 'rooftop'
  return !row.key.startsWith(`pv_${kind}_`)
}

/** Gate S2 [N]: the reason lives in the `needs_attention:<key>:<reason>` note. */
export function parseNeedsAttention(notes: readonly string[] | null | undefined): Array<{ key: string; reason: string }> {
  const out: Array<{ key: string; reason: string }> = []
  for (const note of notes ?? []) {
    if (!note.startsWith('needs_attention:')) continue
    const rest = note.slice('needs_attention:'.length)
    const i = rest.indexOf(':')
    if (i <= 0) continue
    out.push({ key: rest.slice(0, i), reason: rest.slice(i + 1) })
  }
  return out
}

/** Every flagged row: by status, and by the note protocol (the backend's rule). */
export function needsAttentionKeys(payload: LedgerPayload | null | undefined): Set<string> {
  const keys = new Set<string>()
  for (const r of payload?.ledger.rows ?? []) if (r.status === 'needs_attention') keys.add(r.key)
  for (const n of parseNeedsAttention(payload?.ledger.honesty_notes)) keys.add(n.key)
  return keys
}

const CURRENCIES = new Set(['EUR', 'USD', 'GBP', 'CHF'])

/** A money unit is recognised by its unit, not by `currency_year` (the ledger's rule). */
export function isMoneyUnit(unit: string | null | undefined): boolean {
  return !!unit && CURRENCIES.has(unit.split('/')[0].trim().toUpperCase())
}

/** Gate S2 [N]: the row's currency year beside a money input. */
export function ledgerCurrencyYearLabel(row: Pick<LedgerRow, 'unit' | 'currency_year'>): string | null {
  if (!isMoneyUnit(row.unit)) return null
  const cur = row.unit.split('/')[0].trim().toUpperCase()
  return row.currency_year != null ? `${cur} of ${row.currency_year}` : `${cur}, currency year not stated`
}

/** Plan S8: maturity is read from ONE source — the ledger payload's `maturity`
 * (recomputed live from the ledger and the intake's load on every GET). */
export function maturityLabel(m: StudyMaturity | null | undefined): string {
  if (!m || m.status !== 'ok' || !m.class) return MATURITY_NOT_ESTABLISHED
  return MATURITY_LABELS[m.class]
}

// ── figures: ADR-0001, a null is "not established", never 0 ────────────────

function trim(n: number, digits: number): string {
  // ADR-0001 spirit: a near-zero value renders as the value, not as 0.
  if (n !== 0 && Math.abs(n) < 10 ** -digits) return String(Number(n.toPrecision(2)))
  return String(Number(n.toFixed(digits)))
}

export function formatMoney(value: number, currency = 'EUR'): string {
  const a = Math.abs(value)
  if (a >= 1e9) return `${currency} ${trim(value / 1e9, 2)} B`
  if (a >= 1e6) return `${currency} ${trim(value / 1e6, 2)} M`
  if (a >= 1e3) return `${currency} ${trim(value / 1e3, 1)} k`
  return `${currency} ${trim(value, 0)}`
}

/** A value in a unit, as the guided flow shows it; `null` is "not established". */
/**
 * U2 gate (owner decision 7): the streams the page shows — all but one at
 * exactly 0 whose tariff has no item for any of its components
 * (`itemised === false`), as `findings.shown_streams` does for the report.
 */
export function shownStreams<T extends { annual_value: number | null; itemised?: boolean | null }>(streams: T[]): T[] {
  return streams.filter(s => !(s.itemised === false && s.annual_value === 0))
}

/** Whether a bill line is shown: present on the bill, and not a 0 the tariff has no item for. */
export function billLineShown(value: number | null | undefined, key: string,
  itemised: string[] | null | undefined): boolean {
  if (value === undefined) return false
  return !(value === 0 && itemised != null && !itemised.includes(key))
}

export function valueText(value: number | null | undefined, unit: string | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return NOT_ESTABLISHED
  const u = (unit ?? '').trim()
  if (isMoneyUnit(u)) {
    const [cur, ...per] = u.split('/')
    const money = formatMoney(value, cur.trim().toUpperCase())
    return per.length ? `${money}/${per.join('/')}` : money
  }
  if (u === 'years') return `${trim(value, 1)} years`
  if (u === 'per unit') return `${trim(value * 100, 2)} %`
  if (u === 'multiplier') return `× ${trim(value, 2)}`
  if (u === 'MW' || u === 'MWh' || u === 'h') return `${trim(value, 2)} ${u}`
  return u ? `${trim(value, 3)} ${u}` : trim(value, 3)
}

export function figureText(fig: Figure | null | undefined): string {
  if (!fig) return NOT_ESTABLISHED
  return valueText(fig.value, fig.unit)
}

/** "real, pre-tax, no subsidy · EUR of 2020 · cash-flow model · quick screen" parts. */
export function figureBasisParts(fig: Figure): string[] {
  const parts: string[] = []
  if (fig.basis) {
    parts.push(`${fig.basis.terms}, ${fig.basis.tax}-tax, ${fig.basis.subsidy === 'excl' ? 'no subsidy' : 'with subsidy'}`)
  }
  if (isMoneyUnit(fig.unit)) {
    const cur = fig.unit.split('/')[0].trim().toUpperCase()
    parts.push(fig.currency_year != null ? `${cur} of ${fig.currency_year}` : `${cur}, currency year not stated`)
  }
  return parts
}

/** A number as the guided flow writes it, without its unit. */
export function numberText(value: number, unit: string): string {
  return trim(value, unit === 'years' ? 1 : unit === 'MW' || unit === 'MWh' || unit === 'h' ? 2 : 3)
}

/** "1 hour", "2 hours" (a duration read aloud, not a unit symbol). */
export function hoursText(h: number): string {
  return `${trim(h, 2)} ${h === 1 ? 'hour' : 'hours'}`
}

// The unit words a template may write after a reference, per figure unit.
const UNIT_WORDS: Record<string, RegExp> = {
  MW: /^MW$/, MWh: /^MWh$/, h: /^(h|hours?)$/, years: /^years?$/,
}

/**
 * The verdict sentence is a fixed template whose numbers are `{{fact_id}}`
 * references; a null or unknown fact reads "not established" (gate S6 carry).
 * Where the template already writes the unit after the reference
 * (`{{battery_p_nom_mw}} MW`, `{{battery_max_hours}} hours`), only the number
 * is substituted and the unit word agrees with it (gate S8 BC-S8-1); a money
 * figure carries its currency.
 */
export function renderSentence(template: string, facts: Record<string, Figure>): string {
  return template.replace(/\{\{\s*([A-Za-z0-9_]+)\s*\}\}( ([A-Za-z]+))?/g, (_m, id: string, tail?: string, word?: string) => {
    const fig = facts[id]
    const after = tail ?? ''
    if (!fig || fig.value == null || !Number.isFinite(fig.value)) {
      return word && Object.values(UNIT_WORDS).some(re => re.test(word)) ? NOT_ESTABLISHED : NOT_ESTABLISHED + after
    }
    const re = UNIT_WORDS[fig.unit]
    if (word && re?.test(word)) {
      if (fig.unit === 'h' && word !== 'h') return hoursText(fig.value)
      if (fig.unit === 'years') return `${numberText(fig.value, 'years')} ${fig.value === 1 ? 'year' : 'years'}`
      return `${numberText(fig.value, fig.unit)} ${word}`
    }
    return figureText(fig) + after
  })
}

// ── the verdict and the findings ──────────────────────────────────────────

export type Tone = 'good' | 'caution' | 'bad' | 'unknown'

export function verdictTone(v: Verdict, available: boolean): Tone {
  if (!available || v.status !== 'ok' || !v.class) return 'unknown'
  return v.class === 'recommended' ? 'good' : v.class === 'marginal' ? 'caution' : 'bad'
}

/** Gate S6/S7 carry: the waterfall is labelled by `value_streams_basis`; an
 * older payload without the field falls back to the note the backend wrote. */
export function streamsBasis(f: Findings): ValueStreamsBasis | null {
  if (f.value_streams_status !== 'ok') return null
  if (f.value_streams_basis) return f.value_streams_basis
  return f.honesty_notes.includes('value_streams_battery_increment_over_pv_only_reference')
    ? 'pv_only_reference' : 'baseline'
}

/** Bars by swing, largest first; a row whose swing is unknown goes last. */
export function sortTornado(rows: readonly TornadoRow[]): TornadoRow[] {
  return [...rows].sort((a, b) => {
    if (a.swing == null && b.swing == null) return 0
    if (a.swing == null) return 1
    if (b.swing == null) return -1
    return b.swing - a.swing
  })
}

/**
 * Gate S4 carry: the Expert view opens the chosen OPTION FORK, never the base
 * project (the base holds the creation-time baseline network). `optionId`
 * defaults to the option the verdict names. A reference equal to the base is
 * refused, whatever the payload says.
 */
export function expertTarget(
  f: Findings, study: Pick<DecisionStudy, 'base_project'>, optionId?: string | null,
): { optionId: string; projectRef: string } | null {
  const id = optionId ?? f.verdict.option_id
  if (!id) return null
  const opt = f.options.find(o => o.option_id === id)
  if (!opt || opt.solve_status !== 'ok' || !opt.project_ref) return null
  if (opt.project_ref === study.base_project) return null
  return { optionId: opt.option_id, projectRef: opt.project_ref }
}

// ── polling (gate S6 [N5]: GET findings re-reads every option network) ────

/** Poll a status route while its record runs; never poll findings. */
export function pollInterval(rec: { status: string } | null | undefined, ms: number): number | false {
  return rec?.status === 'running' ? ms : false
}

// ── the report ────────────────────────────────────────────────────────────

/** Gate S7 [N7]: a run or a tornado that finished after the report was
 * generated makes it worth re-assembling (a tornado does not mark it stale). */
export function reportNeedsReassembly(
  report: Pick<DecisionReport, 'generated_at'> | null | undefined,
  run: { finished_at?: number | null } | null | undefined,
  tornado: { finished_at?: number | null } | null | undefined,
): boolean {
  if (!report?.generated_at) return false
  const generated = Date.parse(report.generated_at) / 1000
  if (!Number.isFinite(generated)) return false
  return [run?.finished_at, tornado?.finished_at].some(t => t != null && t > generated)
}

/** A refusal whose only remedy is running the study again. */
export function rerunNeeded(code: string | null | undefined): boolean {
  return !!code && ERROR_COPY[code]?.rerun === true
}

// ── the hub's section chips ───────────────────────────────────────────────

export interface HubInputs {
  study: DecisionStudy
  ledger?: LedgerPayload | null
  run?: RunRecord | null
  findings?: { data?: Findings | null; errorCode?: string | null }
  report?: { data?: DecisionReport | null; errorCode?: string | null }
}

export function sectionStatuses(x: HubInputs): Record<HubSectionId, ChipState> {
  const { study } = x
  const missing = missingInputs(study.intake)
  const pack = runsPack(study)
  const rows = x.ledger?.ledger.rows ?? []
  const flagged = needsAttentionKeys(x.ledger)
  const keyRows = rows.filter(r => r.sensitivity_flag && r.value != null)
  const keyDefault = keyRows.some(r => r.status === 'default')

  const site: ChipState = !pack ? 'unavailable'
    : missing.includes('site') || missing.includes('connection_limit') ? 'not_started' : 'done'
  const demand: ChipState = !pack ? 'unavailable'
    : missing.includes('load') ? 'not_started'
    : study.intake.load?.source === 'upload' ? 'done' : 'using_defaults'
  const options: ChipState = !pack ? 'unavailable' : 'done'
  const tariffRow = rows.find(r => r.key === 'tariff')
  const tariff: ChipState = !pack ? 'unavailable'
    : flagged.has('demand_charge_price') || flagged.has('energy_price_level') ? 'needs_attention'
    : !study.intake.tariff || tariffRow?.provenance === 'library' ? 'using_defaults' : 'customised'
  const finance: ChipState = rows.find(r => r.key === 'discount_rate')?.status === 'customised' ? 'customised' : 'using_defaults'
  const assumptions: ChipState = !x.ledger ? 'not_started'
    : flagged.size ? 'needs_attention' : keyDefault ? 'using_defaults' : 'customised'

  const runStatus = x.run?.status
  const run: ChipState = !pack ? 'unavailable'
    : runStatus === 'running' ? 'running'
    : runStatus === 'done' ? 'done'
    : runStatus === 'aborted' || runStatus === 'failed' ? 'not_established'
    : 'not_started'

  let findings: ChipState = 'not_started'
  if (!pack) findings = 'unavailable'
  else if (runStatus === 'running' || !runStatus) findings = 'not_started'
  else if (rerunNeeded(x.findings?.errorCode)) findings = 'rerun_needed'
  else if (x.findings?.data) {
    const f = x.findings.data
    findings = f.available && f.verdict.status === 'ok' ? 'done' : 'not_established'
  } else if (x.findings?.errorCode) findings = 'not_established'

  let report: ChipState = 'not_started'
  if (!pack) report = 'unavailable'
  else if (x.report?.data) report = x.report.data.stale ? 'stale' : 'done'

  return { site, demand, options, tariff, finance, assumptions, run, findings, report }
}

/** The options a run solves for this intake (`questions.options_for`): the
 * grid-only baseline, the three battery durations, and battery + PV only
 * when PV is on. One solve each (`campaign.estimate_solves`). */
export function optionsFor(intake: StudyIntake | null | undefined): string[] {
  const base = ['none', 'bess_1h', 'bess_2h', 'bess_4h']
  return intake?.pv?.enabled ? [...base, 'bess_pv_2h'] : base
}

// ── a bess_pv verdict (gate S8 BC-S8-2; the report's S7 rule) ────────────

/** Hours of storage of a battery option, read from its id (`bess_2h`, `bess_pv_2h`). */
export function optionHours(optionId: string): number | null {
  const m = /_(\d+(?:\.\d+)?)h$/.exec(optionId)
  return m ? Number(m[1]) : null
}

export interface PvVerdictContext {
  optionNpv: number | null
  currencyYear: number | null
  best: { optionId: string; powerMw: number; hours: number | null; npv: number; currencyYear: number | null } | null
}

/**
 * When the verdict names an option that also builds PV, the numbers a site
 * that will not build PV needs: the option's total NPV (PV included) and the
 * best battery-only option (a battery above zero with an established battery
 * NPV), or none. Null for a battery-only verdict.
 */
export function pvVerdictContext(f: Findings): PvVerdictContext | null {
  const id = f.verdict.option_id
  if (!id) return null
  const named = f.battery_attribution.find(a => a.option_id === id)
  const withPv = named?.method === 'battery_removed_same_pv'
    || !!f.options.find(o => o.option_id === id)?.sizes.some(s => s.asset === 'pv')
  if (!withPv) return null
  const only = f.battery_attribution.filter(a => a.method === 'battery_only' && a.status === 'ok'
    && a.battery_npv != null && a.battery_p_nom_mw != null && a.battery_p_nom_mw > 1e-3)
  const best = only.reduce<typeof only[number] | null>((b, a) => (!b || a.battery_npv! > b.battery_npv! ? a : b), null)
  return {
    optionNpv: named?.option_npv ?? null,
    currencyYear: named?.currency_year ?? null,
    best: best ? { optionId: best.option_id, powerMw: best.battery_p_nom_mw!, hours: optionHours(best.option_id),
      npv: best.battery_npv!, currencyYear: best.currency_year } : null,
  }
}

/** Two answers are the same when their defined fields are equal (key order and undefined ignored). */
export function sameAnswer(a: unknown, b: unknown): boolean {
  const norm = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(norm)
    if (v && typeof v === 'object') {
      return Object.fromEntries(Object.entries(v as Record<string, unknown>)
        .filter(([, x]) => x !== undefined).sort(([p], [q]) => p.localeCompare(q)).map(([k, x]) => [k, norm(x)]))
    }
    return v
  }
  return JSON.stringify(norm(a ?? {})) === JSON.stringify(norm(b ?? {}))
}
