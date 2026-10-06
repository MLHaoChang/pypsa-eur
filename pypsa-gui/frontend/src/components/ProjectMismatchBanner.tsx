// A2 — the blocking banner while this tab and the backend disagree about the
// open project (deferred spec 2026-09-28 §2.1, "Banner"). Shown in both modes:
// it is a data-integrity guard, not a Guided feature. While it shows, the
// axios interceptor refuses the tab's writes, autosave is suspended and chat
// Send is gated; the two buttons are the way out.
//   Reload <tab>   — `projectsApi.load(tab)` (a GET, never blocked). It can be
//                    refused with `study_in_flight` while a study runs on the
//                    backend's project: the sentence shows under the button
//                    and Switch stays available.
//   Switch to <backend> — `switchToProject(backend)` (`POST …/activate`,
//                    allowlisted), adopting what the backend has.
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'
import { projectsApi } from '../api/projects'
import { useUIStore } from '../store/uiStore'
import { appLog } from '../store/simulationStore'
import { blockerMessage } from '../utils/blockerMessage'
import {
  acquireProjectLock, invalidateNetworkQueries, lastHeldLockProject, switchToProject,
} from '../utils/projectActions'
import { authEnabled } from '../auth/config'
import { mismatchSentence } from '../utils/projectMismatch'
import { nk } from '../utils/queryKeys'

export default function ProjectMismatchBanner() {
  const mismatch = useUIStore(s => s.projectMismatch)
  const qc = useQueryClient()
  const [busy, setBusy] = useState<'reload' | 'switch' | null>(null)
  const [reloadError, setReloadError] = useState<string | null>(null)
  const [switchError, setSwitchError] = useState<string | null>(null)
  if (!mismatch) return null
  const { tab, backend } = mismatch

  const reload = async () => {
    setBusy('reload'); setReloadError(null); setSwitchError(null)
    try {
      await projectsApi.load(tab)
      useUIStore.getState().setProjectMismatch(null)
      // Auth mode (P27b gate B1): take back the edit lock this tab held on
      // its project — a mismatch can outlive the lock (it expires, or a
      // heartbeat was lost). Acquire is idempotent for the holder; a lock
      // another user took meanwhile leaves the tab read-only, as it should.
      if (authEnabled && lastHeldLockProject() === tab) await acquireProjectLock(tab)
      invalidateNetworkQueries(qc, tab)
      void qc.invalidateQueries({ queryKey: nk(tab, 'results') })
      appLog('INFO', `Reloaded '${tab}' — the backend was on '${backend}'`)
    } catch (e) {
      setReloadError(blockerMessage(e))
    } finally {
      setBusy(null)
    }
  }

  const adopt = async () => {
    setBusy('switch'); setReloadError(null); setSwitchError(null)
    const ui = useUIStore.getState()
    ui.setProjectSwitchInProgress(true)
    try {
      const r = await switchToProject(backend, qc)
      if (r?.status === 'switched' || r?.status === 'noop') {
        useUIStore.getState().setProjectMismatch(null)
        appLog('INFO', `Switched this tab to '${backend}' (the backend's project)`)
      } else {
        setSwitchError(r?.status === 'busy-study' ? r.message
          : `Could not switch to ${backend} (${r?.status ?? 'error'}).`)
      }
    } catch (e) {
      setSwitchError(blockerMessage(e))
    } finally {
      useUIStore.getState().setProjectSwitchInProgress(false)
      setBusy(null)
    }
  }

  return (
    <div data-testid="project-mismatch" role="alert"
      className="flex flex-wrap items-start gap-3 border-b border-warn/40 bg-warn/10 px-4 py-2 text-[12px] text-text">
      <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warn" />
      <span className="flex-1 min-w-[16rem]">{mismatchSentence(mismatch)}</span>
      <div className="flex flex-col items-start gap-1">
        <button type="button" data-testid="project-mismatch-reload" onClick={reload}
          disabled={busy !== null}
          className="rounded border border-border px-2 py-1 text-[11px] hover:border-accent hover:text-accent disabled:opacity-50">
          {`Reload ${tab}`}
        </button>
        {reloadError && (
          <span data-testid="project-mismatch-reload-error" className="max-w-md text-[11px] text-danger">
            {reloadError}
          </span>
        )}
      </div>
      <div className="flex flex-col items-start gap-1">
        <button type="button" data-testid="project-mismatch-switch" onClick={adopt}
          disabled={busy !== null}
          className="rounded bg-accent px-2 py-1 text-[11px] font-semibold text-white disabled:opacity-50">
          {`Switch to ${backend}`}
        </button>
        {switchError && (
          <span data-testid="project-mismatch-switch-error" className="max-w-md text-[11px] text-danger">
            {switchError}
          </span>
        )}
      </div>
    </div>
  )
}
