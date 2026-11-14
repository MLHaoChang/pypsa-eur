// SC-04 "Export for Blender": downloads the active Site's GLB + manifest zip.
// A read, so it is offered read-only too. Whether it can run is the server's
// `/flow` export_scene verdict (it needs accepted 3D buildings).
// The download is a snapshot of one revision, never a live link (UX plan GO).
import { useState } from 'react'
import toast from 'react-hot-toast'
import { sceneErrorCopy, sceneErrorDetail, siteSceneApi } from '../api/siteScene'
import { useProjectFlow } from '../hooks/useProjectFlow'
import { useUIStore } from '../store/uiStore'
import { useSiteScene } from './useSiteScene'

export const exportTitle = (revision: number | null) =>
  `Download a copy of this 3D site${revision !== null ? ` (revision ${revision})` : ''} as a 3D model file (GLB) and a manifest to open in Blender. ` +
  'It is a snapshot: later edits here do not update it, and it is not a certified model. No photos are included.'

export interface SceneExportControl {
  /** The server's `/flow` export_scene verdict (CODING_STANDARDS UI rule 1): never re-derived here. */
  available: boolean
  /** The verdict's blocker reason, shown as visible text; null when available (or the flow is still loading). */
  reason: string | null
  /** The revision this Site's scene is at, for the title and toast; null until its scene is read. */
  revision: number | null
  busy: boolean
  onExport: () => void
}

function save(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export function useSceneExport(siteId: string | null): SceneExportControl {
  const project = useUIStore(s => s.currentProject)
  const { flow } = useProjectFlow()
  const scene = useSiteScene(siteId)
  const [busy, setBusy] = useState(false)
  const verdict = flow?.scene?.actions.find(a => a.action === 'export_scene')
  const available = !!verdict?.available
  const reason = verdict && !verdict.available ? verdict.blocker?.reason ?? null : null
  const revision = scene.data?.state === 'enabled' ? scene.data.revision : null
  const onExport = () => {
    if (!project || !siteId || !available || busy) return
    setBusy(true)
    siteSceneApi.exportScene(project, siteId)
      .then(({ blob, filename, revision: got }) => {
        save(blob, filename)
        toast.success(`Downloaded ${filename}: a snapshot of revision ${got ?? revision}`, { id: 'scene-export', duration: 5000 })
      })
      // This Site has no 3D scene while another one does: the server says so in its own words.
      .catch(err => {
        const d = sceneErrorDetail(err)
        toast.error(d?.error_kind === 'not_found' && d.message ? d.message : sceneErrorCopy(err), { id: 'scene-export' })
      })
      .finally(() => setBusy(false))
  }
  return { available, reason, revision, busy, onExport }
}
