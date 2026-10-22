// The header of a results group (W10 R1, card-workspaces.md §2.3, §4, §8): the
// card's name, its freshness in ResultState's own words, and the state a failed
// run leaves. A native view shows freshness, never "checked" (integration-
// sensitivities §5.4); "Out of date" is the same pill the strip uses.
import type { ReactNode } from 'react'
import { StatePill, type NonValueState } from '../../components/ResultState'
import type { SimulationStatus } from '../../api/types'
import { useUIStore } from '../../store/uiStore'
import { openSectionInExpert, openSectionInGuided } from '../../layout/flowNavigation'
import type { CardGroup } from './groups'

/** The group's freshness: from the solve's dispatch freshness (the rail's
 *  `results.freshness` reads the same one). The solve carries no timestamp, so
 *  a current result reads "From the latest run". */
export function freshnessOf(status: SimulationStatus | undefined): NonValueState | 'fresh' {
  if (!status) return 'not_run'
  if (status.running) return 'running'
  if (status.dispatch === 'stale') return 'stale'
  if (status.dispatch === 'fresh' || (status.dispatch == null && status.objective != null)) return 'fresh'
  return 'not_run'
}

/** The reserved place for H2b's single "Explain these results" entry. R1 adds
 *  no assistant entry; the slot stays empty. */
export const GROUP_HEADER_SLOT = 'results-ghead-slot'

export default function GroupHeader({ group, status, outsideScope, children }: {
  group: CardGroup
  status: SimulationStatus | undefined
  outsideScope: boolean
  children?: ReactNode
}) {
  const fresh = freshnessOf(status)
  const uiMode = useUIStore(st => st.uiMode)
  const failed = !!status && !status.running && !!status.failure
  return (
    <div data-testid="results-ghead" data-group={group.id}
      className="shrink-0 border-b border-border bg-bg px-3 py-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
      <h2 className="text-[13px] font-semibold text-text">{group.label}</h2>
      {!outsideScope && group.tabs.length > 0 && !group.fromStudy && (
        <span data-testid="results-ghead-freshness" data-freshness={fresh}
          className="flex items-center gap-2 text-[11px] text-muted">
          {fresh === 'fresh' ? 'From the latest run' : <StatePill state={fresh} />}
        </span>
      )}
      {failed && !outsideScope && (
        <span data-testid="results-ghead-failed" className="text-[11px] text-danger">
          Did not complete: {status!.failure!.title}{status!.failure!.hint ? `. ${status!.failure!.hint}` : ''}
          {' '}
          <button type="button" data-testid="results-ghead-failed-open"
            onClick={() => (uiMode === 'guided' ? openSectionInGuided : openSectionInExpert)('readiness')}
            className="text-accent hover:underline">Open Issues ›</button>
        </span>
      )}
      <span className="flex-1" />
      <div data-testid={GROUP_HEADER_SLOT} className="flex items-center gap-2">{children}</div>
    </div>
  )
}
