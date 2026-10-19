// The Guided report entry (UX overhaul S6, findings G4 and G6): one tile per
// category — Investment, Grid plan, Reliability, Operation — so Guided shows
// the whole picture at its own density, not only the reliability study. Each
// tile is a figure when one was computed, else a state in words (ResultState),
// and says where its detail lives. Pure: the hooks are in ChaptersSection.
import type { ResultStateKind } from '../../components/ResultState'
import type { CampusState, GfmSettingsState } from '../../api/campusElectrical'
import type { InvestmentCaseReportPayload, InvestmentCaseStudy, SimulationStatus } from '../../api/types'
import { RESULT_GROUPS, type ResultsGroup, type ResultsTab } from '../results/groups'
import { fmtCurrency } from '../../utils/format'
import { failureLine, runningLine } from '../campusRunProgress'

export type ChapterId = Exclude<ResultsGroup, 'summary'>

/** Where a tile's detail is: a Results tab, the campus panel, or a Guided step. */
export type ChapterOpen =
  | { panel: 'results'; tab: ResultsTab }
  | { panel: 'campusElectrical' }
  | { step: 'goal' }

export interface ChapterTile {
  id: ChapterId
  /** The result group's own label (RESULT_GROUPS), so the names match Expert. */
  group: string
  label: string
  /** The figure is the study's, and the project itself is not solved (G5). */
  fromStudy?: boolean
  /** '' unless the state shows a figure (ready / zero / stale). */
  value: string
  unit?: string
  basis?: string
  state: ResultStateKind
  reason?: string
  open: ChapterOpen
  /** The operation tile's control line (GFM-b): the grid-forming settings, and
   *  the re-solve an applied recommendation needs (owner decision 2026-10-15). */
  control?: { text: string; state: ResultStateKind; resolve?: boolean }
}

export interface ChapterInputs {
  investment: { study: InvestmentCaseStudy | null; report: InvestmentCaseReportPayload | null }
  /** `notHub`: the campus route answered that this project has no campus (409). */
  grid: { campus: CampusState | null; notHub: boolean }
  reliability: { hasResults: boolean; running: boolean; lole: number | null; target: number | null
                 verdict: 'pass' | 'fail' | 'inconclusive' | null
                 /** The project has its own solve; `false` marks the figure as the study's alone (G5). */
                 solved?: boolean
                 /** The review says the study is out of date (N4). */
                 stale?: boolean }
  /** `gfm`: the campus GFM settings (null when the project has no campus or no island). */
  operation: { status: SimulationStatus | null; gfm?: GfmSettingsState | null }
}

/** Why a study figure is dimmed (UX review N4): the network was solved or edited after the study. */
export const STALE_TILE_REASON = 'The network changed since the study ran. Run the study again to refresh.'

/** Whether the project itself has an hour-by-hour solve (stale counts: its figures exist). */
export function dispatchSolved(status: SimulationStatus | null | undefined): boolean {
  return !!status && status.dispatch != null && status.dispatch !== 'none' && num(status.objective) != null
}

/** The hover text of a figure marked "From the study" (G5). */
export const FROM_STUDY_HINT =
  'From the study, which ran on the design. The project itself has not been solved yet.'

const ORDER: ChapterId[] = ['investment', 'grid_plan', 'reliability', 'operation']
const groupLabel = (id: ChapterId) => RESULT_GROUPS.find(g => g.id === id)?.label ?? id
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

