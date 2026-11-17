import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { resultsApi, type ReserveHolder } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { openOffGridConstraints } from '../../layout/flowNavigation'

/** Most binding hours listed; the rest are counted in the visible cap line. */
export const RESERVE_ROW_CAP = 50

const pct = (f: number) => `${+(f * 100).toFixed(1)} %`
const mw = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 2 })

/** Units named per hour before the rest fold behind "Show all". */
export const RESERVE_HOLDER_CAP = 5

/** One unit as people read it: "Diesel 120 MW", with "(storage)" after a storage unit's name. */
const holderText = (h: ReserveHolder) => `${h.name}${h.kind === 'storage_unit' ? ' (storage)' : ''} ${mw(h.held_mw)} MW`

/** Who holds the reserve in one binding hour: "Diesel 120 MW · Battery (storage) 30 MW", folded past five units. */
function HeldBy({ holders }: { holders: ReserveHolder[] }) {
  const [all, setAll] = useState(false)
  if (holders.length === 0) return <>No unit had spare capacity to hold reserve</>
  const shown = all ? holders : holders.slice(0, RESERVE_HOLDER_CAP)
  const rest = holders.length - shown.length
  return (
    <>
      {shown.map(holderText).join(' · ')}
      {rest > 0 && (
        <> · <button type="button" data-testid="operating-reserve-show-all" onClick={() => setAll(true)}
          className="text-accent hover:underline">({rest} more) Show all</button></>
      )}
    </>
  )
}

/** The server's reason, in plain words (it carries engine wording). */
function plainReason(reason: string): string {
  if (reason.startsWith('the solve has no dispatch for'))
    return 'The reserve could not be checked: the solve has no dispatch results for some units.'
  if (reason.includes('missing values'))
    return 'The reserve could not be checked: the dispatch has missing values.'
  return 'The reserve could not be checked for this run.'
}

/** The hours where the operating reserve binds (homer-parity 01c). Read-only; hidden when the reserve was off. */
export default function OperatingReserve() {
  const currentProject = useUIStore(s => s.currentProject)
  const { data } = useQuery({
    queryKey: nk(currentProject, 'results', 'operating_reserve'),
    queryFn: resultsApi.getOperatingReserve,
  })
  if (!data || !data.shares) return null
  const { load, pv, wind } = data.shares
  const rows = data.per_snapshot.filter(r => r.binding)
  const shown = rows.slice(0, RESERVE_ROW_CAP)
  const thLeft = 'px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide whitespace-nowrap text-left'
  const td = 'px-2 py-1 text-[11px] text-right font-mono'
  const th = 'px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide whitespace-nowrap text-right'
  return (
    <section data-testid="operating-reserve" className="mb-4">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted mb-1">Operating reserve</h3>
      <p data-testid="operating-reserve-shares" className="text-[11px] mb-1">
        Reserve for load {pct(load)}, solar {pct(pv)}, wind {pct(wind)}
      </p>
      <p className="text-[11px] text-muted mb-2">
        The hours where the reserve decided the dispatch: the headroom held equals the headroom required. The shares are set in Solver settings.{' '}
        <button type="button" data-testid="operating-reserve-inputs" onClick={() => openOffGridConstraints()}
          className="text-accent hover:underline">Reserve inputs ›</button>
      </p>
      {data.reason ? (
        <p data-testid="operating-reserve-reason" className="text-[11px] text-muted">{plainReason(data.reason)}</p>
      ) : rows.length === 0 ? (
        <p data-testid="operating-reserve-never" className="text-[11px] text-muted">The reserve never bound in this run.</p>
      ) : (
        <>
          <div className="overflow-auto border border-border rounded">
            <table className="w-full text-xs border-collapse" style={{ minWidth: 640 }}>
              <thead>
                <tr>
                  <th className={thLeft}>Hour</th>
                  <th className={th}>Reserve required (MW)</th>
                  <th className={th}>Reserve held (MW)</th>
                  <th className={th}>Shortfall (MW)</th>
                  <th className={thLeft}>Held by</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r, i) => (
                  <tr key={r.snapshot} data-testid="operating-reserve-row" className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                    <td className="px-2 py-1 text-[11px]">{r.snapshot}</td>
                    <td className={td}>{mw(r.required_mw)}</td>
                    <td className={td}>{mw(r.held_mw)}</td>
                    <td className={td}>{mw(Math.max(0, r.required_mw - r.held_mw))}</td>
                    <td data-testid="operating-reserve-holders" className="px-2 py-1 text-[11px]"><HeldBy holders={r.holders ?? []} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > shown.length && (
            <p data-testid="operating-reserve-cap" className="text-[11px] text-muted mt-1">
              Showing the first {shown.length} of {rows.length} hours.
            </p>
          )}
        </>
      )}
      {data.not_counted.length > 0 && (
        <p className="text-[11px] text-muted mt-1">
          Not counted toward the reserve (not on the electricity bus): {data.not_counted.join(', ')}.
        </p>
      )}
    </section>
  )
}
