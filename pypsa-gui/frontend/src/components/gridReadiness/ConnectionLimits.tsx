// The connection's energization, ramp limit and oscillation band, read-only,
// in Connection & grid (design §5: they belong to section 3). The values come
// from the grid-readiness rows, which say where each one is set: a limit from
// the project's own record, or a generic placeholder marked assumed. Editing
// stays where the value lives (the connection link) until the ledger gets a
// write path; this block is the one place the three are listed together.
import { PageSection } from '../PageKit'
import type { FlowTarget } from '../../api/flow'
import type { GridReadinessRow } from '../../api/campusElectrical'
import { useUIStore } from '../../store/uiStore'
import { openFlowTarget, openFlowTargetInPlace } from '../../layout/flowNavigation'
import { fmt, useGridReadiness } from './GridReadinessScorecard'

const SHOWN: Record<string, string> = {
  energization_peak: 'Energization',
  ramp_rate: 'Connection ramp limit',
  oscillation: 'Oscillation band',
}

/** The row's kind: staged projects have one energization row per period, `energization_peak:2035`. */
const kindOf = (r: GridReadinessRow) => r.id.split(':')[0]

export function ConnectionLimits() {
  const q = useGridReadiness()
  const expert = useUIStore(s => s.uiMode === 'expert')
  // Same button in both modes: Expert opens the Link's properties, Guided the field drawer over the card.
  const open = (t: FlowTarget) => (expert ? openFlowTarget(t) : openFlowTargetInPlace(t))
  const rows = (q.data?.rows ?? []).filter(r => kindOf(r) in SHOWN)
  // One line per kind. Energization has one row per period: every period is listed,
  // so a connection that grows from 20 to 60 MW is never shown as 20 MW.
  const kinds = Object.keys(SHOWN).filter(k => rows.some(r => kindOf(r) === k))
  return (
    <PageSection title="Connection limits" hint="Read-only here">
      {q.isPending ? <p className="text-[12px] text-muted">Loading the connection limits…</p>
        : q.isError ? <p className="text-[12px] text-warn" data-testid="connection-limits-error">
            The connection limits could not be read.</p>
        : kinds.length === 0 ? <p className="text-[12px] text-muted">No grid connection is tagged on this project.</p>
          : (
            <ul className="flex flex-col gap-2" data-testid="connection-limits">
              {kinds.map(k => {
                const group = rows.filter(r => kindOf(r) === k)
                const lead = group[0]
                const staged = group.length > 1 || lead.id.includes(':')
                const figures = staged
                  ? group.map(r => `${r.id.split(':')[1] ?? ''} ${fmt(r.limit, r.unit)}`.trim()).join(' · ')
                  : fmt(lead.limit, lead.unit)
                return (
                  <li key={k} data-testid={`connection-limit-${k}`} className="text-[12px]">
                    <span className="font-semibold">{SHOWN[k]}</span>{': '}
                    <span className="tabular-nums">{figures}</span>
                    {group.some(r => r.assumed) && <span className="ml-1 text-muted">(assumed)</span>}
                    <span className="block text-muted">{lead.source}{lead.note && !staged ? `. ${lead.note}` : ''}</span>
                    {lead.target.field && (
                      <button type="button" className="underline text-[11px]" onClick={() => open(lead.target as FlowTarget)}>
                        {k === 'energization_peak' ? 'Edit the energization schedule' : 'Edit on the connection link'}
                      </button>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
    </PageSection>
  )
}
