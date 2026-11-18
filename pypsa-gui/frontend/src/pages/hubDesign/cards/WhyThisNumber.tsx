// "Why this number" (guided-results 02b, owner decision WHY-FULL-1): a disclosure under a study figure, closed by
// default, that reads GET /results/eh_study/why/{figure} when opened. It says what produced the figure and whether
// it is still current, the run's settings recorded and now, and the assumed defaults the study rested on, each with
// its source and one "Edit ›". Plain words only: no node ids, engine ids or route names (RAW-NAMES-1).
//
// Three states, never more lenient than the server: "Current" only when the walk compared everything and found
// nothing changed; "Out of date" names what changed; "Not compared" names what could not be checked (GK B W6).
import { useId, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { getEhStudyWhy, type EhStudyWhy, type EhStudyWhyFigure, type WhyState, type WhyStep } from '../../../api/results'
import type { EhStudyAssumption } from '../../../api/simulation'
import type { FlowSectionId, FlowTarget } from '../../../api/flow'
import { AssumedChip } from '../../../components/AssumedChip'
import { StatePill } from '../../../components/ResultState'
import { Skeleton } from '../../../components/Skeleton'
import { siteTypeLabel } from '../../../components/reliabilityGoal/SiteTypeSelect'
import { SECTION_META } from '../../../layout/flowSections'
import { openFlowTargetInPlace, withReturnFrom } from '../../../layout/flowNavigation'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { SECTION_STEP, useHubDesignStore } from '../hubDesignStore'
import { kpiMoney } from '../plainWords'
import { useHubStudy } from '../useHubData'

const INPUT = 'input:eh_study/'

/** Each input's plain name (UX 2, 4). An input not named here is never shown by its id. */
export const INPUT_WORDS: Record<string, string> = {
  'goal/value': 'Reliability goal',
  'goal/basis': 'Goal basis',
  'goal/unit': 'Goal unit',
  site_archetype: 'Site type',
  'mc/draws': 'Draws',
  'mc/seed': 'Random seed',
  'mc/cov_target': 'Convergence target',
  'mc/horizon_years': 'Years modelled',
  network_revision: 'Network version',
  'run/pack_overrides': 'Other site-type settings',
  'run/stages': 'Study stages',
  'run/budget_solves': 'Solve budget',
  'run/dsr_buses': 'Demand-response buses',
  'run/dtc_config': 'Critical-load settings',
  'run/dtc_attribution': 'Critical-load attribution',
  finance: 'Finance and currency settings',
  pack_prices: "The site type's prices",
  assumptions: 'Assumed inputs',
}

/** The run settings always listed; the other run choices are listed only when they did not compare equal. */
const CORE = ['goal/value', 'site_archetype', 'mc/draws', 'mc/seed', 'mc/cov_target', 'mc/horizon_years', 'network_revision']

const FIGURE_WORDS: Record<EhStudyWhyFigure, string> = {
  expected_shortfall: 'expected shortfall',
  design_cost: 'design cost',
  verdict: 'verdict',
}

/** The shown state: only the server's `fresh` is current; anything else it sends is never current (GK B W6). */
export type ShownState = 'current' | 'out_of_date' | 'not_compared'
export function shownState(state: WhyState | string | null | undefined): ShownState {
  return state === 'fresh' ? 'current' : state === 'stale' ? 'out_of_date' : 'not_compared'
}

const key = (s: WhyStep) => (s.node.startsWith(INPUT) ? s.node.slice(INPUT.length) : null)

/** A step's plain name: an assumed row by its own label, any other input by `INPUT_WORDS`. */
export function inputName(s: WhyStep): string {
  const k = key(s)
  if (k == null) return s.label
  if (k.startsWith('assumption/')) return s.label
  return INPUT_WORDS[k] ?? 'Another input'
}

/** A recorded or current value in words, for the input it belongs to. */
export function valueWords(k: string, v: unknown): string {
  if (v === undefined || v === null) return 'not recorded'
  if (k === 'site_archetype' && typeof v === 'string') return siteTypeLabel(v as never)
  if (k === 'goal/value') return v === 'no goal set' ? 'no goal set' : `${v} h per year`
  if (k === 'goal/basis') return v === 'h_per_year' ? 'per year' : String(v)
  if (k === 'goal/unit') return v === 'h' ? 'hours' : String(v)
  if (k === 'network_revision') return `version ${v}`
  if (k === 'mc/horizon_years' && typeof v === 'number') return `${Number(v.toPrecision(3))} years`
  if (typeof v === 'string') return v === "none (the study's default)" ? "the study's default" : v
  if (Array.isArray(v)) return v.length ? v.map(String).join(', ') : 'none'
  if (typeof v === 'object') return 'custom settings'
  return String(v)
}

/** Recorded and current values of one input, in words, never colour alone (UX 2, 4). */
export function recordedAndNow(s: WhyStep): { recorded: string; now: string } {
  const k = key(s) ?? ''
  const unrecorded = s.via?.recorded?.unrecorded
  const recorded = unrecorded != null || s.value == null ? 'not recorded' : valueWords(k, s.value)
  const state = shownState(s.state)
  if (state === 'not_compared') return { recorded, now: 'not compared' }
  if (state === 'current') return { recorded, now: recorded }
  if (s.broken?.reason?.startsWith('cannot be read now')) return { recorded, now: "can't be read now" }
  return { recorded, now: valueWords(k, s.broken?.compared?.current) }
}

/** The study finished at, as an ISO date and time (UTC). */
export function isoTime(epochSeconds: number | null | undefined): string | null {
  if (typeof epochSeconds !== 'number' || !Number.isFinite(epochSeconds)) return null
  return `${new Date(epochSeconds * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC`
}

const KNOWN_SECTIONS = new Set<string>(SECTION_META.map(m => m.id))

/** "Edit ›" for one assumed row (GK B W9, UX 2, 2): a section with its own Guided card is a user move to it;
 *  a unit's field opens in place; anything else opens in Expert, with "Back to" Results either way. */
export function editFor(row: EhStudyAssumption, unit: string | null): { label: string; run: () => void } | null {
  if (!KNOWN_SECTIONS.has(row.section)) return null
  const section = row.section as FlowSectionId
  const step = SECTION_STEP[section]
  if (step) {
    return { label: 'Edit ›', run: () => withReturnFrom(() => useHubDesignStore.getState().setStep(step, { user: true })) }
  }
  const target: FlowTarget = {
    section, panel: null, component: row.component || null, entity: unit || null, field: row.field || null,
  }
  return target.component && target.entity
    ? { label: 'Edit ›', run: () => openFlowTargetInPlace(target) }
    : { label: 'Edit in Expert ›', run: () => openFlowTargetInPlace(target) }
}

/** The failed read in words (GK B W7): no study, a study running, another project, or anything else. */
export function failureWords(err: unknown): string {
  const res = (err as { response?: { status?: number; data?: { detail?: { error_kind?: string } } } })?.response
  const kind = res?.data?.detail?.error_kind
  if (kind === 'study_not_run') {
    return res?.status === 409
      ? 'The study is running. Open this again when it finishes.'
      : 'No study has run in this session, so there is nothing to explain yet.'
  }
  if (kind === 'project_not_open') return 'This is read from the open project. Open the project again, then retry.'
  return 'The explanation could not be read.'
}

function names(steps: WhyStep[]): string {
  return [...new Set(steps.map(inputName))].join(', ')
}

function Outcome({ why, testId }: { why: EhStudyWhy; testId: string }) {
  const inputs = why.steps.filter(s => key(s) != null)
  const changed = inputs.filter(s => shownState(s.state) === 'out_of_date')
  const unchecked = inputs.filter(s => shownState(s.state) === 'not_compared')
  const state = shownState(why.outcome.state)
  return (
    <div data-testid={`${testId}-outcome`} data-state={state} className="flex flex-col gap-1">
      {state === 'current' && (
        <p className="flex flex-wrap items-center gap-1.5 text-text">
          <span className="whitespace-nowrap rounded-full bg-success/12 px-2 py-0.5 text-[11px] font-semibold text-success">Current</span>
          Nothing it rests on has changed since the study ran.
        </p>
      )}
      {changed.length > 0 && (
        <p data-testid={`${testId}-changed`} className="flex flex-wrap items-center gap-1.5 text-text">
          <StatePill state="stale" /> Changed since the study: {names(changed)}.
        </p>
      )}
      {unchecked.length > 0 && (
        <p data-testid={`${testId}-unchecked`} className="flex flex-wrap items-center gap-1.5 text-text">
          <span className="whitespace-nowrap rounded-full bg-muted/12 px-2 py-0.5 text-[11px] font-semibold text-muted">Not compared</span>
          Not compared: {names(unchecked)} could not be checked.
        </p>
      )}
      {why.outcome.truncated && <p className="text-muted">Only the first {why.steps.length} steps are listed.</p>}
    </div>
  )
}

function Settings({ why, testId }: { why: EhStudyWhy; testId: string }) {
  const rows = why.steps.filter(s => {
    const k = key(s)
    if (k == null || k.startsWith('assumption') || k === 'finance' || k === 'pack_prices') return false
    return CORE.includes(k) || shownState(s.state) !== 'current'
  })
  if (rows.length === 0) return null
  return (
    <table data-testid={`${testId}-settings`} className="w-full text-left">
      <caption className="text-left font-semibold text-text">Run settings</caption>
      <thead>
        <tr className="text-muted"><th scope="col" className="font-normal">Setting</th>
          <th scope="col" className="font-normal">Recorded</th><th scope="col" className="font-normal">Now</th></tr>
      </thead>
      <tbody>
        {rows.map(s => {
          const v = recordedAndNow(s)
          return (
            <tr key={s.node} data-testid={`${testId}-setting`} data-state={shownState(s.state)}>
              <th scope="row" className="pr-2 font-normal text-text">{inputName(s)}</th>
              <td className="pr-2 text-text">{v.recorded}</td>
              <td className={shownState(s.state) === 'current' ? 'text-text' : 'font-semibold text-warn'}>{v.now}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function Assumed({ why, rows, testId }: { why: EhStudyWhy; rows: EhStudyAssumption[]; testId: string }) {
  const steps = why.steps.filter(s => key(s)?.startsWith('assumption/'))
  if (steps.length === 0) return null
  const byKey = new Map(rows.map(r => [r.key, r]))
  return (
    <div className="flex flex-col gap-1">
      <span className="font-semibold text-text">Assumed defaults it rests on</span>
      <ul data-testid={`${testId}-assumed`} className="flex flex-col gap-1.5">
        {steps.map(s => {
          const rest = (key(s) as string).slice('assumption/'.length)
          const [rowKey, unit] = rest.includes('/') ? [rest.slice(0, rest.lastIndexOf('/')), rest.slice(rest.lastIndexOf('/') + 1)] : [rest, null]
          const row = byKey.get(rowKey.replace(/#\d+$/, ''))
          const edit = row ? editFor(row, unit ?? (row.entity && !row.entity.includes(', ') ? row.entity : null)) : null
          const state = shownState(s.state)
          return (
            <li key={s.node} data-testid={`${testId}-assumed-row`} data-state={state} className="flex flex-col gap-0.5">
              <span className="flex flex-wrap items-baseline gap-x-2 text-text">
                <span className="flex-1">{inputName(s)}: {row?.shown ?? valueWords('', s.value)}</span>
                {state === 'out_of_date' && <StatePill state="stale" />}
                {state === 'not_compared' && <span className="text-muted">not compared</span>}
                {edit && (
                  <button type="button" data-testid={`${testId}-edit`} onClick={edit.run}
                    className="shrink-0 text-[11px] text-accent hover:underline">{edit.label}</button>
                )}
              </span>
              <AssumedChip source={row?.reason ?? null} testId={`${testId}-chip`} />
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export function WhyThisNumber({ figure, value, testId = `hub-why-${figure}` }: {
  figure: EhStudyWhyFigure
  /** The figure as its tile shows it, money in the tiles' notation (`kpiMoney`). */
  value?: number | null
  testId?: string
}) {
  const [open, setOpen] = useState(false)
  const project = useUIStore(s => s.currentProject)
  const { study } = useHubStudy()
  const region = useId()
  const q = useQuery({
    queryKey: nk(project, 'results', 'eh_study_why', figure),
    queryFn: () => getEhStudyWhy(project as string, figure),
    enabled: open && !!project,
    retry: false,
    // Read on every open: the answer depends on edits made since.
    staleTime: 0,
    refetchOnMount: 'always',
  })
  const finished = isoTime(study?.finished_at)
  return (
    <div data-testid={testId} className="flex flex-col gap-1 text-[11.5px]">
      <button type="button" aria-expanded={open} aria-controls={region} data-testid={`${testId}-toggle`}
        onClick={() => setOpen(o => !o)}
        className="inline-flex items-center gap-1 self-start text-[11px] text-accent hover:underline">
        {open ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />} Why this number
      </button>
      {open && (
        <div id={region} className="flex flex-col gap-2 rounded border border-border bg-panel px-2 py-1.5">
          {q.isPending ? (
            <div role="status" aria-busy="true" data-testid={`${testId}-loading`} className="flex min-h-[72px] flex-col gap-2">
              <span className="sr-only">Reading why this number…</span>
              <Skeleton className="h-3 w-full" /><Skeleton className="h-3 w-full" /><Skeleton className="h-3 w-2/3" />
            </div>
          ) : q.isError ? (
            <p role="alert" data-testid={`${testId}-failed`} className="flex flex-wrap items-center gap-2 text-text">
              {failureWords(q.error)}
              <button type="button" data-testid={`${testId}-retry`} onClick={() => void q.refetch()}
                className="rounded border border-border px-2 py-0.5 text-[11px] text-accent hover:border-accent">Retry</button>
            </p>
          ) : (
            <>
              <p className="text-text" data-testid={`${testId}-source`}>
                The {FIGURE_WORDS[figure]}{value != null && figure === 'design_cost' ? ` (${kpiMoney(value)})` : ''} comes
                from the reliability study{finished ? `, finished ${finished}` : ''}.
              </p>
              <Outcome why={q.data} testId={testId} />
              <Settings why={q.data} testId={testId} />
              <Assumed why={q.data} rows={Array.isArray(study?.assumptions) ? study.assumptions : []} testId={testId} />
            </>
          )}
        </div>
      )}
    </div>
  )
}
