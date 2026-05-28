import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, X } from 'lucide-react'
import { projectsApi } from '../api/projects'

// CrashRecoveryBanner — top-of-app banner shown when any project on disk
// has an orphaned `*.tmp` sibling next to a bundle file. The orphan is
// `_atomic_write_with`'s tell-tale that a prior save was killed AFTER the
// .tmp write but BEFORE the `os.replace(tmp, final)` step. The main file
// is still the previous-version (the rename is atomic), but the partial
// write hasn't been explicitly committed or discarded — the user should
// investigate before continuing to edit.
//
// Dismissal is per-session: we don't persist the "I've seen this" state to
// localStorage because that would hide a recurring problem (e.g. if the
// orphan is from a new crash after a previous dismissal, the user would
// never notice). The list endpoint polls every 30 s as the natural ground
// truth — once the user resolves the orphan (delete or rename), the
// banner disappears on next refetch.
export default function CrashRecoveryBanner() {
  const [dismissed, setDismissed] = useState(false)

  // Reuse the existing projects cache — App.tsx already queries `['projects']`
  // for the recents-prune effect, so this is normally a cache hit.
  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    staleTime: 10_000,
    refetchInterval: 30_000,
  })

  // Find projects with orphans. Multiple at once is rare but possible (e.g.
  // sequential crashes during a session); the banner enumerates them so the
  // user knows the scope.
  const orphaned = useMemo(
    () => projects.filter(p => p.has_orphan_tmp),
    [projects],
  )

  if (dismissed || orphaned.length === 0) return null

  const names = orphaned.map(p => p.name).join(', ')

  return (
    <div className="shrink-0 flex items-start gap-2 px-3 py-2 bg-warn/10 border-b border-warn/40 text-xs">
      <AlertTriangle className="text-warn shrink-0 mt-0.5" size={14} />
      <div className="flex-1 min-w-0">
        <div className="font-semibold text-text">
          Interrupted save detected{orphaned.length > 1 ? `s` : ''} —{' '}
          <span className="font-mono">{names}</span>
        </div>
        <div className="text-muted mt-0.5">
          A previous save was killed mid-write. The project loaded from the
          previous good copy, but an orphaned <span className="font-mono">.tmp</span>{' '}
          sibling is in the project folder. Inspect or delete it before continuing.
        </div>
      </div>
      <button
        onClick={() => setDismissed(true)}
        className="shrink-0 text-muted hover:text-text transition-colors"
        title="Dismiss for this session"
      >
        <X size={12} />
      </button>
    </div>
  )
}
