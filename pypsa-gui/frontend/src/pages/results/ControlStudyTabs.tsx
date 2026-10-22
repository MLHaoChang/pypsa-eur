// The Control card's two study tabs (W10 R3, card-workspaces.md §3.3 C): "Island &
// ride-through" and "Grid-forming". The Run does not request the island study yet
// (island_checks is "not yet available"), so both show what the campus island study already
// holds, read-only and labelled with where it came from (O2): never styled as a Run output,
// no freshness of the solve, no Run button. The study is started where it is run today (Grid
// study); the cuts' loop too. The load-smoothing screen stays in the grid-readiness scorecard
// (RLR-527), so the island tab links to it and copies nothing.
import { useQuery } from '@tanstack/react-query'
import { campusApi, errorText, isOtherKind } from '../../api/campusElectrical'
import { inScope } from '../../api/flow'
import { useUIStore } from '../../store/uiStore'
import { openPanel } from './openPanel'
import { showGridReadiness, showResultsTab } from '../../layout/flowNavigation'
import { Btn, PageSection } from '../../components/PageKit'
import { StatePill } from '../../components/ResultState'
import { ScopeGate } from '../../components/flowScope/ScopeGate'
import { useFlowScope } from '../../components/flowScope/useFlowScope'
import { CAMPUS_KEY } from '../CampusElectricalPanel'
import { ISLAND_KEY, ISLAND_REPORT_KEY, IslandReportView } from '../CampusIslandSection'
import CampusIslandCutsSection from '../CampusIslandCutsSection'
import CampusIslandGfmSection from '../CampusIslandGfmSection'

/** Next from the Control results (§3.3 A): Reliability when it is in scope, else Hand-off. */
function ControlNext() {
  const { scope, isSuccess } = useFlowScope()
  if (!isSuccess) return null
  const reliability = inScope(scope, 'reliability')
  const go = () => (reliability ? showResultsTab('adequacy') : openPanel('results', 'reports'))
  return (
    <p data-testid="control-next" className="text-[11px] text-muted">
      Next: {reliability ? 'Reliability' : 'hand off'}{' '}
      <button type="button" data-testid="control-next-open" onClick={go} className="text-accent hover:underline">
        {reliability ? 'Open Adequacy' : 'Open Reports'} ›
      </button>
    </p>
  )
}

function Note({ children, testId = 'control-tab-state' }: { children: React.ReactNode; testId?: string }) {
  return <p className="text-[12px] text-muted" data-testid={testId}>{children}</p>
}

const NO_PROJECT = 'Open a project to see its island results.'
const OTHER_KIND = 'The island checks are for capacity-expansion (hub) projects.'

/** "From the island study (run 2026-10-22)": where the numbers come from, with the pill when they moved. */
function Provenance({ at, stale, testId }: { at: string | null | undefined; stale: boolean; testId: string }) {
  const day = at ? at.slice(0, 10) : null
  return (
    <p className="flex flex-wrap items-center gap-2 text-[11px] text-muted" data-testid={testId}>
      <span>From the island study ({day ? `run ${day}` : 'latest run'}).</span>
      {stale && <span data-testid="control-stale"><StatePill state="stale" /></span>}
    </p>
  )
}

