import { useMemo } from 'react'
import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import type { FlowSectionId } from '../../api/flow'
import type { Generator, Link } from '../../api/types'
import { networkApi } from '../../api/network'
import { simulationApi } from '../../api/simulation'
import { businessCaseApi } from '../../api/businessCase'
import { commercialApi } from '../../api/commercial'
import { campusApi } from '../../api/campusElectrical'
import { projectsApi } from '../../api/projects'
import { useAssetSchema } from '../../hooks/useAssetSchema'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { FIELD_LABELS } from '../../utils/fieldLabels'
import { STEP_LABELS } from '../modelHorizon/StepShell'
import { CAMPUS_KEY } from '../CampusElectricalPanel'
import { GRID_CODES_KEY } from '../CampusGridCodeSection'
import { buildScenarioForest, collectSubtree } from '../ScenariosPanel'
import { openFlowTarget, openSectionInExpert, withReturnFrom } from '../../layout/flowNavigation'

// The fields of a Guided section that only Expert edits (design §3.5): each is
// named with its value, and one "Open in Expert >" per Expert place goes there,
// leaving "Back to <section>" (GAP03-ADD-1). Read-only and frontend-only: the
// lines add no flow item and change no section state.

const NOT_SET = 'not set'
const LOADING = 'loading…'
const FAILED = 'could not be read'
const NOT_APPLICABLE = 'not used by this project (no grid-code study)'

const statusOf = (e: unknown): number | undefined =>
  (e as { response?: { status?: number } } | null)?.response?.status

/** Where a line is edited in Expert: `place` keys the one link per place (#1194). */
interface Home { place: string; open: () => void; label?: string }

export interface ExpertOnlyLine {
  key: string
  label: string
  value: string
  /** null: no Expert home; the line is plain words (#1252). */
  home: Home | null
}

/** A query's value in words: loading, failed, or `fmt`'s text ("not set" when empty). */
function read<T>(q: Pick<UseQueryResult<T>, 'isPending' | 'isError' | 'data'>, fmt: (d: T) => string | null): string {
  if (q.isError) return FAILED
  if (q.isPending) return LOADING
  return fmt(q.data as T) || NOT_SET
}

const names = (xs: Array<string | null | undefined>): string | null =>
  xs.filter((x): x is string => !!x).join(', ') || null

function inExpert(section: FlowSectionId, then?: () => void): () => void {
  return () => withReturnFrom(() => { openSectionInExpert(section); then?.() })
}

const useProject = () => useUIStore(s => s.currentProject)

/** True when the snapshots of a period skip time: sampled weeks, not a chronological run. */
export function hasGaps(snapshots: string[], periods?: Array<number | string>): boolean {
  const byPeriod = new Map<string, number[]>()
  snapshots.forEach((s, i) => {
    const t = Date.parse(s)
    if (Number.isNaN(t)) return
    const k = String(periods?.[i] ?? '')
    byPeriod.set(k, [...(byPeriod.get(k) ?? []), t])
  })
  for (const ts of byPeriod.values()) {
    const steps = ts.slice(1).map((t, i) => t - ts[i]).filter(d => d > 0)
    if (steps.length === 0) continue
    const step = Math.min(...steps)
    if (steps.some(d => d > step)) return true
  }
  return false
}

// ── 1 Project: the model horizon (Expert: Model horizon panel) ───────────────
function useProjectLines(): ExpertOnlyLine[] {
  const project = useProject()
  const snap = useQuery({ queryKey: nk(project, 'snapshots'), queryFn: networkApi.getSnapshots })
  const ip = useQuery({ queryKey: nk(project, 'investmentPeriods'), queryFn: networkApi.getInvestmentPeriods })
  const home: Home = { place: 'horizon', open: inExpert('project', () => useUIStore.getState().setSlidePanel('horizon')) }
  return [
    { key: 'investment_years', label: STEP_LABELS.years, home,
      value: read(ip, d => names((d?.periods ?? []).map(String))) },
    { key: 'representative_weeks', label: STEP_LABELS.sampling, home,
      value: read(snap, d => (!d || d.snapshots.length === 0 ? null
        : hasGaps(d.snapshots, d.periods) ? 'sampled' : 'no, a chronological year')) },
  ]
}

