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
  source: FlowItemSource
  target: FlowTarget
  /** A server refusal (`validate_for_run`, or the study's own start refusal):
   *  only these make `run_ready` false. Other errors are display-only gaps. */
  blocking: boolean
  /** A warning the user has checked (`POST …/flow/ack`); it no longer counts. */
  acknowledged: boolean
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
  /** "Connection & grid: …", ready for the disabled Run's reason. */
  message: string
  target: FlowTarget
}
export type FlowNote = FlowRunBlocker

export interface ProjectFlow {
  sections: FlowSection[]
  /** Exactly the server's verdict (design §3.3 R1); warnings never block
   *  (W3); false while deferred. */
  run_ready: boolean
  /** The first blocking item, in flow order. */
  run_blocker: FlowRunBlocker | null
  /** Display-only gaps (non-blocking errors), shown beside an enabled Run. */
  notes: FlowNote[]
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
  return client.get<ProjectFlow>(flowPath(project)).then(r => r.data)
}

/** Acknowledge a standing warning. 404: no such item; 422: an error. */
export function ackFlowItem(project: string, code: string, entity: string | null): Promise<FlowAck> {
  return client.post<{ ack: FlowAck }>(`${flowPath(project)}/ack`, { code, entity })
    .then(r => r.data.ack)
}
