// The Site map card (UX assessment IA-6 / M7; visual-layers plan 3 S5): a
// read-only picture of the 3D site under the current hub-design step, so
// the model is never invisible in Guided mode. Shown only when the project
// has a site. The picture is the canvas's last capture from the south
// (`GET sites/<id>/thumbnail`, taken after each fresh solve or on demand);
// before any capture the card says so and still offers Open in 3D. No
// three, no query: the sites document is already in the store for the map.
import { useEffect, useState } from 'react'
import { Box } from 'lucide-react'
import { useUIStore } from '../../../store/uiStore'
import { useSitesStore } from '../../../site3d/sitesStore'
import { chooseSite } from '../../../site3d/scene'
import { writeActiveSite } from '../../../site3d/activeSite'
import { thumbnailKey, useThumbnailStore } from '../../../site3d/thumbnailStore'
import { thumbnailUrl } from '../../../site3d/thumbnail'

export function SiteThumbnailCard() {
  const project = useUIStore(s => s.currentProject)
  const activeSiteId = useUIStore(s => s.activeSiteId)
  const setActiveSiteId = useUIStore(s => s.setActiveSiteId)
  const setCanvasView = useUIStore(s => s.setCanvasView)
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const doc = useSitesStore(s => s.docFor(project))
  const site = chooseSite(doc, activeSiteId, null)
  const version = useThumbnailStore(s => (site ? s.versions[thumbnailKey(project, site.id)] ?? 0 : 0))
  // 'loading' until the <img> settles; 'none' on a 404 (no capture yet).
  const [state, setState] = useState<'loading' | 'ok' | 'none'>('loading')
  useEffect(() => { setState('loading') }, [project, site?.id, version])
  if (!project || !site) return null

  const openIn3D = () => {
    setActiveSiteId(site.id)
    writeActiveSite(project, site.id)
    setSlidePanel(null)
    setCanvasView('site')
  }

  return (
    <section data-testid="hub-site-thumbnail" aria-label="Site map"
      className="flex flex-col gap-2 rounded-lg border border-border bg-panel p-3">
      <header className="flex items-center justify-between gap-2">
        <h3 className="text-[12px] font-semibold text-text">Site map · {site.name}</h3>
        <button type="button" data-testid="hub-site-open-3d" onClick={openIn3D}
          className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-text hover:border-accent hover:text-accent">
          <Box size={12} /> Open in 3D
        </button>
      </header>
      <div className="relative overflow-hidden rounded border border-border/60 bg-canvas" style={{ aspectRatio: '3 / 2' }}>
        {state !== 'none' && (
          <img
            data-testid="hub-site-thumbnail-img"
            src={thumbnailUrl(project, site.id, version)}
            alt={`${site.name}, seen from the south in the 3D site view`}
            onLoad={() => setState('ok')}
            onError={() => setState('none')}
            className="h-full w-full object-cover"
          />
        )}
        {state === 'none' && (
          <p data-testid="hub-site-thumbnail-none" className="absolute inset-0 flex items-center justify-center px-4 text-center text-[11px] text-muted">
            No picture yet — open the site in 3D; one is taken after each solve, or with Snapshot.
          </p>
        )}
      </div>
    </section>
  )
}
