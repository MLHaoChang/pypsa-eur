import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, BarChart2, Cable, Camera, ChevronDown, Clock, Coins, FileText, FlaskConical,
  GitBranch, Layers, ListChecks, Plus, Scale, Settings2, Table2, TrendingUp, Wand2,
} from 'lucide-react'
import type { FlowSectionId } from '../../api/flow'
import { projectsApi } from '../../api/projects'
import { simulationApi } from '../../api/simulation'
import { isActive } from '../../api/solveQueue'
import { useProjectFlow } from '../../hooks/useProjectFlow'
import { useSolveQueue } from '../../hooks/useSolveQueue'
import { storedStudyRef, useDecisionStore } from '../../pages/decision/decisionStore'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { countOpen } from '../../utils/flowItems'
import AssetPaletteInline, { DEMAND_PALETTE_SECTION } from './AssetPaletteInline'
import { SItem, usePanelToggle } from './SidebarRows'

// The rows each flow section holds in the Expert sidebar (workspace-
// reconciliation §6.2). Navigation only: every row opens the panel it opened
// before, under its new section. The moves of a value's home are W3b.

type Props = { onCloseModal?: () => void }

/** "2 errors · 1 warning": the Issues badge says what its number counts. */
function issueBadgeTitle(errors: number, warnings: number): string {
  const part = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
  return [errors > 0 && part(errors, 'error'), warnings > 0 && part(warnings, 'warning')]
    .filter(Boolean).join(' · ')
}

function ProjectRows({ onCloseModal }: Props) {
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  return (
    <SItem icon={<Clock size={15} />} label="Model Horizon"
      title="Define the snapshots (time steps) and investment periods/years the optimisation plans over."
      active={activeSlidePanel === 'horizon'} onClick={() => toggle('horizon')} />
  )
}

/** A row that opens one of the bottom tables (the tables stay where they are). */
function TableRow({ label, tab, title, onCloseModal }: { label: string; tab: string; title: string } & Props) {
  const requestBottomTab = useUIStore(s => s.requestBottomTab)
  return (
    <SItem icon={<Table2 size={15} />} label={label} title={title}
      onClick={() => { requestBottomTab(tab); onCloseModal?.() }} />
  )
}

function ConnectionRows({ onCloseModal }: Props) {
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  return (
    <SItem icon={<Cable size={15} />} label="Campus electrical"
      title="Take a solved hub to its electrical design: transformers, reactive compensation, short circuit and PCC grid-code compliance at the critical hours (AC load flow)."
      active={activeSlidePanel === 'campusElectrical'} onClick={() => toggle('campusElectrical')} />
  )
}

function LoadRows({ onCloseModal }: Props) {
  const [addOpen, setAddOpen] = useState(false)
  return (
    <>
      <TableRow label="Loads" tab="Loads" onCloseModal={onCloseModal}
        title="Every demand and its set point (the Loads table)." />
      <SItem icon={<Plus size={15} />} label="Add demand"
        title="Add an electrical, hydrogen or heating demand: click it, or drag it onto the canvas."
        rightEl={<ChevronDown size={11} className="text-ink-400 shrink-0 transition-transform duration-200"
          style={{ transform: addOpen ? 'rotate(0deg)' : 'rotate(-90deg)' }} />}
        onClick={() => setAddOpen(o => !o)} />
      {addOpen && <AssetPaletteInline only={[DEMAND_PALETTE_SECTION]} />}
    </>
  )
}

function AssetRows({ onCloseModal }: Props) {
  const [assetsOpen, setAssetsOpen] = useState(false)
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  return (
    <>
      <SItem icon={<Layers size={15} />} label="Assets"
        rightEl={<ChevronDown size={11} className="text-ink-400 shrink-0 transition-transform duration-200"
          style={{ transform: assetsOpen ? 'rotate(0deg)' : 'rotate(-90deg)' }} />}
        onClick={() => setAssetsOpen(o => !o)} />
      {assetsOpen && <AssetPaletteInline skip={[DEMAND_PALETTE_SECTION]} />}
      <SItem icon={<Layers size={15} />} label="Capacity Bounds"
        title="Set per-technology minimum/maximum build limits, optionally different for each investment period (vintages)."
        active={activeSlidePanel === 'capacityBounds'} onClick={() => toggle('capacityBounds')} />
    </>
  )
}

