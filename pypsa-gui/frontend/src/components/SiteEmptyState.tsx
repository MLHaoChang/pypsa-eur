// What Site 3D shows when the project has no 3D site yet (UI contract rule 2,
// SC-01b): the current "Create 3D site" state from rule 3 (the disabled
// reason, or the enabled action's label) and one link to that action in
// Project & site. It is not a second creation control. After "Create 3D site"
// it shows the "New 3D site" panel instead.
import { useUIStore } from '../store/uiStore'
import { openSiteEntry } from '../layout/flowNavigation'
import { useSceneEntry } from '../site3d/SiteEntryAction'
import { useSiteCreation } from '../site3d/siteCreation'
import EmptyState from './EmptyState'
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
    <div className="flex h-full items-center justify-center px-8">
      <div className="w-full max-w-md" data-testid="site-empty-state">
        <EmptyState title="This project has no 3D site yet"
          action={{ label: '› Create 3D site in Project & site', onClick: () => openSiteEntry({ fromSite3D: true }) }}
          actionTestId="site-empty-entry-link">
          A 3D site starts from the project’s site coordinates and a campus boundary. Model equipment is optional.
          {loading || entryLoading
            ? <span className="mt-1 block">Checking the project…</span>
            : state && <span data-testid="site-empty-entry-state" className="mt-1 block text-text">{state}</span>}
        </EmptyState>
      </div>
    </div>
  )
}