export function IslandTab() {
  const name = useUIStore(s => s.currentProject)
  const island = useQuery({ queryKey: ISLAND_KEY(name ?? ''), queryFn: () => campusApi.island(name!), retry: false, enabled: !!name })
  const report = useQuery({ queryKey: ISLAND_REPORT_KEY(name ?? ''), queryFn: () => campusApi.islandReport(name!), retry: false, enabled: !!name })
  const campus = useQuery({ queryKey: CAMPUS_KEY(name ?? ''), queryFn: () => campusApi.state(name!), retry: false, enabled: !!name })
  if (!name) return <div className="p-4"><Note>{NO_PROJECT}</Note></div>
  if (island.isError) {
    return <div className="p-4"><Note>{isOtherKind(island.error) ? OTHER_KIND : errorText(island.error)}</Note></div>
  }
  if (island.isPending || report.isPending) return <div className="p-4"><Note>Loading…</Note></div>
  // The report's `inputs` tables are the island data the project carries (a template ships it),
  // not a result: only a study, or a table of the island hours, is something to show.
  const has = !!island.data?.study || (report.data?.tables ?? []).some(t => t.group !== 'inputs')
  return (
    <div className="space-y-3 p-4 h-full overflow-y-auto" data-testid="island-tab">
      {has ? (
        <>
          <Provenance at={island.data?.study_at} stale={campus.data?.stale === true} testId="island-from" />
          <PageSection title="Island & ride-through" hint="Ride-through, the instant of islanding, and the cost of island capability">
            <IslandReportView report={report.data} />
          </PageSection>
          <PageSection title="Frequency cuts" hint="Proposed by the last campus run">
            <CampusIslandCutsSection name={name} mode="read" />
            <div className="mt-2"><Btn data-testid="island-cuts-open"
              onClick={() => openPanel('scenarios', 'campusStudy')}>Apply or take back cuts in Grid study ›</Btn></div>
          </PageSection>
          <PageSection title="Load smoothing" hint="Whether the battery holds the load's steps inside the connection's limits">
            <p className="text-[12px] text-muted">
              The smoothing screen is judged with the grid-code verdicts, before and after the battery.{' '}
              <button type="button" data-testid="island-scorecard-link" className="text-accent hover:underline"
                onClick={showGridReadiness}>Readiness › Grid readiness ›</button>
            </p>
          </PageSection>
        </>
      ) : (
        <NoIslandResults>
          <div className="space-y-2" data-testid="island-tab-state">
            <Note testId="island-none">Not run yet: the island study has not produced results for this project.</Note>
            <Btn onClick={() => openPanel('scenarios', 'campusStudy')}>Run the island study in Grid study ›</Btn>
          </div>
        </NoIslandResults>
      )}
      <ControlNext />
    </div>
  )
}

/** No island results. island_checks is "not yet available" in the scope (the Run does not request
 *  it), so the tab says "Not yet available" with its why (§2.3); a site that does not offer it, or
 *  one where it can be switched on, gets ScopeGate's own words. */
function NoIslandResults({ children }: { children: React.ReactNode }) {
  const { registry } = useFlowScope()
  const opt = registry?.cards.find(c => c.id === 'control')?.options.find(o => o.id === 'island_checks')
  if (opt?.status === 'not_yet_available') {
    return (
      <p className="text-[12px] text-muted" data-testid="island-gate-why">
        Not yet available{opt.reason ? `: ${opt.reason}` : '.'}
      </p>
    )
  }
  return <ScopeGate card="control" option="island_checks" testId="island-gate">{children}</ScopeGate>
}

export function GridFormingTab() {
  const name = useUIStore(s => s.currentProject)
  const gfm = useQuery({ queryKey: ['campusElectrical', 'gfmSettings', name ?? ''], queryFn: () => campusApi.gfmSettings(name!),
    retry: false, enabled: !!name })
  if (!name) return <div className="p-4"><Note>{NO_PROJECT}</Note></div>
  if (gfm.isError) {
    return <div className="p-4"><Note>{isOtherKind(gfm.error) ? OTHER_KIND : errorText(gfm.error)}</Note></div>
  }
  if (gfm.isPending) return <div className="p-4"><Note>Loading…</Note></div>
  return (
    <div className="space-y-3 p-4 h-full overflow-y-auto" data-testid="grid-forming-tab">
      <p className="text-[11px] text-muted" data-testid="grid-forming-from">
        From the island study's frequency check; heuristic screening, not a certificate.
      </p>
      <CampusIslandGfmSection name={name} />
      <ControlNext />
    </div>
  )
}