function ProfileRows({ onCloseModal }: Props) {
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  return (
    <>
      <SItem icon={<BarChart2 size={15} />} label="Time Series"
        active={activeSlidePanel === 'timeseries'} onClick={() => toggle('timeseries')} />
      <SItem icon={<Wand2 size={15} />} label="Profile Studio"
        title="Generate wind/solar capacity-factor profiles for sites (synthetic or ERA5/atlite) and apply them onto generators' p_max_pu."
        active={activeSlidePanel === 'profiles'} onClick={() => toggle('profiles')} />
    </>
  )
}

function CommercialRows({ onCloseModal }: Props) {
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  return (
    <SItem icon={<Coins size={15} />} label="Economics"
      title="Discount and inflation rates, finance inputs, tariffs, contracts and participants."
      active={activeSlidePanel === 'economics'} onClick={() => toggle('economics')} />
  )
}

function ScenarioRows({ onCloseModal }: Props) {
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  return (
    <>
      <SItem icon={<Settings2 size={15} />} label="Solver Settings"
        title="Choose the solver, foresight mode (overnight / myopic / perfect), CO₂ price, discount rate and value-of-lost-load."
        active={activeSlidePanel === 'simparams'} onClick={() => toggle('simparams')} />
      <SItem icon={<GitBranch size={15} />} label="Scenarios"
        active={activeSlidePanel === 'scenarios'} onClick={() => toggle('scenarios')} />
    </>
  )
}

