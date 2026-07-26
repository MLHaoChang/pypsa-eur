import { Lock } from 'lucide-react'
import { useUIStore } from '../store/uiStore'
import { authEnabled } from '../auth/config'

// Read-only banner (Task 14). Shows a thin amber strip whenever the active
// project is held by another user (or the lock couldn't be acquired), so the
// viewer understands why edits are disabled and who to ask. Renders nothing in
// the writable case or when auth is disabled (legacy single-user workbench is
// always writable), so it never steals vertical space unnecessarily — the same
// pattern CrashRecoveryBanner uses.
export default function LockBanner() {
  const readOnly = useUIStore(s => s.readOnly)
  const holder = useUIStore(s => s.lockHolderEmail)

  if (!authEnabled || !readOnly) return null

  return (
    <div
      role="status"
      className="flex items-center gap-2 px-3 h-8 shrink-0 border-b border-amber-500/30 bg-amber-500/10 text-[11.5px] text-amber-700 dark:text-amber-300"
    >
      <Lock size={12} className="shrink-0" />
      <span className="font-semibold">Read-only</span>
      <span className="text-amber-700/80 dark:text-amber-300/80">
        {holder
          ? `${holder} is currently editing this project — your changes are disabled until the lock is released.`
          : 'Another user is editing this project — your changes are disabled until the lock is released.'}
      </span>
    </div>
  )
}
