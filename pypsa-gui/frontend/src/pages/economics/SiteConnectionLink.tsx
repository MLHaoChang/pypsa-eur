// The site connection (PoC link, export link, timezone) is edited in one place,
// Connection & grid (audit #13). Everywhere else shows it read-only with this link.
import { useQuery } from '@tanstack/react-query'
import { commercialApi } from '../../api/commercial'
import type { FlowTarget } from '../../api/flow'
import { openFlowTarget, openFlowTargetInPlace } from '../../layout/flowNavigation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import SiteConnectionSection from '../results/investment/SiteConnectionSection'

const TARGET: FlowTarget = { section: 'connection', panel: 'campusElectrical', component: null, entity: null, field: null }

/** Expert opens the section's panel; Guided opens it in place over the card. */
export function openSiteConnection(): void {
  if (useUIStore.getState().uiMode === 'expert') openFlowTarget(TARGET)
  else openFlowTargetInPlace(TARGET)
}

function EditLink() {
  return (
    <button type="button" className="underline" onClick={openSiteConnection}>
      Edit in Connection &amp; grid ›
    </button>
  )
}

function useSiteConnection() {
  const project = useUIStore(s => s.currentProject)
  return useQuery({ queryKey: nk(project, 'commercial', 'config'),
                    queryFn: () => commercialApi.getCommercial() })
}

/** The editor: the one mount, at the top of the Grid connection panel. */
export function SiteConnectionEditor() {
  const config = useSiteConnection()
  if (config.isPending) return <p className="text-[11px] text-muted">Loading the site connection…</p>
  if (config.isError) return <p className="text-[11px] text-warn">The site connection could not be read.</p>
  return <SiteConnectionSection config={config.data} needed={!config.data?.poc_link} />
}

/** The read-only line (PoC, export link, timezone, or "not set") and the link to the editor. */
export function SiteConnectionSummary() {
  const config = useSiteConnection()
  if (config.isPending) return <p className="text-[11px] text-muted">Loading the site connection…</p>
  if (config.isError) return <p className="text-[11px] text-warn">The site connection could not be read.</p>
  const c = config.data
  return (
    <p className="text-[11px] text-muted" data-testid="site-connection-summary">
      {c?.poc_link
        ? <>Site connection: imports through {c.poc_link}
            {c.export_link ? `, exports through ${c.export_link}` : ', no export link'}
            {c.timezone ? `, site time ${c.timezone}` : ', snapshots in site time'}.</>
        : <>Site connection: not set.</>}{' '}
      <EditLink />
    </p>
  )
}

/** Results › Investment when the commercial root is missing. */
export function SiteConnectionNeeded() {
  return (
    <p className="text-[11px] text-muted" data-testid="ic-site-connection-needed">
      The investment case needs the site connection. Set it in{' '}
      <button type="button" className="underline" onClick={openSiteConnection}>Connection &amp; grid ›</button>
    </p>
  )
}