function ReadinessRows({ onCloseModal }: Props) {
  const currentProject = useUIStore(s => s.currentProject)
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  // 30 s feels right for an out-of-view indicator; the panel polls faster.
  const { data: preflight, isError: preflightErrored } = useQuery({
    queryKey: nk(currentProject, 'preflight'),
    queryFn: simulationApi.preflight,
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
  // One number for the badge, the rail and Issues: the flow's open to-do and
  // to-check items (sections 1–8, acknowledged ones left out), falling back to
  // the preflight count only while the flow is unavailable.
  const { flow } = useProjectFlow()
  const flowItems = flow ? flow.sections.filter(s => s.state !== null).flatMap(s => s.items) : null
  const open = flowItems ? countOpen(flowItems) : null
  const errorCount = open ? open.todo : (preflight?.errors ?? 0)
  const warningCount = open ? open.check : (preflight?.warnings ?? 0)
  // ADR-0001: a failed preflight fetch must never render as "no issues".
  const issueCount = errorCount + warningCount
  const hasErrors = errorCount > 0
  const { data: solveQueue } = useSolveQueue()
  const activeQueueCount = (solveQueue?.jobs ?? []).filter(isActive).length
  return (
    <>
      <SItem icon={<AlertTriangle size={15} />} label="Issues"
        active={activeSlidePanel === 'issues'}
        rightEl={preflightErrored && !flowItems ? (
          <span className="shrink-0 ml-1 text-[10px] font-mono font-semibold px-1.5 py-px rounded"
            style={{ background: 'rgba(148,163,184,0.18)', color: '#94a3b8' }}
            title="Could not check for issues — the preflight validation request failed">?</span>
        ) : issueCount > 0 ? (
          <span className={`shrink-0 ml-1 text-[10px] font-mono font-semibold px-1.5 py-px rounded ${
            hasErrors ? 'bg-danger/15 text-danger' : 'bg-warn/15 text-warn'}`}
          data-severity={hasErrors ? 'error' : 'warning'}
          title={issueBadgeTitle(errorCount, warningCount)}>{issueCount}</span>
        ) : undefined}
        onClick={() => toggle('issues')} />
      <SItem icon={<ListChecks size={15} />} label="Solve Queue"
        title="Queue saved projects to solve sequentially in the background; results persist to disk."
        active={activeSlidePanel === 'solveQueue'}
        rightEl={activeQueueCount > 0 ? (
          <span className="shrink-0 ml-1 text-[10px] font-mono font-semibold px-1.5 py-px rounded"
            style={{ background: 'rgba(217,119,6,0.18)', color: '#f59e0b' }}>{activeQueueCount}</span>
        ) : undefined}
        onClick={() => toggle('solveQueue')} />
    </>
  )
}

function ResultRows({ onCloseModal }: Props) {
  const currentProject = useUIStore(s => s.currentProject)
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  // Planning → dynamics only works in a study project. Hidden only when the
  // project list says so: unknown is not "not a study".
  const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: projectsApi.list, staleTime: 10_000 })
  const openProject = projects?.find(p => p.id === currentProject || p.name === currentProject)
  const knownNotAStudy = !!openProject && openProject.project_kind !== 'planning_dynamics'
  // A decision study is its own project kind, started and reopened from the
  // projects home (workspace-reconciliation §6.2): the row shows only in the
  // study's own project, or while its panel is open (UX-flow #21).
  const studyRef = useDecisionStore(s => s.active) ?? storedStudyRef()
  const showDecision = activeSlidePanel === 'decision'
    || (!!studyRef && !!currentProject && studyRef.project === currentProject)
  return (
    <>
      <SItem icon={<TrendingUp size={15} />} label="Results"
        active={activeSlidePanel === 'results'} onClick={() => toggle('results')} />
      {showDecision && (
        <SItem icon={<Scale size={15} />} label="Decision study"
          title="Answer an investment question (do I need a battery?) with a verdict, a size, a value and a report."
          active={activeSlidePanel === 'decision'} onClick={() => toggle('decision')} />
      )}
      {!knownNotAStudy && (
        <SItem icon={<FlaskConical size={15} />} label="Planning → dynamics"
          title="Rank a year's extreme hours, screen N-1/N-2 and fault levels, and export PowerFactory handoff bundles (gridspine)."
          active={activeSlidePanel === 'gridspine'} onClick={() => toggle('gridspine')} />
      )}
      <SItem icon={<FileText size={15} />} label="Reports"
        title="Study reports written from the Energy Hub reference design and the adequacy study; read them here and export to Word."
        active={activeSlidePanel === 'reports'} onClick={() => toggle('reports')} />
    </>
  )
}

/** The rows of one section. */
export function SectionRows({ id, onCloseModal }: { id: FlowSectionId } & Props) {
  switch (id) {
    case 'project': return <ProjectRows onCloseModal={onCloseModal} />
    case 'carriers':
      return <TableRow label="Carriers" tab="Carriers" onCloseModal={onCloseModal}
        title="The carriers the model handles and their units (the Carriers table)." />
    case 'connection': return <ConnectionRows onCloseModal={onCloseModal} />
    case 'loads': return <LoadRows onCloseModal={onCloseModal} />
    case 'assets': return <AssetRows onCloseModal={onCloseModal} />
    case 'profiles': return <ProfileRows onCloseModal={onCloseModal} />
    case 'commercial': return <CommercialRows onCloseModal={onCloseModal} />
    case 'scenarios': return <ScenarioRows onCloseModal={onCloseModal} />
    case 'readiness': return <ReadinessRows onCloseModal={onCloseModal} />
    case 'results': return <ResultRows onCloseModal={onCloseModal} />
  }
}

/** PROJECT utilities that belong to no section. */
export function ProjectUtilityRows({ onCloseModal }: Props) {
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  return (
    <SItem icon={<Camera size={15} />} label="Snapshots"
      title="Saved copies of the network: restore one, or compare it with the network you have now."
      active={activeSlidePanel === 'snapshots'} onClick={() => toggle('snapshots')} />
  )
}