// ── 3 Connection: grid code and site connection (Expert: Grid connection panel) ──
function useConnectionLines(): ExpertOnlyLine[] {
  const project = useProject() ?? ''
  const campus = useQuery({ queryKey: CAMPUS_KEY(project), queryFn: () => campusApi.state(project), retry: false,
                            enabled: !!project })
  const codes = useQuery({ queryKey: GRID_CODES_KEY(project), queryFn: () => campusApi.gridCodes(project),
                           retry: false, enabled: !!project })
  const config = useQuery({ queryKey: nk(project, 'commercial', 'config'), queryFn: () => commercialApi.getCommercial() })
  const home: Home = { place: 'campusElectrical', open: inExpert('connection') }
  const poc = config.data?.poc_link
  // 409: not a hub (capacity-expansion) project, so the grid-code study does not
  // apply: plain words and no link (#1252), not "could not be read".
  const noStudy = [campus, codes].some(q => statusOf(q.error) === 409)
  if (noStudy) {
    return [
      { key: 'grid_code_profile', label: FIELD_LABELS.grid_code_profile, home: null,
        value: NOT_APPLICABLE },
      { key: 'grid_code_document', label: FIELD_LABELS.grid_code_document, home: null,
        value: NOT_APPLICABLE },
      { key: 'poc_link', label: FIELD_LABELS.poc_link, home, value: read(config, d => d?.poc_link ?? null) },
      { key: 'export_link', label: FIELD_LABELS.export_link, home,
        value: read(config, d => (poc ? d?.export_link ?? null : null)) },
      { key: 'timezone', label: FIELD_LABELS.timezone, home,
        value: read(config, d => (poc ? d?.timezone ?? null : null)) },
    ]
  }
  return [
    { key: 'grid_code_profile', label: FIELD_LABELS.grid_code_profile, home,
      value: read(campus, d => {
        const p = d?.settings?.profile
        return p ? (d.profiles[p] ?? p) : null
      }) },
    { key: 'grid_code_document', label: FIELD_LABELS.grid_code_document, home,
      value: read(codes, d => names((d?.documents ?? []).map(doc => doc.filename))) },
    { key: 'poc_link', label: FIELD_LABELS.poc_link, home, value: read(config, d => d?.poc_link ?? null) },
    { key: 'export_link', label: FIELD_LABELS.export_link, home,
      value: read(config, d => (poc ? d?.export_link ?? null : null)) },
    { key: 'timezone', label: FIELD_LABELS.timezone, home,
      value: read(config, d => (poc ? d?.timezone ?? null : null)) },
  ]
}

// ── 4 Loads: the demand-response tier (Expert: Solver settings) ──────────────
function useLoadsLines(): ExpertOnlyLine[] {
  const project = useProject()
  const cfg = useQuery({ queryKey: nk(project, 'solverConfig'), queryFn: simulationApi.getSolverConfig })
  const home: Home = { place: 'simparams', open: inExpert('loads', () => useUIStore.getState().setSlidePanel('simparams')) }
  const on = (d: typeof cfg.data) => (d?.dsr_share_of_load ?? 0) > 0 && (d?.dsr_buses ?? []).length > 0
  return [
    { key: 'dsr_share_of_load', label: FIELD_LABELS.dsr_share_of_load, home,
      value: read(cfg, d => (on(d)
        ? `${+((d!.dsr_share_of_load ?? 0) * 100).toFixed(1)}% of bus peak · ${names(d!.dsr_buses ?? [])}` : null)) },
    { key: 'dsr_price_eur_per_mwh', label: FIELD_LABELS.dsr_price_eur_per_mwh, home,
      value: read(cfg, d => (on(d) && (d!.dsr_price_eur_per_mwh ?? 0) > 0 ? `${d!.dsr_price_eur_per_mwh} €/MWh` : null)) },
  ]
}

// ── 5 Assets: unit commitment (Expert: the unit's Properties) ────────────────
type Committable = { committable?: boolean; p_min_pu?: number; min_up_time?: number; min_down_time?: number }

function useAssetsLines(): ExpertOnlyLine[] {
  const project = useProject()
  const gens = useQuery({ queryKey: nk(project, 'generators'), queryFn: networkApi.getGenerators })
  const links = useQuery({ queryKey: nk(project, 'links'), queryFn: networkApi.getLinks })
  const genSchema = useAssetSchema('Generator')
  const linkSchema = useAssetSchema('Link')
  return useMemo(() => {
    const units = [
      ...(gens.data ?? []).map(u => ({ cls: 'Generator', u: u as Generator & Committable })),
      ...(links.data ?? []).map(u => ({ cls: 'Link', u: u as Link & Committable })),
    ].filter(x => x.u.committable === true)
    const pending = gens.isPending || links.isPending
    const failed = gens.isError || links.isError
    if (!pending && !failed && units.length === 0) return []
    const labelOf = (cls: string, key: string): string | undefined =>
      (cls === 'Link' ? linkSchema : genSchema).data?.groups.flatMap(g => g.fields).find(f => f.key === key)?.label
    const schemaRead = { isPending: genSchema.isPending || linkSchema.isPending,
                         isError: genSchema.isError || linkSchema.isError, data: true }
    const value = failed ? FAILED : pending ? LOADING : read(schemaRead, () => names(units.map(({ cls, u }) => {
      const fields = [
        [labelOf(cls, 'p_min_pu'), `${+((u.p_min_pu ?? 0) * 100).toFixed(1)}%`],
        [labelOf(cls, 'min_up_time'), `${u.min_up_time ?? 0} h`],
        [labelOf(cls, 'min_down_time'), `${u.min_down_time ?? 0} h`],
      ].filter(([l]) => !!l).map(([l, v]) => `${l} ${v}`)
      return [u.name, ...fields].join(' · ')
    })))
    const first = units[0]
    const label = genSchema.data ? labelOf('Generator', 'committable') : labelOf('Link', 'committable')
    return [{
      key: 'committable', label: label ?? FIELD_LABELS.committable, value,
      home: first ? {
        place: `unit:${first.cls}:${first.u.name}`, label: `Open ${first.u.name} in Expert`,
        open: () => openFlowTarget({ section: 'assets', panel: null, component: first.cls, entity: first.u.name,
                                     field: 'committable' }),
      } : { place: 'assets', open: inExpert('assets') },
    }]
  }, [gens.data, gens.isPending, gens.isError, links.data, links.isPending, links.isError,
      genSchema.data, genSchema.isPending, genSchema.isError, linkSchema.data, linkSchema.isPending, linkSchema.isError])
}

