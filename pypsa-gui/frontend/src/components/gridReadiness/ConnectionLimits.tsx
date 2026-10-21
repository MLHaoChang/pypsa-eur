// The connection's energization, ramp limit and oscillation band, read-only,
// in Connection & grid (design §5: they belong to section 3). The values come
// from the grid-readiness rows, which say where each one is set: a limit from
// the project's own record, or a generic placeholder marked assumed. Editing
// stays where the value lives (the connection link) until the ledger gets a
// write path; this block is the one place the three are listed together.
import { PageSection } from '../PageKit'
import type { GridReadinessRow } from '../../api/campusElectrical'
import { useUIStore } from '../../store/uiStore'
import { openFlowTarget } from '../../layout/flowNavigation'
import { useGridReadiness } from './GridReadinessScorecard'

const SHOWN: Record<string, string> = {
  energization_peak: 'Energization',
  ramp_rate: 'Connection ramp limit',
  oscillation: 'Oscillation band',
}

function limitText(row: GridReadinessRow): string {
  if (row.limit == null) return 'not known'
  return typeof row.limit === 'number' ? `${Number(row.limit.toPrecision(4))}${row.unit ? ` ${row.unit}` : ''}` : String(row.limit)
}

export function ConnectionLimits() {
  const q = useGridReadiness()
  const expert = useUIStore(s => s.uiMode === 'expert')
  const rows = (q.data?.rows ?? []).filter(r => r.id in SHOWN)
  // Energization has one row per period; the first of each id names the connection.
  const first = rows.filter((r, i) => rows.findIndex(x => x.id === r.id) === i)
  return (
    <PageSection title="Connection limits" hint="Read-only here">
      {q.isPending ? <p className="text-[12px] text-muted">Loading the connection limits…</p>
        : q.isError ? <p className="text-[12px] text-warn" data-testid="connection-limits-error">
            The connection limits could not be read.</p>
        : first.length === 0 ? <p className="text-[12px] text-muted">No grid connection is tagged on this project.</p>
          : (
            <ul className="flex flex-col gap-2" data-testid="connection-limits">
              {first.map(r => (
                <li key={r.id} data-testid={`connection-limit-${r.id}`} className="text-[12px]">
                  <span className="font-semibold">{SHOWN[r.id]}</span>{': '}
                  <span className="tabular-nums">{limitText(r)}</span>
                  {r.assumed && <span className="ml-1 text-muted">(assumed)</span>}
                  <span className="block text-muted">{r.source}{r.note ? `. ${r.note}` : ''}</span>
                  {expert && r.target.field && (
                    <button type="button" className="underline text-[11px]" onClick={() => openFlowTarget(r.target)}>
                      {r.id === 'energization_peak' ? 'Edit the energization series' : 'Edit on the connection link'}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
    </PageSection>
  )
}
