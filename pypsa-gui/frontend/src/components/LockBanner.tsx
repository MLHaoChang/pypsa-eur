import { Lock } from 'lucide-react'
import { useUIStore } from '../store/uiStore'
import { authEnabled } from '../auth/config'
import { readOnlyBannerMessage } from '../utils/mutationGuard'

// Read-only banner (Task 14). Shows a thin amber strip whenever the active
// project cannot be edited, so the viewer understands why edits are disabled
// and — when someone else holds the lock — who to ask. Renders nothing in the
// writable case or when auth is disabled (legacy single-user workbench is
// always writable), so it never steals vertical space unnecessarily — the same
// pattern CrashRecoveryBanner uses.
//
// The REASON, not just the flag, picks the sentence. `readOnly` has had two
// causes since the queue work: another user holding the edit lock, and a queue
// job solving this project. Reading only the flag (and only the holder email)
// printed "Another user is editing this project" at a user whose own project
// was simply solving — the exact lie the reason widening exists to remove.
export default function LockBanner() {
  const readOnly = useUIStore(s => s.readOnly)
  const reason = useUIStore(s => s.readOnlyReason)
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
        {readOnlyBannerMessage(reason, holder)}
      </span>
    </div>
  )
}
