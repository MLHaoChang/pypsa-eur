import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Hexagon, Square } from 'lucide-react'
import {
  resultsApi,
  type EhArchetype,
  type EhReadiness,
  type EhDtcAttribution,
  type EhTemplateMeta,
  type EhCertificationPayload,
  type EhFleetScope,
  type EhDtcPlanningTable,
  type EhDtcStressTable,
  type EhFmeaTopMode,
  type EhFrontierPayload,
  type EhFrontierPoint,
  type EhLeverTable,
  type EhPipelineStage,
  type EhRedundancyTable,
  type EhReferenceDesignReport,
  type EhScrVerdict,
  type EhSectionStatus,
  type EhStudyPayload,
  type EhStudyRequestBody,
} from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { useStudyFinishedInvalidation } from '../../hooks/useStudyFinishedInvalidation'
import { ehStudyRefetchInterval } from './ehStudyPoll'
import { useChatStore } from '../../store/chatStore'
import { nk } from '../../utils/queryKeys'
import { blockerMessage } from './McPanel'
import { downloadCSV, downloadJSON, fmtCurrency, fmtEnergy, fmtPower } from './shared'
import { GuideButton, useGuide } from '../../components/GuidedTour'
import { prepareTaggingTour } from './prepareTaggingTour'
import { InfoTip } from '../../layout/properties/cardKit'
import type { ZeroReason } from './fmea'

const ARCHETYPES: { id: EhArchetype; label: string; blurb: string }[] = [
  {
    id: 'strong_grid',
    label: 'Strong grid',
    blurb: 'Sizing + ENS target; redundancy/DtC may stay skipped.',
  },
  {
    id: 'weak_flexible',
    label: 'Weak / flexible',
    blurb: 'Import + storage levers and DtC stress by default.',
  },
  {
    id: 'off_grid',
    label: 'Off-grid',
    blurb: 'Island overlay; storage-duration levers (no import MW lever).',
  },
]

const eur = (v: number) =>
  v >= 1e9 ? `€${(v / 1e9).toFixed(2)}bn`
    : v >= 1e6 ? `€${(v / 1e6).toFixed(1)}m`
      : `€${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`

const cell = (v: unknown) =>
  v == null || v === '' ? '—' : String(v)

/** A numeric table cell: MWh via fmtEnergy (scales to GWh/TWh), € via
 *  fmtCurrency, MW via fmtPower; anything else (or a non-number) renders
 *  like `cell`. */
export const cellNum = (v: unknown, kind: 'mwh' | 'eur' | 'mw' | 'plain'): string => {
  if (typeof v !== 'number' || !Number.isFinite(v)) return cell(v)
  if (kind === 'mwh') return fmtEnergy(v, 2)
  if (kind === 'eur') return fmtCurrency(v, 2)
  if (kind === 'mw') return fmtPower(v, 2)
  return String(v)
}

/** Pack-settings form state: strings, so a half-typed value is not coerced. */
export interface PackForm {
  ensCap: string
  loleTarget: string
  importMw: string
  /** weak_flexible only (P17): annual energy import budget, MWh/yr. */
  importEnergy: string
  budget: string
  draws: string
  seed: string
  dsrBuses: string
  /** Optional stages to run; null = the pack's default pipeline. */
  stages: string[] | null
  /** P18: '' = the default (bus aggregate); 'per_load' opts in. */
  dtcAttribution: '' | EhDtcAttribution
  /** E2E review m5: lever flags; '' keeps the pack's own value. */
  levers: Partial<Record<LeverKey, 'on' | 'off'>>
}

export type LeverKey = 'import_cap' | 'storage_duration' | 'redundancy' | 'import_energy'

/** Lever toggles offered per archetype (the backend refuses the others). */
export function leverKeysFor(archetype: EhArchetype): LeverKey[] {
  if (archetype === 'weak_flexible') {
    return ['storage_duration', 'import_cap', 'import_energy', 'redundancy']
  }
  if (archetype === 'off_grid') return ['storage_duration', 'redundancy']
  return ['storage_duration', 'import_cap', 'redundancy']
}

export const EMPTY_PACK_FORM: PackForm = {
  ensCap: '', loleTarget: '', importMw: '', importEnergy: '', budget: '',
  draws: '', seed: '',
  dsrBuses: '', stages: null, dtcAttribution: '', levers: {},
}

/** Stages a user may toggle; apply_pack / ens_solve / assemble always run. */
export const OPTIONAL_STAGES = [
  'frontier', 'mc_certify', 'fmea_top', 'redundancy', 'levers', 'dtc_stress',
  'dtc_planning',
] as const

const PIPELINE_ORDER = [
  'apply_pack', 'ens_solve', ...OPTIONAL_STAGES, 'assemble',
] as const

/**
 * The POST body from the form. Blank fields are OMITTED (never defaulted
 * here — the engine owns its defaults); an invalid value returns an error
 * naming it instead of a body. Import MW and DSR buses are weak_flexible only.
 */
export function buildEhStudyBody(
  archetype: EhArchetype, form: PackForm,
): { body: EhStudyRequestBody | null; error: string | null } {
  const body: EhStudyRequestBody = { archetype }
  const num = (raw: string, label: string, ok: (v: number) => boolean,
    rule: string): number | undefined | string => {
    if (raw.trim() === '') return undefined
    const v = Number(raw)
    if (!Number.isFinite(v) || !ok(v)) return `${label} must be ${rule}`
    return v
  }
  const checks = {
    ens: num(form.ensCap, 'ENS target', v => v > 0, '> 0 ‱'),
    lole: num(form.loleTarget, 'LOLE target', v => v >= 0, '≥ 0 h/yr'),
    imp: archetype === 'weak_flexible'
      ? num(form.importMw, 'import cap', v => v > 0, '> 0 MW') : undefined,
    energy: archetype === 'weak_flexible'
      ? num(form.importEnergy, 'import energy budget', v => v >= 0,
        '≥ 0 MWh/yr') : undefined,
    budget: num(form.budget, 'budget', v => Number.isInteger(v) && v >= 1 && v <= 120,
      'an integer 1–120'),
    draws: num(form.draws, 'MC draws', v => Number.isInteger(v) && v >= 1 && v <= 2000,
      'an integer 1–2000'),
    seed: num(form.seed, 'MC seed', v => Number.isInteger(v) && v >= 0,
      'an integer ≥ 0'),
  }
  for (const v of Object.values(checks)) {
    if (typeof v === 'string') return { body: null, error: v }
  }
  const po: NonNullable<EhStudyRequestBody['pack_overrides']> = {}
  if (typeof checks.ens === 'number') po.ens_cap_permyriad = checks.ens
  if (typeof checks.lole === 'number') {
    // A target certifies with the MC; strong_grid's factory metric is
    // 'none', which the backend refuses beside a target (E2E review m2).
    po.target_lole_h = checks.lole
    po.certification_metric = 'mc_lole'
  }
  if (typeof checks.imp === 'number') po.import_p_nom_mw = checks.imp
  if (typeof checks.energy === 'number') po.import_energy_mwh_per_year = checks.energy
  const levers: Record<string, boolean> = {}
  for (const k of leverKeysFor(archetype)) {
    const v = form.levers[k]
    if (v) levers[k] = v === 'on'
  }
  if (Object.keys(levers).length > 0) po.levers = levers
  if (Object.keys(po).length > 0) body.pack_overrides = po
  if (typeof checks.budget === 'number') body.budget_solves = checks.budget
  const mc: NonNullable<EhStudyRequestBody['mc']> = {}
  if (typeof checks.draws === 'number') mc.draws = checks.draws
  if (typeof checks.seed === 'number') mc.seed = checks.seed
  if (Object.keys(mc).length > 0) body.mc = mc
  if (archetype === 'weak_flexible') {
    const buses = form.dsrBuses.split(',').map(b => b.trim()).filter(Boolean)
    if (buses.length > 0) body.dsr_buses = buses
  }
  if (form.dtcAttribution) body.dtc_attribution = form.dtcAttribution
  if (form.stages !== null) {
    const chosen = new Set(['apply_pack', 'ens_solve', 'assemble', ...form.stages])
    body.stages = PIPELINE_ORDER.filter(st => chosen.has(st))
  }
  return { body, error: null }
}

/** Solved frontier points a knee needs (owner's Q3 rule; mirrors the
 *  backend's `MIN_EH_FRONTIER_KNEE_POINTS`). */
export const MIN_KNEE_POINTS = 3

/** Stable display order for completeness chips (matches REPORT_SECTIONS). */
export const COMPLETENESS_ORDER = [
  'target', 'certification', 'cost', 'frontier', 'sizing', 'redundancy',
  'levers', 'dtc', 'fmea_top', 'tea', 'gates', 'multi_energy',
] as const

export function completenessRows(
  map: Record<string, EhSectionStatus> | null | undefined,
): { name: string; status: EhSectionStatus }[] {
  if (!map) return []
  const seen = new Set<string>()
  const out: { name: string; status: EhSectionStatus }[] = []
  for (const name of COMPLETENESS_ORDER) {
    if (name in map) {
      out.push({ name, status: map[name] })
      seen.add(name)
    }
  }
  for (const [name, status] of Object.entries(map)) {
    if (!seen.has(name)) out.push({ name, status })
  }
  return out
}

export function statusTone(status: EhSectionStatus): string {
  // The theme's success token: the accent is the brand red, which read as an
  // error on every "ok" chip (click-through obstacle 5).
  if (status === 'ok') return 'text-success'
  if (status === 'skipped') return 'text-muted'
  return 'text-warn'
}

/** Sections that are not_established with a reason, in chip order.
 *  Gates / multi-energy notes render in their own blocks, so they are left out. */
export function notEstablishedNotes(
  report: EhReferenceDesignReport,
): { name: string; note: string }[] {
  return completenessRows(report.completeness)
    .filter(({ name, status }) =>
      status === 'not_established'
      && name !== 'gates' && name !== 'multi_energy'
      && Boolean(report.sections?.[name]?.note))
    .map(({ name }) => ({ name, note: String(report.sections![name].note) }))
}

/** Tone for the MC certification verdict (P11 vocabulary `pass` / `fail` /
 *  `inconclusive`; a report stored by a build that used `certified` /
 *  `failed` / `no_target` is toned the same way — merge 2026-09-28). */
export function verdictTone(verdict: string | null | undefined): string {
  if (verdict === 'pass' || verdict === 'certified') return 'text-accent'
  if (verdict === 'fail' || verdict === 'failed') return 'text-danger'
  if (verdict === 'no_target') return 'text-muted'
  return 'text-warn'
}

/** A verdict in the P11 vocabulary; `null` = no verdict (no target, or not
 *  established). Older stored reports used certified / failed / no_target. */
export function normalisedVerdict(verdict: unknown): string | null {
  if (verdict === 'certified') return 'pass'
  if (verdict === 'failed') return 'fail'
  if (verdict === 'no_target' || verdict === 'not_established') return null
  return typeof verdict === 'string' ? verdict : null
}

/** What to do after each verdict (spec §2.9); null when nothing is owed. */
export const CERTIFICATION_NEXT = {
  fail: 'Next: tighten the energy target or add firm capacity — press Ask the assistant for a reviewed recommendation.',
  inconclusive: 'Next: raise MC draws (Pack settings → Draws) or tighten the plan; an inconclusive verdict is not a failure.',
  noTarget: 'Next: set a shortfall target (Pack settings → LOLE target) to certify.',
} as const

