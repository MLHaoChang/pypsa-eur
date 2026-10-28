// "3D site" in Project & site: the coordinate field and the one
// "Create 3D site" action (UI contract rules 2–4). Guided shows the
// coordinates as one line with a "›" link to the `site_anchor` drawer;
// Expert (the horizon panel) shows the field itself. Each mode renders the
// entry exactly once.
import { useEffect, useRef } from 'react'
import { useUIStore } from '../store/uiStore'
import { openSiteAnchor } from '../layout/flowNavigation'
import SiteAnchorField, { anchorSummary, useSiteAnchor } from './SiteAnchorField'
import SiteEntryAction from './SiteEntryAction'

export default function SiteSetupSection({ variant }: { variant: 'guided' | 'expert' }) {
  const project = useUIStore(s => s.currentProject)
  const request = useUIStore(s => s.siteAnchorRequest)
  const drawer = useUIStore(s => s.fieldDrawer)
  const anchor = useSiteAnchor()
  const ref = useRef<HTMLElement>(null)

  // Guided: the card answers a request only when no drawer took it (the drawer's field scrolls itself).
  useEffect(() => {
    if (variant !== 'guided' || !request || drawer || !ref.current) return
    ref.current.scrollIntoView({ block: 'start' })
    useUIStore.getState().clearSiteAnchorRequest()
  }, [variant, request, drawer])

  return (
    <section ref={ref} data-testid={`site-setup-${variant}`} aria-labelledby={`site-setup-${variant}-h`}
      className={variant === 'guided' ? 'flex flex-col gap-2 border-t border-border pt-3' : 'flex flex-col gap-3 rounded-lg border border-border p-4'}>
      <h3 id={`site-setup-${variant}-h`} className="text-[13px] font-semibold text-text">3D site</h3>
      {variant === 'guided' ? (
        <div data-testid="site-anchor-summary" className="flex flex-wrap items-baseline gap-2 text-[12px]">
          <span className="text-muted">Site coordinates:</span>
          <span className="text-text">{project ? anchorSummary(anchor.data) : 'Not set'}</span>
          {project && (
            <button type="button" onClick={() => openSiteAnchor()} className="text-[11px] text-accent hover:underline">
              › Site coordinates
            </button>
          )}
        </div>
      ) : (
        <SiteAnchorField />
      )}
      <SiteEntryAction />
    </section>
  )
}
