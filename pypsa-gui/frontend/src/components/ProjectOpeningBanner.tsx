import { useUIStore } from '../store/uiStore'

/**
 * Progress strip shown while a project switch is in flight. Opening a cold
 * project can take a minute (large template, solve running); without this the
 * workbench sits on "Unnamed Network / No project open" with no sign of work.
 * The backend reports no percentage, so the bar is indeterminate.
 */
export default function ProjectOpeningBanner() {
  const opening = useUIStore(s => s.openingProject)
  if (!opening) return null
  return (
    <div role="status" aria-live="polite" data-testid="project-opening"
         className="shrink-0 border-b border-border bg-panel px-3 py-1.5">
      <div className="text-[11px] text-muted">Opening {opening}… large projects can take a minute.</div>
      <div role="progressbar" aria-label={`Opening ${opening}`}
           className="mt-1 h-0.5 w-full overflow-hidden rounded bg-border">
        <div className="h-full w-1/3 animate-pulse rounded bg-accent" />
      </div>
    </div>
  )
}