/** MC certification headline from the `certification` section, if it ran. */
export function certificationHeadline(report: EhReferenceDesignReport): {
  perYear: number
  ci: [number, number] | null
  verdict: string | null
  target: number | null
  next: string | null
} | null {
  const payload = report.sections?.certification?.payload as
    | Record<string, unknown> | null | undefined
  if (!payload) return null
  // A report stored by a build without `lole_h_per_year` carried the LOLE as
  // `mc_lole_h` on a one-year basis (merge 2026-09-28).
  const perYear = typeof payload.lole_h_per_year === 'number'
    ? payload.lole_h_per_year
    : typeof payload.mc_lole_h === 'number' ? payload.mc_lole_h : null
  if (perYear == null) return null
  const years = typeof payload.horizon_years === 'number' ? payload.horizon_years : 1
  const raw = Array.isArray(payload.lole_ci) ? payload.lole_ci : null
  const ci = raw && years > 0
    && typeof raw[0] === 'number' && typeof raw[1] === 'number'
    ? [raw[0] / years, raw[1] / years] as [number, number]
    : null
  const verdict = normalisedVerdict(payload.verdict)
  // No target (the study reports LOLE but certifies nothing) and the 'none'
  // metric both leave the verdict empty: the next step is to set a target.
  const next = verdict === 'fail' ? CERTIFICATION_NEXT.fail
    : verdict === 'inconclusive' ? CERTIFICATION_NEXT.inconclusive
      : verdict === 'pass' ? null
        : (verdict == null || verdict === 'none' || payload.metric === 'none')
          ? CERTIFICATION_NEXT.noTarget : null
  return {
    perYear,
    ci,
    verdict,
    target: typeof payload.target_lole_h === 'number' ? payload.target_lole_h : null,
    next,
  }
}

/** Tone for the P9 SCR product verdict (fail reserved; thin slice uses warn). */
export function scrTone(scr: EhScrVerdict): string {
  if (scr === 'pass') return 'text-accent'
  if (scr === 'fail') return 'text-danger'
  return 'text-warn'
}

/** The certification payload when the mc_certify stage produced one. */
export function certificationPayload(
  report: EhReferenceDesignReport,
): EhCertificationPayload | null {
  const section = report.sections?.certification
  const payload = section?.payload
  if (!payload || typeof payload !== 'object') return null
  return payload as EhCertificationPayload
}

/**
 * One line saying how the import entered the certification fleet, or null
 * when the payload says nothing about it (whole-network scope, or a report
 * without a hub-side scope). A report from before 2026-09-27 carries a
 * `fleet_scope` with `import_firmness: planning_limit_only` and no
 * `import_model` — that was a firm block.
 */
export function importModelLabel(payload: EhCertificationPayload | null): string | null {
  if (!payload) return null
  const scope = payload.fleet_scope ?? null
  const model = payload.import_model ?? scope?.import_model
    ?? (scope?.mode === 'hub_side' ? 'firm_block' : null)
  const cap = scope?.import_cap_mw_max ?? scope?.import_firm_mw_max
  const capText = cap != null ? ` (cap ${Number(cap).toFixed(0)} MW)` : ''
  const firmness = payload.import_firmness ?? scope?.import_firmness
  if (firmness === 'common_mode_sampled') {
    return `firm block; common-mode event sampled (Link and grid down together)${capText}`
  }
  if (firmness === 'outage_and_common_mode_sampled') {
    return `Link outages + common-mode event sampled (Link and grid down together)${capText}`
  }
  switch (model) {
    case 'zonal':
      return firmness === 'grid_sampled'
        ? `grid-side surplus sampled; Links firm at their planning cap (two-area MC)${capText}`
        : `Link outages + grid-side surplus sampled (two-area MC)${capText}`
    case 'sampled_unit':
      return `Link outages sampled (two-state unit at its planning cap)${capText}`
    case 'mixed':
      return `partly sampled: Links without outage data counted firm${capText}`
    case 'islanded':
      return 'islanded (0 MW)'
    case 'excluded':
      return 'not counted in the MC (no outage data of its own — decision 6)'
    case 'firm_block':
      return `firm at the planning limit (no Link outage data)${capText}`
    default:
      return null
  }
}

/** `copt_metrics.import_exact` — the analytic LOLE with the import mixed in. */
export interface EhImportExact {
  lole_hours?: number | null
  eue_mwh?: number | null
  delta_mw?: number
  levels?: number
  note?: string
}

/** One line on the grid area(s) behind the hub, or null when not zonal. */
export function gridAreasSummary(scope: EhFleetScope | null | undefined): string | null {
  const areas = scope?.grid_areas ?? []
  if (areas.length === 0) return null
  const sampled = areas.filter(a => a.sampled)
  const parts = [`${areas.length} grid area${areas.length === 1 ? '' : 's'}, ${sampled.length} sampled`]
  const stores = sampled.flatMap(a => (a.storage_dispatched ? a.storage : []))
  if (stores.length > 0) parts.push(`grid storage dispatched: ${stores.join(', ')}`)
  for (const a of areas.filter(x => !x.sampled)) {
    parts.push(`${a.links.join(', ')}: unbounded (v1)${a.reason ? ` — ${a.reason}` : ''}`)
  }
  return parts.join('; ')
}

/** One line per common-mode event (applied, or why not). */
export function commonModeLines(scope: EhFleetScope | null | undefined): string[] {
  return (scope?.import_common_mode ?? []).map(e => (e.applied
    ? (e.rate > 0
      ? `${e.link}: common-mode q=${e.rate} (${e.basis}), MTTR ${e.mttr_hours} h — Link and grid area down together`
      : `${e.link}: common-mode q=0 — no effect`)
    : `${e.link}: common-mode data not applied — ${e.reason ?? 'no reason given'}`))
}

/** How the COPT screening holds the import, plus the exact import LOLE. */
export function coptImportSummary(
  scope: EhFleetScope | null | undefined,
  coptMetrics: { import_exact?: EhImportExact | null } | null | undefined,
): string | null {
  const model = scope?.copt_import_model
  if (!model) return null
  const label = model === 'expected_surplus_profile'
    ? 'COPT screening: import Link scaled by the expected grid surplus (ranking only)'
    : model === 'two_state'
      ? 'COPT screening: import Link as a two-state unit'
      : 'COPT screening: import as a firm block at the planning limit'
  const withEvents = scope?.copt_common_mode === 'event_mixture'
    ? `${label}; common-mode events mixed exactly`
    : label
  const exact = coptMetrics?.import_exact?.lole_hours
  if (exact == null) return withEvents
  const delta = coptMetrics?.import_exact?.delta_mw
  const rounding = delta != null && delta > 1
    ? `, import rounded down to ${Number(delta).toFixed(0)} MW levels`
    : ''
  return `${withEvents}; exact import LOLE ${Number(exact).toFixed(2)} h (analytic, no storage${rounding})`
}

/** True when there is a certification verdict or an honest reason to show. */
export function hasCertificationBlock(report: EhReferenceDesignReport): boolean {
  const section = report.sections?.certification
  if (!section) return report.mc_lole_h != null
  if (section.status === 'skipped') return false
  return report.mc_lole_h != null || Boolean(section.note)
    || certificationPayload(report) != null
}

/** The frontier points (loosest first, as the engine orders them). */
export function frontierPoints(report: EhReferenceDesignReport): EhFrontierPoint[] {
  const payload = report.sections?.frontier?.payload
  if (!payload || typeof payload !== 'object') return []
  const pts = (payload as { points?: unknown }).points
  if (!Array.isArray(pts)) return []
  return pts.filter(
    (p): p is EhFrontierPoint =>
      Boolean(p) && typeof p === 'object'
      && typeof (p as EhFrontierPoint).target_permyriad === 'number',
  )
}

/** CSV rows for the frontier table — cost is ex-shed by construction.
 *  Takes the report or its points (both call shapes survive the merge). */
export function frontierCsvRows(
  arg: EhReferenceDesignReport | EhFrontierPoint[],
): unknown[][] {
  const points = Array.isArray(arg) ? arg : frontierPoints(arg)
  return points.map(p => [
    p.target_permyriad,
    p.status,
    p.point?.total_system_cost_eur ?? '',
    p.point?.achieved_ens_mwh ?? '',
    p.point?.achieved_shed_hours ?? '',
    p.binding ?? '',
    p.period_basis ?? '',
    p.excludes_shed_cost === false ? 'no' : 'yes',
  ])
}

/** The ranked residual failure modes from the fmea_top stage: the
 *  Link-primary Class-B ranking (`rows`) first, then the class-A COPT
 *  screening of the same plan (`class_a.rows`). Each class is ranked WITHIN
 *  itself (B1…, A1…): the two criticalities come from different engines and
 *  are never one ranking (owner's Q2 rule, merge review N1).
 *  A report stored by a build that merged both into `top` is read as is. */
export function fmeaTopModes(report: EhReferenceDesignReport): EhFmeaTopMode[] {
  const payload = report.sections?.fmea_top?.payload
  if (!payload || typeof payload !== 'object') return []
  const isMode = (m: unknown): m is EhFmeaTopMode =>
    Boolean(m) && typeof m === 'object'
    && typeof (m as EhFmeaTopMode).mode_id === 'string'
  const top = (payload as { top?: unknown }).top
  if (Array.isArray(top)) return top.filter(isMode)
  const b = fmeaTopRows(report).filter(isMode).map((r, i) => ({
    ...r,
    rank: i + 1,
    failure_class: (r as Partial<EhFmeaTopMode>).failure_class ?? 'B',
    component_class: (r as Partial<EhFmeaTopMode>).component_class ?? 'Link',
    name: r.name ?? r.mode_id,
  } as EhFmeaTopMode))
  const classA = (payload as { class_a?: { rows?: unknown } | null }).class_a
  const aRows = Array.isArray(classA?.rows) ? classA!.rows as unknown[] : []
  const a = aRows.filter(isMode).map((r, i) => ({
    ...r, rank: i + 1, failure_class: r.failure_class ?? 'A',
  }))
  return [...b, ...a]
}

/** The class-A COPT screening block of `fmea_top` (merge 2026-09-28): the
 *  payload's `class_a`, or the payload itself on a report that carried the
 *  COPT fields at the top level. */
export function fmeaClassA(report: EhReferenceDesignReport): {
  status?: string
  reason?: string | null
  rows?: EhFmeaTopMode[]
  fleet_scope?: EhFleetScope | null
  copt_metrics?: { import_exact?: EhImportExact | null } | null
  copt_fidelity_note?: string | null
  import_link_ranking?: Record<string, string>
  import_link_ranking_note?: string | null
} | null {
  const payload = report.sections?.fmea_top?.payload as
    | Record<string, unknown> | null | undefined
  if (!payload || typeof payload !== 'object') return null
  const block = payload.class_a
  if (block && typeof block === 'object') return block as never
  return payload as never
}

