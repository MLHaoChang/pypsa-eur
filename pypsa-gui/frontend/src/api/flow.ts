import { client } from './client'

// The project flow (project-workflow B1): one row per flow section with its
// state and items, from `GET /api/projects/{name}/flow`. Read-only over the
// existing checks; `/run` keeps its own validation. Cached under
// `nk(project, 'flow')`, which the asset writes and network swaps invalidate
// (`COMPONENT_QUERY_ROOTS`, `ALL_NETWORK_KEYS`). 409 when `project` is not the
// open one: the flow is read from the loaded network.

/** The ten flow sections, in flow order (`services/flow_sections.py`). */
export const FLOW_SECTIONS = [
  'project', 'carriers', 'connection', 'loads', 'assets', 'profiles',
  'commercial', 'scenarios', 'readiness', 'results',
] as const
export type FlowSectionId = (typeof FLOW_SECTIONS)[number]

/** Sections 1–8. `unknown` only while the preflight is deferred; never `complete`. */
export type FlowSectionState = 'complete' | 'warning' | 'incomplete' | 'not_needed' | 'unknown'

/** A section state in the user's words (project-workflow design §3.2, the rail). */
export const FLOW_STATE_WORDS: Record<FlowSectionState, string> = {
  complete: 'complete',
  warning: 'to check',
  incomplete: 'to do',
  not_needed: 'not needed',
  unknown: 'not known yet',
}

/** Section 10's own vocabulary (`results.freshness`). */
export type FlowFreshness = 'not_run' | 'running' | 'fresh' | 'stale'

export type FlowItemSource =
  | 'validation' | 'eh_study' | 'eh_readiness' | 'commercial' | 'library' | 'template' | 'flow'

/** Where an item's fix lives. `panel` is a `SlidePanel` id, or null for the
 *  component's own Properties (`component` + `entity`); unknowns are null. */
export interface FlowTarget {
  section: FlowSectionId
  panel: string | null
  component: string | null
  entity: string | null
  field: string | null
}

export interface FlowItem {
  code: string
  severity: 'error' | 'warning' | 'info'
  message: string
  /** Plain title of the check where the server has one (validation codes). */
  title?: string | null
  source: FlowItemSource
  target: FlowTarget
  /** A server refusal (`validate_for_run`, or the study's own start refusal):
   *  only these make `run_ready` false. Other errors are display-only gaps. */
  blocking: boolean
  /** A warning the user has checked (`POST …/flow/ack`); it no longer counts. */
  acknowledged: boolean
  /** The work card the item belongs to (W4a), or null for the core model. */
  card: ScopeCardId | null
  /** false: its card or kind is outside the scope. It does not count toward
   *  the section's state, is not to do or to check, and never blocks; Issues
   *  lists it under "Not in scope". */
  in_scope: boolean
}

export interface FlowSection {
  section: FlowSectionId
  label: string
  /** null for `readiness` and `results`, which carry no state of their own. */
  state: FlowSectionState | null
  required: boolean
  items: FlowItem[]
  /** One line for the rail ("grid_import, 40 MW"); `readiness`: "n to do, m to check". */
  summary: string | null
  /** `results` only. */
  freshness?: FlowFreshness
}

/** A pointer to one item: the Run's blocker, or a display-only note. */
export interface FlowRunBlocker {
  section: FlowSectionId
  /** "Connection & grid: Bus voltage must be positive (grid)", ready for the disabled Run's reason. */
  message: string
  /** The item's own message, the detail to fix from (unchanged by the title). */
  detail?: string
  /** The validation code behind it ("sclopf_no_branches"). */
  code?: string
  target: FlowTarget
}
export type FlowNote = FlowRunBlocker

export interface FlowVerdict {
  ready: boolean
  blocker: FlowRunBlocker | null
}

export interface ProjectFlow {
  sections: FlowSection[]
  /** Exactly the server's verdict (design §3.3 R1); warnings never block
   *  (W3); false while deferred. */
  run_ready: boolean
  /** The first blocking item, in flow order. */
  run_blocker: FlowRunBlocker | null
  /** What the one Run does: the optimisation always; the study for a project
   *  made from an Energy Hub template (until section 8 has a run mode). */
  run_request: { optimisation: boolean, study: boolean }
  /** Each run kind's own server verdict; `run_ready` is the AND over the
   *  requested kinds. `study` is null when the Run has no study. */
  verdicts: { optimisation: FlowVerdict, study: FlowVerdict | null }
  /** Display-only gaps (non-blocking errors), shown beside an enabled Run. */
  notes: FlowNote[]
  /** What the Run computes (W4a): stored, or the template's preset. */
  scope: FlowScope
  deferred: boolean
  deferred_reason: string | null
}

export interface FlowAck {
  section: FlowSectionId
  code: string
  entity: string
  value_hash: string
  at: string
}

