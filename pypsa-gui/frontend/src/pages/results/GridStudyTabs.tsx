// The Grid card's two study tabs (W10 R2, card-workspaces.md §3.2 C): "Grid code &
// connection" and "Electrical design". Both only read what the grid studies already
// hold; running them stays in Grid study (campus) and Planning → dynamics (gridspine).
import { useQuery } from '@tanstack/react-query'
import { gridspineApi, isNotAStudy } from '../../api/gridspine'
import { errorText } from '../../api/campusElectrical'
import { useUIStore } from '../../store/uiStore'
import { openPanel } from './openPanel'
import { showGridReadiness } from '../../layout/flowNavigation'
import { Btn, PageSection } from '../../components/PageKit'
import { useFlowScope } from '../../components/flowScope/useFlowScope'
import { inScope } from '../../api/flow'
import { CampusResultsTab } from '../CampusElectricalPanel'
import { CapacitySection, ConnectionSection, STATUS_KEY } from '../GridspinePanel'

/** Next from the Grid results (§3.2 A): Control when it is in scope, else Hand-off. */
function GridNext() {
  const { scope, isSuccess } = useFlowScope()
  if (!isSuccess) return null
  const control = inScope(scope, 'control')
  const go = () => (control ? openPanel('scenarios', 'campusStudy') : openPanel('results', 'reports'))
  return (
    <p data-testid="grid-next" className="text-[11px] text-muted">
      Next: {control ? 'Control' : 'hand off'}{' '}
      <button type="button" data-testid="grid-next-open" onClick={go} className="text-accent hover:underline">
        {control ? 'Run the island study in Grid study' : 'Open Reports'} ›
      </button>
    </p>
  )
}

/** Which study a project has: a planning → dynamics study answers its status, a hub project 409s. */
function useStudyKind(name: string | null) {
  const q = useQuery({ queryKey: STATUS_KEY(name ?? ''), queryFn: () => gridspineApi.status(name!), retry: false, enabled: !!name })
  return { isStudy: q.isSuccess, isHub: q.isError && isNotAStudy(q.error),
    pending: !!name && q.isPending, failure: q.isError && !isNotAStudy(q.error) ? errorText(q.error) : null }
}

function Loading() {
  return <p className="text-[12px] text-muted" data-testid="grid-tab-state">Loading…</p>
}

function NoProject() {
  return <p className="text-[12px] text-muted" data-testid="grid-tab-state">Open a solved project to see its grid study results.</p>
}

/** A planning → dynamics project's connection capacity and assessments, read-only. */
function StudyConnection({ name }: { name: string }) {
  const openStudy = () => openPanel('results', 'gridspine')
  const status = useQuery({ queryKey: STATUS_KEY(name), queryFn: () => gridspineApi.status(name), retry: false })
  if (!status.data) return null
  if (status.data.status !== 'completed') {
    const running = status.data.status === 'running'
    return (
      <div data-testid="grid-tab-state" className="text-[12px] text-muted space-y-2">
        <p>{running ? 'Running: the planning → dynamics study is still going.'
          : 'Not run yet: the planning → dynamics study has not finished.'}</p>
        {!running && <Btn onClick={openStudy}>Open Planning → dynamics ›</Btn>}
      </div>
    )
  }
  return (
    <div className="space-y-3" data-testid="grid-tab-study">
      <p className="text-[11px] text-muted" data-testid="grid-tab-from">From the planning → dynamics study (latest run).</p>
      <CapacitySection name={name} locked readOnly />
      <ConnectionSection name={name} locked assessAt={null} readOnly />
      <Btn onClick={openStudy}>Compute or assess in Planning → dynamics ›</Btn>
    </div>
  )
}

export function GridCodeTab() {
  const name = useUIStore(s => s.currentProject)
  // Which kind of project this is decides which study the tab reads.
  const kind = useStudyKind(name)
  if (!name) return <NoProject />
  return (
    <div className="space-y-3 p-4 h-full overflow-y-auto" data-testid="grid-code-tab">
      <PageSection title="Grid code & connection" hint="What the grid-readiness scorecard does not show">
        <p className="text-[11px] text-muted">
          Grid-code verdicts, with the before / after-battery values, are in the scorecard.{' '}
          <button type="button" data-testid="grid-code-scorecard-link" className="text-accent hover:underline"
            onClick={showGridReadiness}>Readiness › Grid readiness ›</button>
        </p>
      </PageSection>
      {kind.pending ? <Loading />
        : kind.isStudy ? <StudyConnection name={name} />
          : kind.failure ? <p className="text-[12px] text-muted" data-testid="grid-tab-state">{kind.failure}</p>
            : <CampusResultsTab name={name} part="grid_code" />}
      <GridNext />
    </div>
  )
}

export function ElectricalDesignTab() {
  const name = useUIStore(s => s.currentProject)
  const kind = useStudyKind(name)
  if (!name) return <NoProject />
  if (kind.isStudy) {
    return (
      <p className="p-4 text-[12px] text-muted" data-testid="grid-tab-state">
        The electrical design comes from the campus study of a capacity-expansion (hub) project, not from a
        planning → dynamics study.
      </p>
    )
  }
  return (
    <div className="space-y-3 p-4 h-full overflow-y-auto" data-testid="electrical-design-tab">
      {kind.pending ? <Loading />
        : kind.failure ? <p className="text-[12px] text-muted" data-testid="grid-tab-state">{kind.failure}</p>
          : <CampusResultsTab name={name} part="electrical_design" />}
      <GridNext />
    </div>
  )
}