/** The fmea_top payload's COPT import line (see ``coptImportSummary``). */
export function fmeaCoptImportSummary(report: EhReferenceDesignReport): string | null {
  const payload = fmeaClassA(report)
  return coptImportSummary(payload?.fleet_scope, payload?.copt_metrics)
}

/** The COPT caveat and fidelity notes carried on the fmea_top payload. */
export function fmeaCoptNotes(report: EhReferenceDesignReport): string[] {
  const payload = fmeaClassA(report)
  return [payload?.fleet_scope?.copt_import_note, payload?.copt_fidelity_note]
    .filter((x): x is string => typeof x === 'string' && x.length > 0)
}

/** How a sampled import Link is ranked in fmea_top (once: B, else A). */
export function fmeaImportRankingNote(report: EhReferenceDesignReport): string | null {
  const payload = fmeaClassA(report)
  const ranking = payload?.import_link_ranking
  if (!ranking || Object.keys(ranking).length === 0) return null
  const parts = Object.entries(ranking).map(
    ([link, view]) => `${link}: ${view === 'class_b' ? 'Class-B row' : 'class-A row'}`)
  return `Import Link ranked once — ${parts.join(', ')}.`
    + (payload?.import_link_ranking_note ? ` ${payload.import_link_ranking_note}` : '')
}

/** True when the table mixes the two engines' classes (per-class ranks). */
export function fmeaMixesClasses(modes: EhFmeaTopMode[]): boolean {
  return new Set(modes.map(m => m.failure_class)).size > 1
}

/** CSV rows for the FMEA top-N table (the report or its modes). `rank` is
 *  within the row's class — see `fmeaTopModes`. */
export function fmeaTopCsvRows(
  arg: EhReferenceDesignReport | EhFmeaTopMode[],
): unknown[][] {
  const modes = Array.isArray(arg) ? arg : fmeaTopModes(arg)
  return modes.map(m => [
    m.rank,
    m.failure_class,
    m.component_class,
    m.name,
    m.criticality_eur_per_year ?? '',
    m.delta_eue_mwh ?? '',
    m.occurrence_per_year ?? '',
    m.severity_eur ?? '',
    m.engine ?? '',
  ])
}

/** LCOH headline: the number when established, else the flag to show. */
export function lcohChip(
  report: EhReferenceDesignReport,
): { value: number | null; status: EhSectionStatus | null; note: string | null } | null {
  const tea = report.tea
  if (!tea) return null
  if (tea.lcoh_eur_per_kg != null) {
    return { value: tea.lcoh_eur_per_kg, status: tea.lcoh_status ?? 'ok', note: tea.lcoh_note ?? null }
  }
  if (tea.lcoh_status) {
    return { value: null, status: tea.lcoh_status, note: tea.lcoh_note ?? null }
  }
  return null
}

/** True when the report carries SCR/EMT values or an honest gates note. */
export function hasGatesBlock(report: EhReferenceDesignReport): boolean {
  const gate = report.gates
  if (gate?.scr != null) return true
  if (gate?.emt_recommended != null) return true
  const section = report.sections?.gates
  if (section?.note) return true
  const payload = section?.payload
  if (payload && typeof payload.min_scr === 'number') return true
  return false
}


/** True when multi-energy ENS is established or fail-closed with a note. */
export function hasMultiEnergyBlock(report: EhReferenceDesignReport): boolean {
  const section = report.sections?.multi_energy
  if (!section) return false
  if (section.status === 'skipped') return false
  if (section.note) return true
  const payload = section.payload
  if (payload && typeof payload === 'object') {
    const by = (payload as { ens_by_carrier_mwh?: unknown }).ens_by_carrier_mwh
    if (by && typeof by === 'object') return true
    const v = (payload as { violations?: unknown }).violations
    if (Array.isArray(v) && v.length > 0) return true
  }
  return section.status === 'ok' || section.status === 'not_established'
}

/** Carrier → MWh pairs from the multi_energy section payload. */
export function multiEnergyCarrierEntries(
  report: EhReferenceDesignReport,
): { carrier: string; mwh: number }[] {
  const payload = report.sections?.multi_energy?.payload
  if (!payload || typeof payload !== 'object') return []
  const by = (payload as { ens_by_carrier_mwh?: unknown }).ens_by_carrier_mwh
  if (!by || typeof by !== 'object') return []
  return Object.entries(by as Record<string, unknown>)
    .filter(([, v]) => typeof v === 'number' && Number.isFinite(v))
    .map(([carrier, mwh]) => ({ carrier, mwh: Number(mwh) }))
}

/** Load → MWh pairs from per-Load slack capture (P6b). */
export function multiEnergyLoadEntries(
  report: EhReferenceDesignReport,
): { load: string; mwh: number }[] {
  const payload = report.sections?.multi_energy?.payload
  if (!payload || typeof payload !== 'object') return []
  const by = (payload as { ens_by_load_mwh?: unknown }).ens_by_load_mwh
  if (!by || typeof by !== 'object') return []
  return Object.entries(by as Record<string, unknown>)
    .filter(([, v]) => typeof v === 'number' && Number.isFinite(v) && Number(v) > 0)
    .map(([load, mwh]) => ({ load, mwh: Number(mwh) }))
}

type FmeaRow = {
  mode_id: string
  name?: string
  criticality_eur_per_year?: number
  occurrence_per_year?: number
  severity_eur?: number
  delta_eue_mwh?: number | null
  /** P29 (B3): why a €0 row is €0 — forwarded as the backend sent it. */
  zero_reason?: ZeroReason | null
}

/** Ranked FMEA rows from the report's `fmea_top` section (P12). */
export function fmeaTopRows(report: EhReferenceDesignReport): FmeaRow[] {
  const payload = report.sections?.fmea_top?.payload as { rows?: unknown } | null | undefined
  return Array.isArray(payload?.rows) ? (payload!.rows as FmeaRow[]) : []
}

/** CSV rows for the redundancy comparison table. */
export function redundancyCsvRows(table: EhRedundancyTable): unknown[][] {
  const selected = table.selection?.selected_id ?? ''
  return (table.options ?? []).map(o => [
    o.scenario_id,
    o.status,
    o.cost_at_target_eur ?? '',
    o.achieved_ens_mwh ?? '',
    o.meets_target == null ? '' : o.meets_target ? 'yes' : 'no',
    o.scenario_id === selected ? 'selected' : '',
    o.condition ?? '',
  ])
}

/** CSV rows for lever options. */
export function leverCsvRows(table: EhLeverTable): unknown[][] {
  return (table.options ?? []).map(o => [
    o.kind,
    o.value,
    o.unit ?? '',
    o.status,
    o.cost_at_target_eur ?? '',
    o.achieved_ens_mwh ?? '',
    o.meets_target == null ? '' : o.meets_target ? 'yes' : 'no',
    o.ineffective ? (o.ineffective_reason ?? 'ineffective') : '',
  ])
}

/** CSV rows for DtC stress contingencies. */
export function dtcStressCsvRows(table: EhDtcStressTable): unknown[][] {
  return (table.contingencies ?? []).map(c => [
    c.contingency,
    c.status,
    c.critical_unserved_mwh ?? '',
    c.noncritical_unserved_mwh ?? '',
    c.condition ?? '',
    byLoadText(c.critical_unserved_by_load),
  ])
}

/** `hospital=20; pump=0` — per-Load critical unserved (P16 `per_load`). */
export function byLoadText(m: Record<string, number> | null | undefined): string {
  if (!m) return ''
  return Object.entries(m).map(([k, v]) => `${k}=${+v.toFixed(3)}`).join('; ')
}

/** Why the per-Load priority may not hold, or null when it is exact. */
export function dtcPriorityCaveat(t: EhDtcStressTable): string | null {
  if (t.attribution !== 'per_load' || t.priority_exact !== false) return null
  const parts: string[] = []
  if (t.priority_caveat_links?.length) {
    parts.push(`lossy Links ${t.priority_caveat_links.join(', ')}`)
  }
  if (t.priority_caveat_line_losses) parts.push('line losses')
  return 'Critical Loads are shed last only on loss-free paths — ' +
    `${parts.join(' and ') || 'a lossy path'} can invert the priority, ` +
    'so a critical Load behind them may be shed first.'
}

/** The attribution chip: the raw mode, plus the disclosed premium. */
export function dtcAttributionLabel(t: EhDtcStressTable): string {
  const base = t.attribution ?? ''
  if (t.attribution === 'per_load' && t.voll_premium_eps != null) {
    return `${base} · critical VOLL +${+(t.voll_premium_eps * 100).toFixed(2)}% priority`
  }
  return base
}

/** The pack form a template recommends (P19). Only fields the form knows
 * are carried; anything else stays the pack's own value. */
export function formFromTemplate(meta: EhTemplateMeta): PackForm {
  const po = meta.pack_overrides ?? {}
  const str = (v: unknown) => (typeof v === 'number' ? String(v) : '')
  const optional = new Set<string>(OPTIONAL_STAGES)
  return {
    ...EMPTY_PACK_FORM,
    ensCap: str(po.ens_cap_permyriad),
    loleTarget: str(po.target_lole_h),
    importMw: str(po.import_p_nom_mw),
    importEnergy: str(po.import_energy_mwh_per_year),
    stages: meta.stages ? meta.stages.filter(s => optional.has(s)) : null,
    dtcAttribution: meta.dtc_attribution ?? '',
  }
}

/** Header for the flat report summary CSV (P18). */
export const REPORT_SUMMARY_CSV_HEADER = ['group', 'key', 'value']

/** Flat CSV of the report headline, completeness and notes (P18). */
export function reportSummaryCsvRows(r: EhReferenceDesignReport): unknown[][] {
  const head: [string, unknown][] = [
    ['archetype', r.archetype], ['pack_hash', r.pack_hash],
    ['assumptions_hash', r.assumptions_hash],
    ['ens_cap_permyriad', r.ens_cap_permyriad],
    ['achieved_ens_permyriad', r.achieved_ens_permyriad],
    ['achieved_shed_hours', r.achieved_shed_hours],
    ['mc_lole_h', r.mc_lole_h], ['certified', r.certified],
    ['cost_at_target_eur', r.cost_at_target_eur],
    ['period_basis', r.period_basis],
    ['solves_consumed', r.pipeline?.solves_consumed],
    ['budget_solves', r.pipeline?.budget_solves],
    ['aborted', r.pipeline?.aborted],
  ]
  const rows: unknown[][] = head.map(([k, v]) => ['headline', k, v ?? ''])
  for (const [section, status] of Object.entries(r.completeness ?? {})) {
    const note = r.sections?.[section]?.note
    rows.push(['completeness', section, note ? `${status}: ${note}` : status])
  }
  for (const [i, note] of (r.notes ?? []).entries()) rows.push(['note', String(i + 1), note])
  return rows
}

/** Tone for a pipeline stage status (P18 table). */
export function stageTone(status: string): string {
  switch (status) {
    case 'run': return 'text-accent'
    case 'failed': return 'text-danger'
    case 'aborted': return 'text-warn'
    default: return 'text-muted'
  }
}

