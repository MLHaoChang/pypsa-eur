import type { FlowSectionId, FlowTarget } from '../api/flow'
import type { SlidePanel } from '../store/uiStore'

// The ten flow sections as the shell shows them (workspace-reconciliation §6.1,
// design §2.1). The server's `label` wins when /flow is read; these are the
// words shown before it is, and the order and bands are the shell's.

export interface SectionMeta {
  id: FlowSectionId
  number: number
  label: string
}

export const SECTION_META: SectionMeta[] = [
  { id: 'project', number: 1, label: 'Project & site' },
  { id: 'carriers', number: 2, label: 'Carriers & processes' },
  { id: 'connection', number: 3, label: 'Connection & grid' },
  { id: 'loads', number: 4, label: 'Loads & demand' },
  { id: 'assets', number: 5, label: 'Assets & sizing' },
  { id: 'profiles', number: 6, label: 'Time series & profiles' },
  { id: 'commercial', number: 7, label: 'Commercial & parties' },
  { id: 'scenarios', number: 8, label: 'Scenarios & optimisation' },
  { id: 'readiness', number: 9, label: 'Readiness → run' },
  { id: 'results', number: 10, label: 'Results → reports' },
]

export const sectionMeta = (id: FlowSectionId): SectionMeta =>
  SECTION_META.find(s => s.id === id) as SectionMeta

/** The Expert sidebar's bands over the sections (§6.1). Section 10 sits below
 *  them; the project utilities at the foot. */
export type BandId = 'setup' | 'model' | 'run' | 'results' | 'reports'
export const BAND_SECTIONS: Record<BandId, FlowSectionId[]> = {
  setup: ['project', 'carriers'],
  model: ['connection', 'loads', 'assets', 'profiles', 'commercial'],
  run: ['scenarios', 'readiness'],
  results: ['results'],
  reports: [],
}

/** The section a slide panel belongs to, or null for a panel that belongs to
 *  none (the workspace, snapshots, the Guided hub design, compare). Navigation
 *  only: a panel keeps its id and its component. */
export const PANEL_SECTION: Partial<Record<SlidePanel, FlowSectionId>> = {
  horizon: 'project',
  campusElectrical: 'connection',
  capacityBounds: 'assets',
  timeseries: 'profiles',
  profiles: 'profiles',
  economics: 'commercial',
  simparams: 'scenarios',
  scenarios: 'scenarios',
  issues: 'readiness',
  solveQueue: 'readiness',
  results: 'results',
  reports: 'results',
  decision: 'results',
  gridspine: 'results',
}

/** The section a component class is edited in (B1's `section_for_component`,
 *  by class only: a Bus or Link on the site fence is told apart server-side). */
const CLASS_SECTION: Record<string, FlowSectionId> = {
  Carrier: 'carriers',
  Bus: 'connection', Line: 'connection', Transformer: 'connection', ShuntImpedance: 'connection',
  Load: 'loads',
  Generator: 'assets', StorageUnit: 'assets', Store: 'assets', Link: 'assets',
}
export const sectionForComponent = (type: string | null | undefined): FlowSectionId | null =>
  (type && CLASS_SECTION[type]) || null

/** The panel a section's "Edit in Expert" opens first: where its inputs live
 *  today (navigation only; the value moves are W3b). A null panel with a table
 *  opens that bottom table; sections 1, 9 and 10 open their one panel. */
export const SECTION_OPEN: Record<FlowSectionId, { panel?: SlidePanel; table?: string }> = {
  project: { panel: 'horizon' },
  carriers: { table: 'Carriers' },
  connection: { panel: 'campusElectrical' },
  loads: { table: 'Loads' },
  assets: { panel: 'capacityBounds' },
  profiles: { panel: 'timeseries' },
  commercial: { panel: 'economics' },
  scenarios: { panel: 'simparams' },
  readiness: { panel: 'issues' },
  results: { panel: 'results' },
}