function investment({ study, report }: ChapterInputs['investment']): Omit<ChapterTile, 'id' | 'group'> {
  const open: ChapterOpen = { panel: 'results', tab: 'investment' }
  const label = 'What the design is worth'
  if (study?.status === 'running') {
    return { label, value: '', state: 'running', reason: 'The investment case is running.', open }
  }
  if (study && ['refused', 'error', 'failed'].includes(String(study.status))) {
    const why = study.message || study.error || study.error_code || 'see the investment case'
    return { label, value: '', state: 'unavailable', reason: `The investment case did not complete: ${why}.`, open }
  }
  if (!report) {
    return { label, value: '', state: 'not_run', open,
      reason: 'The investment case has not run. It turns the design’s costs and savings into NPV and IRR.' }
  }
  const post = num(report.npv_at_wacc)
  const payload = (report.sections?.project?.payload ?? {}) as Record<string, unknown>
  const pre = num(payload.project_pre_tax_npv)
  const figure = post ?? pre
  if (figure == null) {
    return { label, value: '', state: 'unavailable', open,
      reason: 'The investment case ran, but its NPV is not established; it says why.' }
  }
  const stale = study?.stale === true || study?.report?.stale === true || report.stale === true
  return {
    label, value: fmtCurrency(figure), state: stale ? 'stale' : 'ready', open,
    basis: post != null ? 'Net present value at the WACC, after tax'
      : 'Net present value before tax (no tax pack, so the after-tax figure is not established)',
    reason: stale ? 'The inputs changed since the case ran.' : undefined,
  }
}

function gridPlan({ campus, notHub }: ChapterInputs['grid']): Omit<ChapterTile, 'id' | 'group'> {
  const open: ChapterOpen = { panel: 'campusElectrical' }
  const label = 'Grid checks'
  if (notHub) {
    return { label, value: '', state: 'not_applicable', open,
      reason: 'This project has no grid-check study.' }
  }
  // A study run in progress, or the last one failed: say the stage, the time and the error.
  const going = runningLine(campus?.run_job)
  if (going) return { label, value: '', state: 'running', reason: going, open }
  const failed = failureLine(campus?.run_job)
  if (failed) return { label, value: '', state: 'unavailable', reason: failed, open }
  const rows = campus?.results?.compliance ?? null
  if (!rows || rows.length === 0) {
    return { label, value: '', state: 'not_run', open,
      reason: 'The grid checks (voltage, loading, reactive power) have not run.' }
  }
  const pass = rows.filter(r => r.status_with_measures === 'pass').length
  return {
    label, value: `${pass} of ${rows.length}`, unit: 'checks pass', open,
    state: campus?.stale ? 'stale' : 'ready',
    basis: 'Grid checks, with the proposed measures',
    reason: campus?.stale ? 'The network changed since the checks ran.' : undefined,
  }
}

function reliability(r: ChapterInputs['reliability']): Omit<ChapterTile, 'id' | 'group'> {
  const label = 'Expected shortfall'
  if (r.running) {
    return { label, value: '', state: 'running', reason: 'The reliability study is running.',
      open: { panel: 'results', tab: 'adequacy' } }
  }
  if (!r.hasResults) {
    return { label, value: '', state: 'not_run', open: { step: 'goal' },
      reason: 'The reliability study has not run. Set a goal and run it.' }
  }
  const open: ChapterOpen = { panel: 'results', tab: 'adequacy' }
  // No goal, so the study certified nothing: the cause is known and the fix is a step (UX review N13).
  if (r.lole == null && r.target == null) {
    return { label, value: '', state: 'not_modelled', open: { step: 'goal' },
      reason: 'No reliability goal is set, so the study gave no shortfall figure. Set an allowed shortfall in Goal and run it again.' }
  }
  if (r.lole == null) {
    return { label, value: '', state: 'unavailable', open,
      reason: 'The study ran, but its shortfall figure is not established.' }
  }
  const verdict = r.verdict === 'pass' ? 'Certified' : r.verdict === 'fail' ? 'Not certified'
    : r.verdict === 'inconclusive' ? 'Not decided' : null
  const basis = r.target == null ? 'No goal set'
    : `${verdict ? `${verdict}: ` : ''}the goal is ${Number(r.target.toPrecision(6))} h/yr`
  return { label, value: String(r.lole), unit: 'h/yr', state: r.stale ? 'stale' : 'ready', basis, open,
    reason: r.stale ? STALE_TILE_REASON : undefined, fromStudy: r.solved === false && !r.stale }
}

/** Why a "capacity is needed" verdict will not show in a hub solve (UX review N7). */
export const ISLAND_ROWS_OFF_TAIL = ', and the hub solve is not sized to island (island rows are off)'
export const ISLAND_ROWS_OFF_NOTE =
  'The island rows are off in the solver settings, so the hub solve is not sized to island: the capacity '
  + 'above will not appear in its results until they are turned on.'

