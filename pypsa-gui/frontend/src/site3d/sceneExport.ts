// SC-04 "Export for Blender": downloads the active Site's GLB + manifest zip.
// A read, so it is offered read-only too; it needs a 3D scene with buildings.
// The download is a snapshot of one revision, never a live link (UX plan GO).
import { useState } from 'react'
import toast from 'react-hot-toast'
import { sceneErrorCopy, siteSceneApi } from '../api/siteScene'
import { useUIStore } from '../store/uiStore'
import { useSiteScene } from './useSiteScene'

export const EXPORT_NO_SCENE = 'Generate the 3D buildings first (Generate tab): only a 3D site can be exported'

export const exportTitle = (revision: number) =>
  `Download a copy of this 3D site (revision ${revision}) as a 3D model file (GLB) and a manifest to open in Blender. ` +
  'It is a snapshot: later edits here do not update it, and it is not a certified model. No photos are included.'

export interface SceneExportControl {
  /** Why the button is disabled, shown as visible text; null when it can run (or the scene is still loading). */
  reason: string | null
  /** The revision a click would export; null until a 3D scene with buildings is read. */
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
  const scene = useSiteScene(siteId)
  const [busy, setBusy] = useState(false)
  const data = scene.data
  const ready = data?.state === 'enabled' && data.buildings.length > 0
  const revision = ready ? data.revision : null
  const reason = !data || ready ? null : EXPORT_NO_SCENE
  const onExport = () => {
    if (!project || !siteId || !ready || busy) return
    setBusy(true)
    siteSceneApi.exportScene(project, siteId)
      .then(({ blob, filename, revision: got }) => {
        save(blob, filename)
        toast.success(`Downloaded ${filename}: a snapshot of revision ${got ?? revision}`, { id: 'scene-export', duration: 5000 })
      })
      .catch(err => { toast.error(sceneErrorCopy(err), { id: 'scene-export' }) })
      .finally(() => setBusy(false))
  }
  return { reason, revision, busy, onExport }
}