const flowPath = (project: string) => `/projects/${encodeURIComponent(project)}/flow`

export function getProjectFlow(project: string): Promise<ProjectFlow> {
  // Read ambiently by the rail, the sidebar and the Run: a failure shows as
  // "not known yet" on the state dots, never as a toast on every poll.
  return client.get<ProjectFlow>(flowPath(project), { skipErrorToast: true }).then(r => r.data)
}

/** Acknowledge a standing warning. 404: no such item; 422: an error. */
export function ackFlowItem(project: string, code: string, entity: string | null): Promise<FlowAck> {
  return client.post<{ ack: FlowAck }>(`${flowPath(project)}/ack`, { code, entity })
    .then(r => r.data.ack)
}

// ── The scope (W4a): six work cards, each with at most three sub-options ──────

export const SCOPE_CARDS = [
  'capacity', 'grid', 'control', 'reliability', 'commercial', 'handoff',
] as const
export type ScopeCardId = (typeof SCOPE_CARDS)[number]

/** `{version, groups, options}` as `flow_scope.json` stores it. A card is on
 *  when it is in `groups` with a sub-option; one switched off keeps its
 *  sub-options, so switching it on restores them. */
export interface FlowScopeValue {
  version: 1
  groups: ScopeCardId[]
  options: Record<ScopeCardId, string[]>
}

export interface FlowScope extends FlowScopeValue {
  /** `project`: stored; `preset`: none stored yet, the template's preset. */
  source: 'project' | 'preset'
  preset_id: string
}

/** `available`; `required`: on and locked; `not_yet_available`: shown, never
 *  selectable. */
export type ScopeOptionStatus = 'available' | 'required' | 'not_yet_available'

export interface ScopeCardDef {
  id: ScopeCardId
  label: string
  options: { id: string, label: string, status: ScopeOptionStatus, reason: string | null }[]
  /** Why the card cannot be switched off, or null. */
  locked_reason: string | null
  /** What the project's example intends, including sub-options not yet available. */
  intended: string[]
  /** The input sections whose blocks the card holds. */
  sections: FlowSectionId[]
  /** The section rows' chip ("Grid"), or null (Capacity, Hand-off). */
  chip: string | null
}

export interface FlowScopeRegistry {
  scope: FlowScope
  cards: ScopeCardDef[]
  preset_id: string
  /** The example's name, for "Reset to <name>'s cards". */
  preset_label: string
  preset: FlowScopeValue
  /** The solver config's size source: 'off' (sizing on), 'capacity_run' or 'nameplate'. */
  fixed_sizes?: 'off' | 'capacity_run' | 'nameplate'
}

export function getFlowScope(project: string): Promise<FlowScopeRegistry> {
  // A background read the sidebar chips and section 8 make: its readers show
  // (or skip) their own state, so a failure never pops the global toast.
  return client.get<FlowScopeRegistry>(`${flowPath(project)}/scope`, { skipErrorToast: true })
    .then(r => r.data)
}

/** Store the scope. 422 with `flow_scope_unavailable` / `flow_scope_required` /
 *  `flow_scope_unknown` / `flow_scope_invalid`. */
export function putFlowScope(project: string, scope: FlowScopeValue): Promise<FlowScope> {
  const { version, groups, options } = scope
  return client.put<{ scope: FlowScope }>(`${flowPath(project)}/scope`, { version, groups, options })
    .then(r => r.data.scope)
}

/** A card, or one of its sub-options, is in scope. */
export function inScope(scope: FlowScopeValue | null | undefined, card: ScopeCardId,
  option?: string): boolean {
  if (!scope || !scope.groups.includes(card)) return false
  const chosen = scope.options[card] ?? []
  return option === undefined ? chosen.length > 0 : chosen.includes(option)
}

/** Where a goal value comes from: saved by the user, the project template's,
 *  or the study's own default (`services/reliability_goal.py`). */
export type GoalSource = 'stored' | 'template' | 'default'
export interface GoalValue<T> { value: T; source: GoalSource }

/** The project's one reliability goal (W3b-R) as every study resolves it.
 *  A `default` target or ENS of null is "the site type's own value". */
export interface ReliabilityGoal {
  target_lole_h: GoalValue<number | null>
  ens_cap_permyriad: GoalValue<number | null>
  mc_draws: GoalValue<number>
  site_archetype: GoalValue<'strong_grid' | 'weak_flexible' | 'off_grid'>
}

/** Read the goal. Written through `PUT /simulation/solver_config` (partial). */
export function getReliabilityGoal(project: string): Promise<ReliabilityGoal> {
  return client.get<ReliabilityGoal>(`${flowPath(project)}/reliability_goal`, { skipErrorToast: true })
    .then(r => r.data)
}
