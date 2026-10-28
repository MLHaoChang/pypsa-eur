// What Site 3D shows when the project has no 3D site yet (UI contract rule 2,
// SC-01b): the current "Create 3D site" state from rule 3 (the disabled
// reason, or the enabled action's label) and one link to that action in
// Project & site. It is not a second creation control. After "Create 3D site"
// it shows the "New 3D site" panel instead.
import { useUIStore } from '../store/uiStore'
import { openSiteEntry } from '../layout/flowNavigation'
import { useSceneEntry } from '../site3d/SiteEntryAction'
import { useSiteCreation } from '../site3d/siteCreation'
import SiteCreationPanel from '../site3d/SiteCreationPanel'

export default function SiteEmptyState({ loading = false }: { loading?: boolean }) {
  const project = useUIStore(s => s.currentProject)
  const creating = useSiteCreation(s => s.open)
  const { entry, loading: entryLoading } = useSceneEntry()
  if (creating && project) return <SiteCreationPanel />
  const state = !entry ? null
    : entry.enabled && !entry.reason ? `Next: ${entry.label}`
    : entry.blocker?.reason ?? entry.reason ?? entry.label
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center px-8" data-testid="site-empty-state">
      <div className="text-[13px] font-medium text-text">This project has no 3D site yet</div>
      <div className="text-[12px] text-muted max-w-md">
        A 3D site starts from the project’s site coordinates and a campus boundary. Model equipment is optional.
      </div>
      {loading || entryLoading ? (
        <div className="text-[12px] text-muted">Checking the project…</div>
      ) : (
        <div className="flex flex-col items-center gap-1 text-[12px]">
          {state && <span data-testid="site-empty-entry-state" className="text-text">{state}</span>}
          <button type="button" data-testid="site-empty-entry-link" onClick={() => openSiteEntry({ fromSite3D: true })}
            className="text-accent hover:underline">
            › Create 3D site in Project &amp; site
          </button>
        </div>
      )}
    </div>
  )
}
