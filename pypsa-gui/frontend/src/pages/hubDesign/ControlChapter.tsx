// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/**
 * The Control chapter in Guided Results (control-strategy walkthrough 06): what the island study found, read
 * from its report only (GK B3 C5): the cost of island capability per scenario and the island limits reached.
 * Shown when the report has a study; out of scope it stays visible, greyed, with "Outside scope · Add" (RC3).
 * The detail is Expert-only (Results › Island & ride-through), so its link says "Open in Expert ›".
 */
import { useQuery } from '@tanstack/react-query'
import { campusApi, type IslandReport } from '../../api/campusElectrical'
import { AddToScope } from '../../components/flowScope/ScopeCards'
import { StatePill } from '../../components/ResultState'
import { useInScope } from '../../components/flowScope/useFlowScope'
import { withReturnFrom } from '../../layout/flowNavigation'
import { useUIStore } from '../../store/uiStore'
import { money } from '../../utils/money'
import { CAMPUS_KEY } from '../CampusElectricalPanel'
import { scenarioWords } from '../CampusIslandCutsSection'
import { ISLAND_REPORT_KEY } from '../CampusIslandSection'

export interface ControlFindings {
  costs: { scenario: string; eur: number | null }[]
  limits: number
}

/** The report's study tables, in words; null when the report has no study. */
export function controlFindings(report: IslandReport | null | undefined): ControlFindings | null {
  const costs = report?.tables?.find(t => t.id === 'costs' && t.group === 'study')
  if (!costs) return null
  const col = costs.columns.indexOf('cost of island capability')
  const rows = costs.rows.slice(1)  // row 0 is the baseline, 0 by definition
  const binding = report?.tables?.find(t => t.id === 'binding' && t.group === 'study')
  return {
    costs: rows.map(r => {
      const v = col >= 0 ? r[col] : null
      return { scenario: String(r[0]), eur: typeof v === 'number' && Number.isFinite(v) ? v : null }
    }),
    limits: binding?.rows.length ?? 0,
  }
}

const eur = (v: number) => `${v < 0 ? '−' : ''}${money(v, 'EUR')}`

function openIsland(): void {
  withReturnFrom(() => {
    const ui = useUIStore.getState()
    ui.setUiMode('expert')
    ui.setSlidePanel('results')
    ui.requestResultsTab('island')
  })
}

export function ControlChapter() {
  const project = useUIStore(s => s.currentProject)
  const inScope = useInScope('control')
  const report = useQuery({
    queryKey: ISLAND_REPORT_KEY(project ?? ''), queryFn: () => campusApi.islandReport(project!),
    retry: false, enabled: !!project,
  })
  // Out of date after an edit, as Expert's Island & ride-through says it (its Provenance pill).
  const campus = useQuery({
    queryKey: CAMPUS_KEY(project ?? ''), queryFn: () => campusApi.state(project!),
    retry: false, enabled: !!project,
  })
  const stale = campus.data?.stale === true
  const found = controlFindings(report.data)
  if (!found) return null
  return (
    <div data-testid="hub-chapter-control"
      className={`flex flex-col gap-1.5 rounded-card border border-border/70 p-2 ${inScope ? '' : 'opacity-60'}`}>
      <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">Control</span>
      {/* The same inner tile as the other chapters' figures (Kpi). */}
      <div className="flex flex-col gap-1 rounded-[8px] border border-border bg-bg-2 px-3 py-2.5"
        data-testid="hub-chapter-control-tile">
        <p className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
          <span>Cost of island capability</span>
          {stale && <span data-testid="hub-chapter-control-stale"><StatePill state="stale" /></span>}
        </p>
        <ul className={`text-[11.5px] text-text ${stale ? 'opacity-60' : ''}`} data-testid="hub-chapter-control-costs">
          {found.costs.map(c => (
            <li key={c.scenario}>{scenarioWords(c.scenario)}: {c.eur != null ? eur(c.eur) : 'not solved'}</li>
          ))}
        </ul>
        <p className="text-[11.5px] text-muted" data-testid="hub-chapter-control-limits">
          {found.limits === 0 ? 'No island limit was reached.'
            : `${found.limits} island limit${found.limits === 1 ? ' was' : 's were'} reached.`}
        </p>
      </div>
      {inScope ? (
        <button type="button" data-testid="hub-chapter-control-open" onClick={openIsland}
          className="self-start text-[11.5px] text-accent hover:underline">Open in Expert ›</button>
      ) : (
        <AddToScope card="control" option="island_checks" testId="hub-chapter-control-out-of-scope" />
      )}
    </div>
  )
}