// ── 8 Scenarios: the project's variants (Expert: Scenarios panel) ────────────
function useScenariosLines(): ExpertOnlyLine[] {
  const project = useProject()
  const projects = useQuery({ queryKey: ['projects'], queryFn: projectsApi.list, staleTime: 5_000 })
  const home: Home = { place: 'scenarios', open: inExpert('scenarios', () => useUIStore.getState().setSlidePanel('scenarios')) }
  return [{
    key: 'variants', label: FIELD_LABELS.variants, home,
    value: read(projects, d => {
      const tree = buildScenarioForest(d ?? []).map(collectSubtree).find(t => t.some(p => p.name === project))
      return names((tree ?? []).map(p => p.name).filter(n => n !== project))
    }),
  }]
}

// ── 10 Results: the business case (Expert: Results › Investment case) ────────
// Tariff, contracts, finance inputs and participants are edited in Guided
// section 7, so they are not named here; the business case's own lines and its
// break-even line are post-solve inputs with no Guided home (DCBC-SCOPE-1).
function useResultsLines(): ExpertOnlyLine[] {
  const project = useProject()
  const inputs = useQuery({ queryKey: nk(project, 'results', 'business_case', 'inputs'),
                            queryFn: () => businessCaseApi.getInputs() })
  const home: Home = { place: 'investment', open: inExpert('results', () => useUIStore.getState().requestResultsTab('investment')) }
  return [
    { key: 'business_case', label: FIELD_LABELS.business_case, home,
      value: read(inputs, d => {
        const bc = d?.status === 'not_set' ? null : d?.business_case
        const lines = names((bc?.inputs?.lines ?? []).map(l => l.name))
        if (!lines) return null
        return bc?.break_even_line ? `${lines} · break-even on ${bc.break_even_line}` : lines
      }) },
  ]
}

/** Per section, the hook that reads its Expert-only lines. */
export const EXPERT_ONLY: Partial<Record<FlowSectionId, () => ExpertOnlyLine[]>> = {
  project: useProjectLines,
  connection: useConnectionLines,
  loads: useLoadsLines,
  assets: useAssetsLines,
  scenarios: useScenariosLines,
  results: useResultsLines,
}

function Lines({ section, useLines }: { section: FlowSectionId; useLines: () => ExpertOnlyLine[] }) {
  const lines = useLines()
  if (lines.length === 0) return null
  const linked = new Set<string>()
  return (
    <div data-testid={`expert-only-${section}`} className="flex flex-col gap-1">
      <h3 className="text-[11px] font-medium text-muted">Set in Expert</h3>
      <ul className="flex flex-col gap-0.5">
        {lines.map(l => {
          const link = l.home && !linked.has(l.home.place) ? l.home : null
          if (l.home) linked.add(l.home.place)
          return (
            <li key={l.key} data-testid={`expert-only-line-${l.key}`}
              className="flex items-baseline gap-2 text-[12px] text-text">
              <span className="min-w-0 flex-1">
                {l.label}: <span className="text-muted">{l.value}</span>
              </span>
              {link && (
                <button type="button" data-testid={`expert-only-open-${l.key}`} onClick={link.open}
                  aria-label={link.label}
                  className="shrink-0 text-[11px] text-text underline">
                  Open in Expert &gt;
                </button>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/** The section's Expert-only fields, in Guided only: Expert already shows every field. */
export function ExpertOnlyLines({ section }: { section: FlowSectionId }) {
  const guided = useUIStore(s => s.uiMode) === 'guided'
  const useLines = EXPERT_ONLY[section]
  if (!guided || !useLines) return null
  // Keyed by section: each section's reader calls its own hooks.
  return <Lines key={section} section={section} useLines={useLines} />
}