/** A search that evaluated no case: no grid-forming converter takes part in any island
 *  case (a data center whose only scenario is ups_bridged), so there is nothing to tune. */
export const GFM_NOTHING_TO_TUNE =
  'No grid-forming converter takes part in any island case, so there is nothing to tune. '
  + 'A seamless island scenario brings the battery in.'
export function nothingToTune(rec: GfmSettingsState['recommendation']): boolean {
  return !!rec && rec.status === 'unchanged' && rec.cases.length === 0 && rec.simulations === 0
}

function control(gfm: GfmSettingsState | null | undefined): ChapterTile['control'] {
  if (!gfm) return undefined
  const rec = gfm.recommendation
  const applied = gfm.applied ?? rec?.applied
  if (applied && !applied.hub_resolve_needed) {
    return { state: 'ready',
      text: 'Grid-forming settings applied; island constraints are off, so the hub solve does not read them.' }
  }
  if (applied?.hub_resolve_needed) {
    return { state: 'stale', resolve: true,
      text: 'Grid-forming settings applied. Re-solve the hub: these settings change its island rows; '
        + 'then run the grid checks again.' }
  }
  if (nothingToTune(rec)) {
    return { state: 'not_applicable', text: `Grid-forming settings: ${GFM_NOTHING_TO_TUNE.charAt(0).toLowerCase()}${GFM_NOTHING_TO_TUNE.slice(1)}` }
  }
  if (gfm.state === 'not_run' || !rec) {
    return { state: 'not_run', text: 'Grid-forming settings: no recommendation yet.' }
  }
  if (gfm.state === 'stale') {
    return { state: 'stale', text: 'Grid-forming settings: the grid checks changed; recommend them again.' }
  }
  const n = Object.keys(rec.settings).length
  const text = rec.status === 'recommended' ? `recommended for ${n} unit${n === 1 ? '' : 's'}, not applied yet`
    : rec.status === 'unchanged' ? 'the current settings pass with margin'
      : rec.status === 'not_achievable'
        ? `no setting passes: capacity is needed${gfm.island_rows_now === false ? ISLAND_ROWS_OFF_TAIL : ''}`
        : 'the search stopped at its cap; recommend again'
  return { state: 'ready', text: `Grid-forming settings (heuristic screening): ${text}.` }
}

function operation({ status, gfm }: ChapterInputs['operation']): Omit<ChapterTile, 'id' | 'group'> {
  return { ...dispatch(status), control: control(gfm) }
}

function dispatch(status: SimulationStatus | null): Omit<ChapterTile, 'id' | 'group'> {
  const open: ChapterOpen = { panel: 'results', tab: 'dispatch' }
  const label = 'Hour-by-hour operation'
  if (status?.running) {
    return { label, value: '', state: 'running', reason: 'The dispatch is being solved.', open }
  }
  const obj = num(status?.objective)
  if (!status || status.dispatch === 'none' || status.dispatch == null || obj == null) {
    return { label, value: '', state: 'not_run', open,
      reason: 'The hour-by-hour dispatch has not been solved; the reliability study does not solve it.' }
  }
  const stale = status.dispatch === 'stale'
  return {
    // Not the cost again (UX audit M9): the Results card's cost tile shows it.
    label, value: 'Solved', state: stale ? 'stale' : 'ready', open,
    basis: 'Every modelled hour dispatched; its cost is in the yearly cost above',
    reason: stale ? 'The network changed since the dispatch was solved.' : undefined,
  }
}

const BUILD: Record<ChapterId, (i: ChapterInputs) => Omit<ChapterTile, 'id' | 'group'>> = {
  investment: i => investment(i.investment),
  grid_plan: i => gridPlan(i.grid),
  reliability: i => reliability(i.reliability),
  operation: i => operation(i.operation),
}

/** The four tiles, in the category order. */
export function chapterTiles(inputs: ChapterInputs): ChapterTile[] {
  return ORDER.map(id => ({ id, group: groupLabel(id), ...BUILD[id](inputs) }))
}