/** Collapsible pipeline table: every stage, its status, solves and note. */
export function PipelineTable({ stages }: { stages: EhPipelineStage[] }) {
  const [open, setOpen] = useState(false)
  if (stages.length === 0) return null
  return (
    <div className="flex flex-col gap-1" data-testid="eh-pipeline">
      <button type="button" onClick={() => setOpen(o => !o)}
        data-testid="eh-pipeline-toggle" aria-expanded={open}
        className="self-start text-[10px] font-semibold uppercase tracking-wide text-muted hover:text-text">
        Pipeline ({stages.length} stages) {open ? '▾' : '▸'}
      </button>
      {open && (
        <div className="overflow-x-auto">
          <table className="w-full text-[10px]" data-testid="eh-pipeline-table">
            <thead className="text-muted">
              <tr>
                <th className="text-left font-medium py-1 pr-3">Stage</th>
                <th className="text-left font-medium py-1 pr-3">Status</th>
                <th className="text-right font-medium py-1 pr-3">Solves</th>
                <th className="text-left font-medium py-1">Note</th>
              </tr>
            </thead>
            <tbody>
              {stages.map(s => (
                <tr key={s.stage} className="border-t border-border/50"
                    data-testid={`eh-pipeline-row-${s.stage}`}>
                  <td className="py-0.5 pr-3 font-mono">{s.stage}</td>
                  <td className={`py-0.5 pr-3 ${stageTone(s.status)}`}
                      data-status={s.status}>{s.status}</td>
                  <td className="py-0.5 pr-3 text-right font-mono">
                    {s.solves_charged ?? 0}
                  </td>
                  <td className="py-0.5 text-muted">{s.note ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/** CSV rows for DtC planning contingencies. */
export function dtcPlanningCsvRows(table: EhDtcPlanningTable): unknown[][] {
  return (table.contingencies ?? []).map(c => [
    c.contingency,
    c.status,
    c.cost_at_target_eur ?? '',
    c.built_p_nom_mw ?? '',
    c.condition ?? '',
  ])
}

function CsvButton({
  testId, label, onClick, disabled,
}: { testId: string; label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      disabled={disabled}
      className="px-2 py-0.5 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent disabled:opacity-40"
    >
      {label}
    </button>
  )
}

/** `value`, settled for `ms` (the latest value wins). */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return settled
}

const PREDICTION_LABEL: Record<string, string> = {
  may_skip_budget: 'may skip (budget)',
  skipped_budget: 'skipped (budget)',
  not_established: 'not established',
  fails: 'fails',
  not_reached: 'not reached',
}

/** Pre-run readiness: what the study will find and which stages will run. */
export function ReadinessSummary({ r }: { r: EhReadiness }) {
  const skipped = r.stages.filter(s =>
    s.prediction !== 'run' && s.prediction !== 'not_requested')
  return (
    <div className="flex flex-col gap-0.5 text-[10px] border border-border/60 rounded p-2"
         data-testid="eh-readiness">
      <span className="uppercase tracking-wide font-semibold text-muted">Readiness</span>
      <span data-testid="eh-readiness-import">
        <span className="text-muted">Import Links </span>
        {r.import.links.length ? r.import.links.join(', ') : 'none'}
        <span className="text-muted"> (rule: {r.import.rule})</span>
      </span>
      <span data-testid="eh-readiness-critical">
        <span className="text-muted">Critical buses </span>
        {r.critical_buses.length ? r.critical_buses.join(', ') : 'none tagged'}
      </span>
      <span data-testid="eh-readiness-boundary"
            className={r.mc_boundary.ok ? '' : 'text-warn'}>
        <span className="text-muted">MC hub boundary </span>
        {r.mc_boundary.ok ? (r.mc_boundary.hub_buses ?? []).join(', ') : r.mc_boundary.error}
      </span>
      <span data-testid="eh-readiness-solves">
        <span className="text-muted">Estimated solves </span>
        {r.estimated_solves} / {r.budget_solves}
        <span className="text-muted"> · Class-B Links {r.class_b.k}</span>
      </span>
      {skipped.length > 0 && (
        <ul className="text-warn" data-testid="eh-readiness-skipped">
          {skipped.map(s => (
            <li key={s.stage}>
              {s.stage} — {PREDICTION_LABEL[s.prediction] ?? s.prediction}
              {s.reason ? `: ${s.reason}` : ''}
            </li>
          ))}
        </ul>
      )}
      {r.warnings.map((w, i) => (
        <span key={i} className="text-warn">{w}</span>
      ))}
    </div>
  )
}

// The study record's poll lives in ./ehStudyPoll (shared with the greeting).
export { ehStudyRefetchInterval }

/** Everything a new study makes stale — the panel's `invalidateAll` set,
 *  shared with the hub-design Goal card's Run. */
export function ehStudyQueryKeys(project: string | null) {
  return [
    nk(project, 'results', 'eh_study'),
    nk(project, 'results', 'eh_reference_design'),
    nk(project, 'results', 'eh_redundancy'),
    nk(project, 'results', 'eh_levers'),
    nk(project, 'results', 'eh_dtc'),
    nk(project, 'results', 'eh_dtc_planning'),
    // P24: the hub-design cards' review (GET /results/eh_review) goes stale
    // with the study.
    nk(project, 'results', 'eh_review'),
  ]
}

export function EhReferenceDesignPanel() {
  const currentProject = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [archetype, setArchetype] = useState<EhArchetype>('strong_grid')
  const [blocked, setBlocked] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [form, setForm] = useState<PackForm>(EMPTY_PACK_FORM)
  const built = buildEhStudyBody(archetype, form)
  const readinessBudget = useDebounced(built.body?.budget_solves, 400)
  // The stages and overrides that will run, settled like the budget.
  const readinessPreview = useDebounced(JSON.stringify({
    stages: built.body?.stages, pack_overrides: built.body?.pack_overrides,
  }), 400)
  const { data: template } = useQuery({
    queryKey: nk(currentProject, 'adequacy', 'eh_template'),
    queryFn: () => resultsApi.getEhTemplate(currentProject ?? ''),
    enabled: open && !!currentProject,
    staleTime: Infinity,
  })
  // Hover help for every pack control (P21 catalogue — the tour's and the
  // assistant's wording); undefined until the catalogue loads.
  const { data: guide } = useGuide()
  const fieldTip = (key: string): string | undefined => guide?.fields?.[key]
  const askAssistant = (text: string) => {
    useUIStore.getState().setAssistantDockOpen(true)
    useChatStore.getState().seedComposer(text)
  }
  const applyTemplate = (meta: EhTemplateMeta) => {
    setArchetype(meta.recommended_archetype)
    setForm(formFromTemplate(meta))
  }
  // P19–P22 gate: "open the panel and press Run" on a template project must
  // run the RECOMMENDED pack, not the strong_grid default — preselect once per
  // project, and only while the user has not touched the form.
  const preselectedFor = useRef<string | null>(null)
  useEffect(() => {
    if (!template || preselectedFor.current === currentProject) return
    preselectedFor.current = currentProject
    const untouched = archetype === 'strong_grid'
      && JSON.stringify(form) === JSON.stringify(EMPTY_PACK_FORM)
    if (untouched) applyTemplate(template)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [template, currentProject])
  const setField = (k: keyof PackForm) =>
    (e: { target: { value: string } }) => setForm(f => ({ ...f, [k]: e.target.value }))

  const studyKey = nk(currentProject, 'results', 'eh_study')
  const reportKey = nk(currentProject, 'results', 'eh_reference_design')
  const redKey = nk(currentProject, 'results', 'eh_redundancy')
  const levKey = nk(currentProject, 'results', 'eh_levers')
  const dtcKey = nk(currentProject, 'results', 'eh_dtc')
  const dtcPlanKey = nk(currentProject, 'results', 'eh_dtc_planning')

  const { data: studyData } = useQuery({
    queryKey: studyKey,
    queryFn: () => resultsApi.getEhStudy(),
    refetchInterval: ehStudyRefetchInterval,
  })
  const study = (studyData ?? null) as EhStudyPayload | null
  const running = study?.status === 'running'
  useStudyFinishedInvalidation(studyData === undefined ? undefined : study?.status ?? null)

  // Finished cue (click-through obstacles 1/4): the report lands below the
  // fold with nothing saying the run ended. Only a running → done transition
  // seen while mounted shows it; a fresh mount on a done study does not.
  const studyStatus = study?.status ?? null
  const prevStudyStatus = useRef<string | null>(null)
  const [finishedCue, setFinishedCue] = useState(false)
  useEffect(() => {
    const prev = prevStudyStatus.current
    prevStudyStatus.current = studyStatus
    if (studyStatus === 'running') { setFinishedCue(false); return }
    if (prev !== 'running') return
    if (studyStatus === 'done') setFinishedCue(true)
    else if (studyStatus === 'failed' || studyStatus === 'aborted') {
      // No cue for a failed / stopped run: bring its message into view once.
      const id = studyStatus === 'failed' ? 'eh-error' : 'eh-aborted'
      document.querySelector(`[data-testid="${id}"]`)
        ?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
    }
  }, [studyStatus])
  useEffect(() => {
    setFinishedCue(false)
    prevStudyStatus.current = null
  }, [currentProject])
  // P24: the hub-design Results card's "Open full report" — open the
  // collapsed panel and scroll the report into view once it renders.
  const ehReportRequest = useUIStore(s => s.ehReportRequest)
  const scrollToReport = useRef(false)
  useEffect(() => {
    if (!ehReportRequest) return
    useUIStore.getState().clearEhReportRequest()
    scrollToReport.current = true
    setOpen(true)
  }, [ehReportRequest])
  const viewReport = () => {
    setFinishedCue(false)
    document.querySelector('[data-testid="eh-report"]')
      ?.scrollIntoView?.({ block: 'start', behavior: 'smooth' })
  }
  // Readiness copies the network under its lock: not while a study runs,
  // and not on every keystroke of the budget field (debounced above).
  const { data: readiness } = useQuery({
    queryKey: [...nk(currentProject, 'results', 'eh_readiness'), archetype,
      form.dtcAttribution || null,
      readinessBudget ?? null, readinessPreview],
    queryFn: () => resultsApi.getEhReadiness(
      archetype, readinessBudget, form.dtcAttribution || undefined,
      JSON.parse(readinessPreview) as {
        stages?: string[]; pack_overrides?: Record<string, unknown> }),
    enabled: open && !running,
  })

  const { data: reportData } = useQuery({
    queryKey: reportKey,
    queryFn: () => resultsApi.getEhReferenceDesign(),
    enabled: !running,
  })
  const { data: redundancy } = useQuery({
    queryKey: redKey,
    queryFn: () => resultsApi.getEhRedundancy(),
    enabled: !running,
  })
  const { data: levers } = useQuery({
    queryKey: levKey,
    queryFn: () => resultsApi.getEhLevers(),
    enabled: !running,
  })
  const { data: dtcStress } = useQuery({
    queryKey: dtcKey,
    queryFn: () => resultsApi.getEhDtc(),
    enabled: !running,
  })
  const { data: dtcPlanning } = useQuery({
    queryKey: dtcPlanKey,
    queryFn: () => resultsApi.getEhDtcPlanning(),
    enabled: !running,
  })

  // While a new study runs, the previous report and sibling tables describe
  // a different run — hide them rather than show them under "Studying…".
  const report: EhReferenceDesignReport | null = running ? null
    : study?.report
      ?? ((reportData ?? null) as EhReferenceDesignReport | null)

  // Exports read the STORED report (GET /eh_reference_design), which a later
  // foreground solve clears while the study record keeps its copy — both
  // exports then refuse together rather than disagree (P18 gate).
  const storedReport = running ? null
    : (reportData ?? null) as EhReferenceDesignReport | null
  const hasReport = report != null
  useEffect(() => {
    if (!open || !hasReport || !scrollToReport.current) return
    scrollToReport.current = false
    // After the frame that lays the report out.
    requestAnimationFrame(() => document.querySelector('[data-testid="eh-report"]')
      ?.scrollIntoView?.({ block: 'start', behavior: 'smooth' }))
  }, [open, hasReport])
  const redTable = running ? null : (redundancy ?? null) as EhRedundancyTable | null
  const levTable = running ? null : (levers ?? null) as EhLeverTable | null
  const dtcTable = running ? null : (dtcStress ?? null) as EhDtcStressTable | null
  const dtcPlanTable = running ? null
    : (dtcPlanning ?? null) as EhDtcPlanningTable | null

  const invalidateAll = () => {
    setExportError(null)
    for (const key of ehStudyQueryKeys(currentProject)) {
      void qc.invalidateQueries({ queryKey: key })
    }
  }

  // P18: the durable export body, fetched fresh at click so the file is the
  // stored report (not a render-time copy of it).
  const [exportError, setExportError] = useState<string | null>(null)
  const exportJson = useMutation({
    mutationFn: () => resultsApi.getEhReferenceDesign(),
    onMutate: () => setExportError(null),
    onSuccess: (body: unknown) => {
      if (body == null) {
        setExportError('No stored reference design to export yet.')
        return
      }
      const r = body as EhReferenceDesignReport
      downloadJSON(
        `eh-reference-design-${r.archetype}-${String(r.pack_hash ?? '').slice(0, 8)}.json`,
        body)
    },
    onError: (e: unknown) => setExportError(blockerMessage(e)),
  })

  const run = useMutation({
    mutationFn: () => resultsApi.startEhStudy(built.body!),
    onMutate: () => setBlocked(null),
    onSuccess: () => invalidateAll(),
    onError: (e: unknown) => setBlocked(blockerMessage(e)),
  })

  const abort = useMutation({
    mutationFn: () => resultsApi.abortEhStudy(),
    onSuccess: () => void qc.invalidateQueries({ queryKey: studyKey }),
  })

  const completeness = useMemo(
    () => completenessRows(report?.completeness),
    [report?.completeness],
  )
  const cert = report ? certificationHeadline(report) : null
  const frPoints = report ? frontierPoints(report) : []
  const frPayload = report?.sections?.frontier?.payload as
    | EhFrontierPayload | null | undefined
  const fmeaUnsolved = ((report?.sections?.fmea_top?.payload as
    | { unsolved?: { id: string; status: string }[] } | null | undefined)
    ?.unsolved) ?? []
  // knee_index indexes the OK points (loosest first), not all points.
  const okFr = frPoints.filter(p => p.status === 'ok')
  // Owner's Q3 rule (2026-09-29): a knee needs at least three solved points
  // — a report stored before the rule may still carry one from two.
  const kneeTarget = okFr.length >= MIN_KNEE_POINTS && frPayload?.knee_index != null
    ? okFr[frPayload.knee_index]?.target_permyriad : undefined
  const kneeNote = okFr.length > 0 && okFr.length < MIN_KNEE_POINTS
    ? (frPayload?.knee_note
      ?? `Knee not established: needs at least ${MIN_KNEE_POINTS} solved points (${okFr.length} solved).`)
    : null
  const meCarriers = report ? multiEnergyCarrierEntries(report) : []
  const meLoads = report ? multiEnergyLoadEntries(report) : []
  const certification = report ? certificationPayload(report) : null
  const fmeaTop = report ? fmeaTopModes(report) : []
  const fmeaClassABlock = report ? fmeaClassA(report) : null
  const fmeaClassAReason = fmeaClassABlock?.status === 'not_established'
    ? (fmeaClassABlock.reason ?? null) : null
  const lcoh = report ? lcohChip(report) : null

  const selected = ARCHETYPES.find(a => a.id === archetype)!
  const selectedId = redTable?.selection?.selected_id ?? null
  const hasAnyTable = Boolean(
    (redTable?.options?.length)
    || (levTable?.options?.length)
    || (dtcTable?.contingencies?.length)
    || (dtcPlanTable?.contingencies?.length),
  )

  return (
    <section
      className="border border-border rounded"
      data-testid="eh-reference-design-panel"
    >
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        data-testid="eh-reference-design-toggle"
        className="w-full flex items-center gap-2 px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted hover:text-accent"
      >
        <Hexagon size={11} /> Energy Hub reference design {open ? '▾' : '▸'}
      </button>
      {open && (
        <div className="p-3 flex flex-col gap-3">
          {finishedCue && (
            <button type="button" data-testid="eh-study-finished-cue"
              onClick={viewReport}
              className="sticky top-0 z-10 self-start px-2.5 py-0.5 rounded-full border border-success/50 bg-bg text-[10px] font-semibold text-success shadow-sm hover:bg-success/10">
              Study finished — view report
            </button>
          )}
          <div className="flex items-center gap-2">
            <GuideButton tourId="eh_study" testId="eh-guide-button" />
            <GuideButton tourId="eh_tagging" testId="eh-tagging-guide-button"
                         label="How to tag the network"
                         prepare={() => prepareTaggingTour(qc, currentProject)} />
            <button type="button" data-testid="eh-ask-assistant"
              onClick={() => askAssistant(report
                ? 'Review my latest Energy Hub study: what did it establish, '
                  + 'what failed or is not established, and what do you '
                  + 'recommend changing? Offer the changes before applying them.'
                : 'Help me set up an Energy Hub study for this network: which '
                  + 'archetype fits, what must I tag, and what should I enter?')}
              title="Opens the assistant with a request prefilled — nothing is sent or changed until you send it and confirm any action."
              className="inline-flex items-center gap-1 px-2 py-0.5 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent">
              {report ? 'Ask the assistant to review' : 'Ask the assistant'}
            </button>
          </div>
          <p className="text-[11px] text-muted">
            Runs an Energy Hub archetype pack through the reference-design
            pipeline (apply pack → ENS solve → frontier → MC LOLE certify →
            FMEA top-N → assemble, plus any levers the pack enables). Produces one{' '}
            <code className="font-mono">ReferenceDesignReport</code> linking
            availability and cost. Sibling tables (redundancy, levers, DtC)
            appear when those stages ran. Shares the study mesh — one study at
            a time.
          </p>

          <label className="flex flex-col gap-1 text-[10px] text-muted">
            <span className="uppercase tracking-wide font-semibold">
              <InfoTip text={fieldTip('archetype')}>Archetype</InfoTip>
            </span>
            <select
              data-testid="eh-archetype"
              value={archetype}
              disabled={running}
              onChange={e => setArchetype(e.target.value as EhArchetype)}
              className="bg-bg border border-border rounded px-2 py-1 text-[11px] text-text"
            >
              {ARCHETYPES.map(a => (
                <option key={a.id} value={a.id}>{a.label}</option>
              ))}
            </select>
            <span className="text-muted">{selected.blurb}</span>
          </label>

          {/* Stays while a study runs: it is the context the run was set up from. */}
          {template && (
            <div className="flex flex-col gap-1 text-[10px] border border-accent/40 rounded p-2"
                 data-testid="eh-template-banner">
              <span>
                <span className="font-semibold text-text">{template.name}</span>
                <span className="text-muted"> template — recommended pack </span>
                <span className="font-mono">{template.recommended_archetype}</span>
              </span>
              {(template.study_notes ?? []).map((note, i) => (
                <span key={i} className="text-muted">• {note}</span>
              ))}
              {template.provenance && (
                <span className="text-muted italic">{template.provenance}</span>
              )}
              <button type="button" data-testid="eh-template-apply"
                onClick={() => applyTemplate(template)}
                disabled={running}
                className="self-start px-2 py-0.5 border border-accent rounded text-accent hover:bg-accent/10 disabled:opacity-50">
                Use recommended settings
              </button>
            </div>
          )}
          {readiness && !running && (
            <ReadinessSummary r={readiness as EhReadiness} />
          )}
          {running && (
            <p className="text-[10px] text-muted border border-border/60 rounded p-2"
               data-testid="eh-readiness-paused">
              Readiness is paused while the study runs
            </p>
          )}

          <div className="flex flex-col gap-1.5">
            <button
              type="button"
              onClick={() => setSettingsOpen(o => !o)}
              data-testid="eh-pack-settings-toggle"
              className="self-start text-[10px] uppercase tracking-wide font-semibold text-muted hover:text-accent"
            >
              Pack settings {settingsOpen ? '▾' : '▸'}
            </button>
            {settingsOpen && (
              <div className="flex flex-col gap-1.5 border border-border/60 rounded p-2"
                   data-testid="eh-pack-settings">
                <p className="text-[10px] text-muted">
                  Blank fields keep the archetype pack's own value.
                </p>
                <div className="flex flex-wrap gap-3">
                  {([
                    ['ensCap', 'ENS target (‱)', 'eh-pack-ens-cap', true, 'ens_cap_permyriad'],
                    ['loleTarget', 'LOLE target (h/yr)', 'eh-pack-lole-target', true, 'target_lole_h'],
                    ['importMw', 'Import cap (MW)', 'eh-pack-import-mw',
                      archetype === 'weak_flexible', 'import_p_nom_mw'],
                    ['importEnergy', 'Import energy (MWh/yr)', 'eh-pack-import-energy',
                      archetype === 'weak_flexible', 'import_energy_mwh_per_year'],
                    ['budget', 'Budget (LP solves)', 'eh-pack-budget', true, 'budget_solves'],
                    ['draws', 'MC draws', 'eh-pack-draws', true, 'mc_draws'],
                    ['seed', 'MC seed', 'eh-pack-seed', true, 'mc_seed'],
                  ] as [keyof PackForm, string, string, boolean, string][])
                    .filter(([, , , show]) => show)
                    .map(([key, label, testId, , guideKey]) => (
                      <label key={key} className="flex flex-col gap-0.5 text-[10px] text-muted"
                             data-testid={`${testId}-label`}>
                        <InfoTip text={fieldTip(guideKey)}>{label}</InfoTip>
                        <input
                          type="number"
                          step="any"
                          data-testid={testId}
                          value={form[key] as string}
                          disabled={running}
                          onChange={setField(key)}
                          className="w-24 px-1 py-0.5 border border-border rounded bg-bg text-[10px] font-mono text-text"
                        />
                      </label>
                    ))}
                  {archetype === 'weak_flexible' && (
                    <label className="flex flex-col gap-0.5 text-[10px] text-muted">
                      <InfoTip text={fieldTip('dsr_buses')}>DSR buses (comma-separated)</InfoTip>
                      <input
                        type="text"
                        data-testid="eh-pack-dsr-buses"
                        value={form.dsrBuses}
                        disabled={running}
                        onChange={setField('dsrBuses')}
                        className="w-40 px-1 py-0.5 border border-border rounded bg-bg text-[10px] font-mono text-text"
                      />
                    </label>
                  )}
                </div>
                <label className="flex items-center gap-1 text-[10px] text-muted">
                  <InfoTip text={fieldTip('dtc_attribution')}>DtC attribution</InfoTip>
                  <select
                    data-testid="eh-pack-dtc-attribution"
                    value={form.dtcAttribution}
                    disabled={running}
                    onChange={e => setForm(f => ({
                      ...f, dtcAttribution: e.target.value as PackForm['dtcAttribution'] }))}
                    title="Per Load reports critical unserved by Load, shedding non-critical Loads first via a disclosed 5% VOLL priority (exact on loss-free paths); the default reports by bus."
                    className="px-1 py-0.5 border border-border rounded bg-bg text-[10px] text-text"
                  >
                    <option value="">By bus (default)</option>
                    <option value="per_load">Per Load</option>
                  </select>
                </label>
                <fieldset className="flex flex-wrap items-center gap-2 text-[10px] text-muted"
                          data-testid="eh-pack-levers">
                  <span title="Which design levers the levers stage compares. 'pack' keeps the archetype pack's own choice.">
                    <InfoTip text={fieldTip('levers')}>Levers</InfoTip>
                  </span>
                  {leverKeysFor(archetype).map(k => (
                    <label key={k} className="flex items-center gap-1 font-mono">
                      {k}
                      <select
                        data-testid={`eh-pack-lever-${k}`}
                        value={form.levers[k] ?? ''}
                        disabled={running}
                        onChange={e => setForm(f => ({
                          ...f, levers: { ...f.levers,
                            [k]: (e.target.value || undefined) as 'on' | 'off' | undefined },
                        }))}
                        className="px-1 py-0.5 border border-border rounded bg-bg text-[10px] text-text"
                      >
                        <option value="">pack</option>
                        <option value="on">on</option>
                        <option value="off">off</option>
                      </select>
                    </label>
                  ))}
                </fieldset>
                <fieldset className="flex flex-wrap items-center gap-2 text-[10px] text-muted">
                  <label className="flex items-center gap-1">
                    <input
                      type="checkbox"
                      data-testid="eh-pack-stages-default"
                      checked={form.stages === null}
                      disabled={running}
                      onChange={e => setForm(f => ({
                        ...f, stages: e.target.checked ? null : [],
                      }))}
                    />
                    <InfoTip text={fieldTip('stages')}>pack's default stages</InfoTip>
                  </label>
                  {form.stages !== null && OPTIONAL_STAGES.map(st => (
                    <label key={st} className="flex items-center gap-1 font-mono">
                      <input
                        type="checkbox"
                        data-testid={`eh-pack-stage-${st}`}
                        checked={form.stages!.includes(st)}
                        disabled={running}
                        onChange={e => setForm(f => ({
                          ...f,
                          stages: e.target.checked
                            ? [...(f.stages ?? []), st]
                            : (f.stages ?? []).filter(x => x !== st),
                        }))}
                      />
                      {st}
                    </label>
                  ))}
                </fieldset>
                {built.error && (
                  <p className="text-[10px] text-warn" data-testid="eh-pack-error">
                    {built.error}
                  </p>
                )}
              </div>
            )}
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              onClick={() => run.mutate()}
              disabled={running || built.error !== null}
              title={built.error ?? undefined}
              data-testid="eh-run"
              className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent disabled:opacity-50"
            >
              {running ? 'Studying…' : 'Run study'}
            </button>
            {running && (
              <button
                type="button"
                onClick={() => abort.mutate()}
                data-testid="eh-abort"
                className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[10px] text-muted hover:border-danger hover:text-danger"
                title="Stops at the next stage boundary; the pack overlay is restored."
              >
                <Square size={9} /> Abort
              </button>
            )}
            {study?.status === 'aborted' && (
              <span className="text-[10px] text-warn" data-testid="eh-aborted">
                Stopped — the report reflects stages that finished before abort,
                not a full reference design.
              </span>
            )}
            {blocked && (
              <span className="text-[10px] text-warn" data-testid="eh-blocked">
                Blocked: {blocked}
              </span>
            )}
            {study?.error && (
              <span className="text-[10px] text-danger" data-testid="eh-error">
                {study.error}
              </span>
            )}
          </div>

          {!study && !report && !hasAnyTable && (
            <p className="text-[10px] text-muted" data-testid="eh-not-run">
              No Energy Hub study has been run in this session yet. Pick an
              archetype and run the study to get a reference-design report.
            </p>
          )}

          {report && (
            <div
              className="flex flex-col gap-2 border border-border/60 rounded p-2"
              data-testid="eh-report"
            >
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
                <span data-testid="eh-report-archetype">
                  <span className="text-muted">Archetype </span>
                  <span className="text-text font-medium">{report.archetype}</span>
                </span>
                {report.ens_cap_permyriad != null && (
                  <span data-testid="eh-report-ens-cap">
                    <span className="text-muted">ENS cap </span>
                    <span className="text-text font-mono">
                      {report.ens_cap_permyriad}‱
                    </span>
                  </span>
                )}
                {report.achieved_ens_permyriad != null && (
                  <span data-testid="eh-report-ens-achieved">
                    <span className="text-muted">Achieved </span>
                    <span className="text-text font-mono">
                      {Number(report.achieved_ens_permyriad).toFixed(2)}‱
                    </span>
                  </span>
                )}
                {report.cost_at_target_eur != null && (
                  <span data-testid="eh-report-cost">
                    <span className="text-muted">Cost@target </span>
                    <span className="text-text font-mono">
                      {eur(report.cost_at_target_eur)}
                    </span>
                    {report.excludes_shed_cost !== false && (
                      <span className="text-muted"> excl. shed</span>
                    )}
                  </span>
                )}
                {cert && (
                  <span data-testid="eh-report-lole">
                    <span className="text-muted">MC LOLE </span>
                    <span className="text-text font-mono">
                      {cert.perYear.toFixed(2)} h/yr
                    </span>
                    {cert.ci && (
                      <span className="text-muted font-mono">
                        {' '}[{cert.ci[0].toFixed(2)}–{cert.ci[1].toFixed(2)}]
                      </span>
                    )}
                  </span>
                )}
                {cert?.verdict && (
                  <span
                    data-testid="eh-certification-verdict"
                    data-verdict={cert.verdict}
                    className={verdictTone(cert.verdict)}
                    title="MC LOLE certification (decision 2): certified only when the 95% CI upper bound is within the target"
                  >
                    {cert.verdict === 'pass' ? 'Certified' : 'Not certified'}
                    {' '}({cert.verdict}
                    {cert.target != null ? ` vs ${cert.target} h/yr` : ''})
                  </span>
                )}
                {report.pipeline?.solves_consumed != null && (
                  <span data-testid="eh-report-solves">
                    <span className="text-muted">Solves </span>
                    <span className="text-text font-mono">
                      {report.pipeline.solves_consumed}
                      {report.pipeline.budget_solves != null
                        ? ` / ${report.pipeline.budget_solves}` : ''}
                    </span>
                  </span>
                )}
                {report.tea?.lcoe_eur_per_mwh != null && (
                  <span data-testid="eh-report-lcoe">
                    <span className="text-muted">LCOE </span>
                    <span className="text-text font-mono">
                      {eur(report.tea.lcoe_eur_per_mwh)}/MWh
                    </span>
                  </span>
                )}
                {lcoh && lcoh.value != null && (
                  <span data-testid="eh-report-lcoh">
                    <span className="text-muted">LCOH </span>
                    <span className="text-text font-mono">
                      €{lcoh.value.toFixed(2)}/kg
                    </span>
                  </span>
                )}
                {lcoh && lcoh.value == null && lcoh.status && (
                  <span
                    data-testid="eh-report-lcoh-flag"
                    data-status={lcoh.status}
                    className={statusTone(lcoh.status)}
                    title={lcoh.note ?? undefined}
                  >
                    <span className="text-muted">LCOH </span>
                    {lcoh.status === 'skipped' ? 'n/a (no electrolyser)' : 'not established'}
                  </span>
                )}
              </div>

              {cert?.next && (
                <p className="text-[10px] text-muted" data-testid="eh-certification-next">
                  {cert.next}
                </p>
              )}

              <div className="flex items-center gap-2 flex-wrap">
                <button type="button" data-testid="eh-report-json"
                  onClick={() => void exportJson.mutate()}
                  disabled={exportJson.isPending || !storedReport}
                  title="The stable export shape served by GET /results/eh_reference_design"
                  className="px-2 py-0.5 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent disabled:opacity-50">
                  Download JSON
                </button>
                <CsvButton
                  testId="eh-report-summary-csv"
                  label="Summary CSV"
                  disabled={!storedReport}
                  onClick={() => storedReport && downloadCSV(
                    `eh-reference-design-${storedReport.archetype}.csv`,
                    REPORT_SUMMARY_CSV_HEADER, reportSummaryCsvRows(storedReport))}
                />
                {!storedReport && (
                  <span className="text-[10px] text-muted" data-testid="eh-report-not-stored">
                    Not exportable: the stored report was cleared by a later
                    solve — re-run the study to export it.
                  </span>
                )}
                {exportError && (
                  <span className="text-[10px] text-danger" data-testid="eh-report-json-error">
                    {exportError}
                  </span>
                )}
              </div>

              <PipelineTable stages={report.pipeline?.stages ?? []} />

              {(report.notes?.length ?? 0) > 0 && (
                <ul
                  className="flex flex-col gap-0.5 text-[10px] text-warn"
                  data-testid="eh-report-notes"
                >
                  {report.notes!.map((note, i) => <li key={i}>{note}</li>)}
                </ul>
              )}

              {completeness.length > 0 && (
                <ul
                  className="flex flex-wrap gap-1.5"
                  data-testid="eh-completeness"
                >
                  {completeness.map(({ name, status }) => (
                    <li
                      key={name}
                      className={`text-[10px] border border-border rounded px-1.5 py-0.5 ${statusTone(status)}`}
                      data-testid={`eh-section-${name}`}
                      data-status={status}
                      title={report.sections?.[name]?.note ?? undefined}
                    >
                      {name}: {status}
                    </li>
                  ))}
                </ul>
              )}

              {notEstablishedNotes(report).length > 0 && (
                <ul
                  className="flex flex-col gap-0.5 text-[10px] text-muted"
                  data-testid="eh-section-notes"
                >
                  {notEstablishedNotes(report).map(({ name, note }) => (
                    <li key={name} data-testid={`eh-section-note-${name}`}>
                      <span className="text-warn">{name}</span>: {note}
                    </li>
                  ))}
                </ul>
              )}

              {hasCertificationBlock(report) && (
                <div
                  className="flex flex-col gap-1 border-t border-border/50 pt-2"
                  data-testid="eh-certification"
                >
                  <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                    MC LOLE certification
                  </h4>
                  {certification && certification.mc_lole_h != null && (
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
                      <span data-testid="eh-certification-lole">
                        <span className="text-muted">LOLE </span>
                        <span className="text-text font-mono">
                          {Number(certification.mc_lole_h).toFixed(2)} h
                          {certification.horizon_years != null ? '/yr' : ''}
                          {cert?.ci
                            ? ` [${cert.ci[0].toFixed(2)}, ${cert.ci[1].toFixed(2)}]`
                            : ''}
                        </span>
                      </span>
                      {certification.eue_mwh != null && (
                        <span data-testid="eh-certification-eue">
                          <span className="text-muted">EUE </span>
                          <span className="text-text font-mono">
                            {Number(certification.eue_mwh).toFixed(2)} MWh
                          </span>
                        </span>
                      )}
                      {certification.n_samples != null && (
                        <span data-testid="eh-certification-samples">
                          <span className="text-muted">draws </span>
                          <span className="text-text font-mono">
                            {certification.n_samples}
                            {certification.converged === false ? ' (not converged)' : ''}
                          </span>
                        </span>
                      )}
                      {certification.ens_met != null && (
                        <span data-testid="eh-certification-ens">
                          <span className="text-muted">ENS target </span>
                          <span className="text-text">{certification.ens_met ? 'met' : 'missed'}</span>
                        </span>
                      )}
                      {importModelLabel(certification) && (
                        <span
                          data-testid="eh-certification-import"
                          data-import-model={certification.import_model
                            ?? certification.fleet_scope?.import_model ?? 'firm_block'}
                        >
                          <span className="text-muted">import </span>
                          <span className="text-text">{importModelLabel(certification)}</span>
                        </span>
                      )}
                    </div>
                  )}
                  {gridAreasSummary(certification?.fleet_scope) && (
                    <p className="text-[10px] text-muted" data-testid="eh-certification-grid-areas">
                      {gridAreasSummary(certification?.fleet_scope)}
                    </p>
                  )}
                  {commonModeLines(certification?.fleet_scope).length > 0 && (
                    <ul className="text-[10px] text-muted" data-testid="eh-certification-common-mode">
                      {commonModeLines(certification?.fleet_scope).map(line => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  )}
                  {certification?.fleet_scope?.note && (
                    <p className="text-[10px] text-muted" data-testid="eh-certification-scope-note">
                      {certification.fleet_scope.note}
                    </p>
                  )}
                  {report.sections?.certification?.note && (
                    <p className="text-[10px] text-muted" data-testid="eh-certification-note">
                      {report.sections.certification.note}
                    </p>
                  )}
                  {certification?.warning && (
                    <p className="text-[10px] text-muted" data-testid="eh-certification-warning">
                      {certification.warning}
                    </p>
                  )}
                </div>
              )}

              {hasGatesBlock(report) && (
                <div
                  className="flex flex-col gap-1 border-t border-border/50 pt-2"
                  data-testid="eh-gates"
                >
                  <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                    Dynamics gate
                  </h4>
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
                    {report.gates?.scr != null && (
                      <span
                        data-testid="eh-gates-scr"
                        data-scr={report.gates.scr}
                        className={scrTone(report.gates.scr)}
                      >
                        <span className="text-muted">SCR </span>
                        <span className="font-mono font-medium">
                          {report.gates.scr}
                        </span>
                      </span>
                    )}
                    {report.gates?.emt_recommended != null && (
                      <span data-testid="eh-gates-emt">
                        <span className="text-muted">EMT </span>
                        <span className="text-text">
                          {report.gates.emt_recommended
                            ? 'recommended'
                            : 'no'}
                        </span>
                      </span>
                    )}
                    {typeof report.sections?.gates?.payload?.min_scr === 'number' && (
                      <span data-testid="eh-gates-min-scr">
                        <span className="text-muted">min SCR </span>
                        <span className="text-text font-mono">
                          {Number(report.sections.gates.payload.min_scr).toFixed(2)}
                          {typeof report.sections.gates.payload.pass_scr === 'number'
                            ? ` (pass ≥ ${report.sections.gates.payload.pass_scr})`
                            : ''}
                        </span>
                      </span>
                    )}
                  </div>
                  {report.sections?.gates?.note && (
                    <p
                      className="text-[10px] text-muted"
                      data-testid="eh-gates-note"
                    >
                      {report.sections.gates.note}
                    </p>
                  )}
                </div>
              )}

              {frPoints.length > 0 && (
                <div
                  className="flex flex-col gap-1 border-t border-border/50 pt-2"
                  data-testid="eh-frontier"
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                      Cost vs ENS target (frontier)
                    </h4>
                    <span className="text-[10px] text-muted" data-testid="eh-frontier-basis">
                      cost excl. shed
                      {typeof report.sections?.frontier?.payload?.period_basis === 'string'
                        ? ` · ${String(report.sections.frontier.payload.period_basis)}`
                        : ''}
                    </span>
                    <CsvButton
                      testId="eh-frontier-csv"
                      label="CSV"
                      onClick={() => downloadCSV(
                        'eh-frontier.csv',
                        ['target_permyriad', 'status', 'total_system_cost_eur_ex_shed',
                         'achieved_ens_mwh', 'achieved_shed_hours', 'binding',
                         'period_basis', 'excludes_shed_cost'],
                        frontierCsvRows(report),
                      )}
                    />
                  </div>
                  {kneeNote && (
                    <p className="text-[10px] text-muted" data-testid="eh-frontier-knee-note">
                      {kneeNote.startsWith('Knee') ? kneeNote : `Knee not established: ${kneeNote}.`}
                    </p>
                  )}
                  {typeof report.sections?.frontier?.payload?.warning === 'string' && (
                    <p className="text-[10px] text-warn" data-testid="eh-frontier-warning">
                      {String(report.sections.frontier.payload.warning)}
                    </p>
                  )}
                  <div className="overflow-x-auto">
                    <table className="w-full text-[10px]">
                      <thead className="text-muted">
                        <tr>
                          <th className="text-right font-medium py-1 pr-3">Target ‱</th>
                          <th className="text-left font-medium py-1 pr-3">Status</th>
                          <th className="text-right font-medium py-1 pr-3">Cost (ex-shed)</th>
                          <th className="text-right font-medium py-1 pr-3">ENS</th>
                          <th className="text-right font-medium py-1">Shed h</th>
                        </tr>
                      </thead>
                      <tbody className="font-mono">
                        {frPoints.map((p, i) => {
                          const isPack = p.target_permyriad === frPayload?.pack_target_permyriad
                          const isKnee = p.target_permyriad === kneeTarget && p.status === 'ok'
                          return (
                            <tr
                              key={`${p.target_permyriad}:${i}`}
                              className={`border-t border-border/50 ${isPack ? 'text-accent' : ''}`}
                              data-testid={`eh-frontier-row-${i}`}
                              data-pack-target={isPack ? 'true' : 'false'}
                              data-knee={isKnee ? 'true' : 'false'}
                            >
                              <td className="py-0.5 pr-3 text-right">
                                {p.target_permyriad}
                                {isKnee && (
                                  <span className="text-warn font-sans" data-testid="eh-frontier-knee"> knee</span>
                                )}
                              </td>
                              <td className="py-0.5 pr-3 font-sans">{p.status}</td>
                              <td className="py-0.5 pr-3 text-right">
                                {p.point?.total_system_cost_eur != null
                                  ? eur(p.point.total_system_cost_eur) : '—'}
                              </td>
                              <td className="py-0.5 pr-3 text-right">
                                {cellNum(p.point?.achieved_ens_mwh, 'mwh')}
                              </td>
                              <td className="py-0.5 text-right">
                                {cell(p.point?.achieved_shed_hours)}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                  {report.sections?.frontier?.note && (
                    <p className="text-[10px] text-muted" data-testid="eh-frontier-note">
                      {report.sections.frontier.note}
                    </p>
                  )}
                </div>
              )}

              {fmeaTop.length > 0 && (
                <div
                  className="flex flex-col gap-1 border-t border-border/50 pt-2"
                  data-testid="eh-fmea-top"
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                      Residual failure modes (ENS plan)
                    </h4>
                    <CsvButton
                      testId="eh-fmea-top-csv"
                      label="CSV"
                      onClick={() => downloadCSV(
                        'eh-fmea-top.csv',
                        ['rank', 'class', 'component', 'name', 'criticality_eur_per_year',
                         'delta_eue_mwh', 'occurrence_per_year', 'severity_eur', 'engine'],
                        fmeaTopCsvRows(report),
                      )}
                    />
                  </div>
                  {fmeaMixesClasses(fmeaTop) && (
                    <p className="text-[10px] text-muted" data-testid="eh-fmea-top-engines-note">
                      Class B (Link outage, LP re-solve) and class A (unit
                      outage, COPT screening) are ranked separately: their
                      criticalities come from different engines.
                    </p>
                  )}
                  <div className="overflow-x-auto">
                    <table className="w-full text-[10px]">
                      <thead className="text-muted">
                        <tr>
                          <th className="text-right font-medium py-1 pr-3">#</th>
                          <th className="text-left font-medium py-1 pr-3">Class</th>
                          <th className="text-left font-medium py-1 pr-3">Component</th>
                          <th className="text-right font-medium py-1 pr-3">Criticality €/yr</th>
                          <th className="text-right font-medium py-1 pr-3">Occ./yr</th>
                          <th className="text-right font-medium py-1">ΔEUE</th>
                        </tr>
                      </thead>
                      <tbody className="font-mono">
                        {fmeaTop.map((m, i) => (
                          <tr
                            key={`${m.failure_class}:${m.mode_id}`}
                            className="border-t border-border/50"
                            data-testid={`eh-fmea-top-row-${i + 1}`}
                            data-rank={`${m.failure_class}${m.rank}`}
                            data-class={m.failure_class}
                          >
                            <td className="py-0.5 pr-3 text-right">
                              {fmeaMixesClasses(fmeaTop) ? `${m.failure_class}${m.rank}` : m.rank}
                            </td>
                            <td className="py-0.5 pr-3 font-sans"
                                title={m.failure_class === 'A'
                                  ? 'class A: unit forced outage, COPT screening (no LP solve)'
                                  : 'Class B: Link outage, LP re-solve on the frozen plan'}>
                              {m.failure_class}
                            </td>
                            <td className="py-0.5 pr-3 font-sans">
                              {m.component_class} {m.name ?? m.mode_id}
                            </td>
                            <td className="py-0.5 pr-3 text-right">
                              {m.criticality_eur_per_year != null
                                ? eur(m.criticality_eur_per_year) : '—'}
                            </td>
                            <td className="py-0.5 pr-3 text-right">
                              {m.occurrence_per_year != null
                                ? Number(m.occurrence_per_year).toFixed(2) : '—'}
                            </td>
                            <td className="py-0.5 text-right">
                              {cellNum(m.delta_eue_mwh, 'mwh')}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {fmeaUnsolved.length > 0 && (
                    <p className="text-[10px] text-warn" data-testid="eh-fmea-unsolved">
                      Unsolved outages (may be the worst cases):{' '}
                      {fmeaUnsolved.map(u => `${u.id} (${u.status})`).join(', ')}
                    </p>
                  )}
                  {report.sections?.fmea_top?.note && (
                    <p className="text-[10px] text-muted" data-testid="eh-fmea-top-note">
                      {report.sections.fmea_top.note}
                    </p>
                  )}
                  {fmeaClassAReason && (
                    <p className="text-[10px] text-muted" data-testid="eh-fmea-class-a-reason">
                      Class-A screening not established: {fmeaClassAReason}
                    </p>
                  )}
                  {fmeaCoptImportSummary(report) && (
                    <p className="text-[10px] text-muted" data-testid="eh-fmea-top-copt-import">
                      {fmeaCoptImportSummary(report)}
                    </p>
                  )}
                  {fmeaCoptNotes(report).length > 0 && (
                    <div className="text-[10px] text-muted" data-testid="eh-fmea-top-copt-notes">
                      {fmeaCoptNotes(report).map(line => <p key={line}>{line}</p>)}
                    </div>
                  )}
                  {fmeaImportRankingNote(report) && (
                    <p className="text-[10px] text-muted" data-testid="eh-fmea-top-import-ranking">
                      {fmeaImportRankingNote(report)}
                    </p>
                  )}
                </div>
              )}

              {hasMultiEnergyBlock(report) && (
                <div
                  className="flex flex-col gap-1 border-t border-border/50 pt-2"
                  data-testid="eh-multi-energy"
                >
                  <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                    Multi-energy ENS
                  </h4>
                  {meCarriers.length > 0 && (
                    <div
                      className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]"
                      data-testid="eh-multi-energy-by-carrier"
                    >
                      {meCarriers.map(({ carrier, mwh }) => (
                        <span key={carrier} data-testid={`eh-multi-energy-${carrier}`}>
                          <span className="text-muted">{carrier} </span>
                          <span className="text-text font-mono">
                            {fmtEnergy(mwh, 2)}
                          </span>
                        </span>
                      ))}
                    </div>
                  )}
                  {meLoads.length > 0 && (
                    <div
                      className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]"
                      data-testid="eh-multi-energy-by-load"
                    >
                      {meLoads.map(({ load, mwh }) => (
                        <span key={load} data-testid={`eh-multi-energy-load-${load}`}>
                          <span className="text-muted">{load} </span>
                          <span className="text-text font-mono">
                            {fmtEnergy(mwh, 2)}
                          </span>
                        </span>
                      ))}
                    </div>
                  )}
                  {typeof report.sections?.multi_energy?.payload?.attribution === 'string' && (
                    <p className="text-[10px] text-muted" data-testid="eh-multi-energy-attribution">
                      attribution: {String(report.sections.multi_energy.payload.attribution)}
                    </p>
                  )}
                  {report.sections?.multi_energy?.note && (
                    <p
                      className="text-[10px] text-muted"
                      data-testid="eh-multi-energy-note"
                    >
                      {report.sections.multi_energy.note}
                    </p>
                  )}
                </div>
              )}

            </div>
          )}

          {/* ── Redundancy ─────────────────────────────────────────────── */}
          {redTable?.options && redTable.options.length > 0 && (
            <div className="flex flex-col gap-1.5" data-testid="eh-redundancy">
              <div className="flex items-center gap-2">
                <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                  Redundancy
                </h4>
                {selectedId && (
                  <span className="text-[10px] text-accent" data-testid="eh-redundancy-selected">
                    selected: {selectedId}
                  </span>
                )}
                <CsvButton
                  testId="eh-redundancy-csv"
                  label="CSV"
                  onClick={() => downloadCSV(
                    'eh-redundancy.csv',
                    ['scenario_id', 'status', 'cost_at_target_eur',
                     'achieved_ens_mwh', 'meets_target', 'selection', 'condition'],
                    redundancyCsvRows(redTable),
                  )}
                />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[10px]">
                  <thead className="text-muted">
                    <tr>
                      <th className="text-left font-medium py-1 pr-3">Scenario</th>
                      <th className="text-left font-medium py-1 pr-3">Status</th>
                      <th className="text-right font-medium py-1 pr-3">Cost</th>
                      <th className="text-right font-medium py-1 pr-3">ENS</th>
                      <th className="text-left font-medium py-1">Meets</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {redTable.options.map(o => (
                      <tr
                        key={o.scenario_id}
                        className="border-t border-border/50"
                        data-testid={`eh-redundancy-row-${o.scenario_id}`}
                        data-selected={o.scenario_id === selectedId ? 'true' : 'false'}
                      >
                        <td className="py-0.5 pr-3 font-sans">{o.scenario_id}</td>
                        <td className="py-0.5 pr-3 font-sans">{o.status}</td>
                        <td className="py-0.5 pr-3 text-right">
                          {o.cost_at_target_eur != null ? eur(o.cost_at_target_eur) : '—'}
                        </td>
                        <td className="py-0.5 pr-3 text-right">
                          {cellNum(o.achieved_ens_mwh, 'mwh')}
                        </td>
                        <td className="py-0.5 font-sans">
                          {o.meets_target == null ? '—' : o.meets_target ? 'yes' : 'no'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── Levers ─────────────────────────────────────────────────── */}
          {levTable?.options && levTable.options.length > 0 && (
            <div className="flex flex-col gap-1.5" data-testid="eh-levers">
              <div className="flex items-center gap-2 flex-wrap">
                <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                  Levers{levTable.kind ? ` (${levTable.kind})` : ''}
                </h4>
                <CsvButton
                  testId="eh-levers-csv"
                  label="CSV"
                  onClick={() => downloadCSV(
                    'eh-levers.csv',
                    ['kind', 'value', 'unit', 'status', 'cost_at_target_eur',
                     'achieved_ens_mwh', 'meets_target', 'note'],
                    leverCsvRows(levTable),
                  )}
                />
              </div>
              {(levTable.skipped_kinds?.length ?? 0) > 0 && (
                <p className="text-[10px] text-warn" data-testid="eh-levers-skipped">
                  Soft-skipped: {levTable.skipped_kinds!.join('; ')}
                </p>
              )}
              <div className="overflow-x-auto">
                <table className="w-full text-[10px]">
                  <thead className="text-muted">
                    <tr>
                      <th className="text-left font-medium py-1 pr-3">Kind</th>
                      <th className="text-right font-medium py-1 pr-3">Value</th>
                      <th className="text-left font-medium py-1 pr-3">Status</th>
                      <th className="text-right font-medium py-1 pr-3">Cost</th>
                      <th className="text-left font-medium py-1">Note</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {levTable.options.map((o, i) => (
                      <tr
                        key={`${o.kind}:${o.value}:${i}`}
                        className="border-t border-border/50"
                        data-testid={`eh-lever-row-${i}`}
                      >
                        <td className="py-0.5 pr-3 font-sans">{o.kind}</td>
                        <td className="py-0.5 pr-3 text-right">
                          {o.value}{o.unit ? ` ${o.unit}` : ''}
                        </td>
                        <td className="py-0.5 pr-3 font-sans">{o.status}</td>
                        <td className="py-0.5 pr-3 text-right">
                          {o.cost_at_target_eur != null ? eur(o.cost_at_target_eur) : '—'}
                        </td>
                        <td className="py-0.5 font-sans text-muted">
                          {o.ineffective
                            ? (o.ineffective_reason ?? 'ineffective')
                            : (o.meets_target == null
                              ? ''
                              : o.meets_target ? 'meets' : 'miss')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── DtC stress ─────────────────────────────────────────────── */}
          {dtcTable?.contingencies && dtcTable.contingencies.length > 0 && (
            <div className="flex flex-col gap-1.5" data-testid="eh-dtc-stress">
              <div className="flex items-center gap-2 flex-wrap">
                <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                  DtC stress
                </h4>
                {dtcTable.attribution && (
                  <span className="text-[10px] text-muted" data-testid="eh-dtc-attribution">
                    {dtcAttributionLabel(dtcTable)}
                  </span>
                )}
                {dtcPriorityCaveat(dtcTable) && (
                  <span className="text-[10px] text-warn w-full"
                        data-testid="eh-dtc-priority-caveat">
                    {dtcPriorityCaveat(dtcTable)}
                  </span>
                )}
                {dtcTable.refused && (
                  <span className="text-[10px] text-danger w-full"
                        data-testid="eh-dtc-refused">
                    {dtcTable.refused}
                  </span>
                )}
                <CsvButton
                  testId="eh-dtc-stress-csv"
                  label="CSV"
                  onClick={() => downloadCSV(
                    'eh-dtc-stress.csv',
                    ['contingency', 'status', 'critical_unserved_mwh',
                     'noncritical_unserved_mwh', 'condition',
                     'critical_unserved_by_load'],
                    dtcStressCsvRows(dtcTable),
                  )}
                />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[10px]">
                  <thead className="text-muted">
                    <tr>
                      <th className="text-left font-medium py-1 pr-3">Contingency</th>
                      <th className="text-left font-medium py-1 pr-3">Status</th>
                      <th className="text-right font-medium py-1 pr-3">Critical unserved</th>
                      <th className="text-right font-medium py-1">Other unserved</th>
                      {dtcTable.attribution === 'per_load' && (
                        <th className="text-left font-medium py-1 pl-3">Critical by Load (MWh)</th>
                      )}
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {dtcTable.contingencies.map(c => (
                      <tr
                        key={c.contingency}
                        className="border-t border-border/50"
                        data-testid={`eh-dtc-stress-row-${c.contingency}`}
                      >
                        <td className="py-0.5 pr-3 font-sans">{c.contingency}</td>
                        <td className="py-0.5 pr-3 font-sans">{c.status}</td>
                        <td className="py-0.5 pr-3 text-right">
                          {cellNum(c.critical_unserved_mwh, 'mwh')}
                        </td>
                        <td className="py-0.5 text-right">
                          {cellNum(c.noncritical_unserved_mwh, 'mwh')}
                        </td>
                        {dtcTable.attribution === 'per_load' && (
                          <td className="py-0.5 pl-3 font-sans"
                              data-testid={`eh-dtc-by-load-${c.contingency}`}>
                            {byLoadText(c.critical_unserved_by_load) || '—'}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── DtC planning ───────────────────────────────────────────── */}
          {dtcPlanTable?.contingencies && dtcPlanTable.contingencies.length > 0 && (
            <div className="flex flex-col gap-1.5" data-testid="eh-dtc-planning">
              <div className="flex items-center gap-2">
                <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                  DtC planning
                </h4>
                <CsvButton
                  testId="eh-dtc-planning-csv"
                  label="CSV"
                  onClick={() => downloadCSV(
                    'eh-dtc-planning.csv',
                    ['contingency', 'status', 'cost_at_target_eur',
                     'built_p_nom_mw', 'condition'],
                    dtcPlanningCsvRows(dtcPlanTable),
                  )}
                />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[10px]">
                  <thead className="text-muted">
                    <tr>
                      <th className="text-left font-medium py-1 pr-3">Contingency</th>
                      <th className="text-left font-medium py-1 pr-3">Status</th>
                      <th className="text-right font-medium py-1 pr-3">Cost</th>
                      <th className="text-right font-medium py-1">Built</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {dtcPlanTable.contingencies.map(c => (
                      <tr
                        key={c.contingency}
                        className="border-t border-border/50"
                        data-testid={`eh-dtc-planning-row-${c.contingency}`}
                      >
                        <td className="py-0.5 pr-3 font-sans">{c.contingency}</td>
                        <td className="py-0.5 pr-3 font-sans">{c.status}</td>
                        <td className="py-0.5 pr-3 text-right">
                          {cellNum(c.cost_at_target_eur, 'eur')}
                        </td>
                        <td className="py-0.5 text-right">
                          {cellNum(c.built_p_nom_mw, 'mw')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
