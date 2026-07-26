import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Loader, Users, X } from 'lucide-react'
import { projectsApi } from '../api/projects'
import { authApi } from '../api/auth'
import { useAuth } from '../auth/AuthProvider'

interface Props {
  // Project identifier (name or id) — the backend resolves it to the tree ROOT
  // and attaches membership there.
  projectId: string
  // Human-friendly label for the header (usually the same as projectId).
  projectLabel?: string
  onClose: () => void
}

// Assign-members dialog (Task 14). Reads/writes /api/projects/{id}/members.
// Candidate users come from the non-admin org-member directory
// (GET /api/auth/org-members), which any authenticated org member can read —
// so a project CREATOR (not just an org admin) can assign colleagues. The
// directory is already scoped to the caller's own organization server-side.
// If the save is refused (a non-owner / non-admin), the backend returns 403
// and we surface that as a toast rather than dead-ending the dialog.
export default function AssignMembersDialog({ projectId, projectLabel, onClose }: Props) {
  const qc = useQueryClient()
  const { user } = useAuth()

  const membersQuery = useQuery({
    queryKey: ['project-members', projectId],
    queryFn: () => projectsApi.getMembers(projectId),
    staleTime: 5_000,
  })

  // Candidate users to pick from — the org-member directory any authenticated
  // member can read. `retry: false` so a rare failure surfaces immediately as
  // the degraded read-only path instead of retrying.
  const usersQuery = useQuery({
    queryKey: ['org-members-for-assign'],
    queryFn: () => authApi.orgMembers(),
    retry: false,
    staleTime: 30_000,
  })

  // Selected user-id set — seeded from the current membership once both queries
  // resolve, then owned by local state so toggles are instant.
  const [selected, setSelected] = useState<Set<string> | null>(null)

  const currentMemberIds = useMemo(
    () => new Set((membersQuery.data ?? []).map(m => m.user_id)),
    [membersQuery.data],
  )

  // Candidate users — the org-member directory is already scoped to the
  // acting user's organization server-side, so we only sort for a stable list.
  const candidates = useMemo(() => {
    return [...(usersQuery.data ?? [])].sort((a, b) => a.email.localeCompare(b.email))
  }, [usersQuery.data])

  const effectiveSelected = selected ?? currentMemberIds
  const canManage = !usersQuery.isError

  const toggle = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev ?? currentMemberIds)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const saveMut = useMutation({
    mutationFn: () => projectsApi.setMembers(projectId, [...effectiveSelected]),
    onSuccess: (rows) => {
      qc.setQueryData(['project-members', projectId], rows)
      qc.invalidateQueries({ queryKey: ['project-members', projectId] })
      toast.success(`Members updated · ${rows.length} assigned`)
      onClose()
    },
    onError: (e: unknown) => {
      const status = (e as { response?: { status?: number } })?.response?.status
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      if (status === 403) {
        toast.error('You do not have permission to manage members on this project.')
        return
      }
      toast.error(typeof detail === 'string' ? detail : 'Could not update members.')
    },
  })

  const loading = membersQuery.isLoading || usersQuery.isLoading

  return (
    <div
      className="fixed inset-0 z-[400] flex items-center justify-center bg-black/30"
      onClick={onClose}
      data-no-panel-close
    >
      <div
        className="bg-bg rounded-lg shadow-2xl w-[460px] max-w-[92vw] border border-border"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
          <Users size={14} className="text-accent shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="text-xs font-semibold text-text truncate">
              Assign members — <span className="text-accent">{projectLabel ?? projectId}</span>
            </div>
            <div className="text-[10px] text-muted mt-0.5">
              Members can open every scenario in this project's tree.
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-muted hover:text-text rounded transition-colors"
            title="Close"
          >
            <X size={14} />
          </button>
        </div>

        <div className="p-3 max-h-[50vh] overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-[11.5px] text-muted">
              <Loader size={13} className="animate-spin" /> Loading members…
            </div>
          ) : !canManage ? (
            // Degraded path — the org-member directory couldn't be loaded. Show
            // the current members read-only so the dialog is still informative.
            <div className="space-y-2">
              <div className="text-[11px] text-warn bg-warn/10 border border-warn/30 rounded px-2 py-1.5">
                Couldn't load your organization's member directory. Showing the current members only.
              </div>
              {(membersQuery.data ?? []).length === 0 ? (
                <div className="text-[11.5px] text-muted py-4 text-center">No members assigned yet.</div>
              ) : (
                <ul className="space-y-1">
                  {(membersQuery.data ?? []).map(m => (
                    <li key={m.user_id} className="text-[12px] text-text px-2 py-1.5 rounded bg-bg-2 border border-border truncate">
                      {m.email ?? m.user_id}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : candidates.length === 0 ? (
            <div className="text-[11.5px] text-muted py-6 text-center">
              No other users in your organization to assign.
            </div>
          ) : (
            <ul className="space-y-1">
              {candidates.map(u => {
                const checked = effectiveSelected.has(u.id)
                const isSelf = u.id === user?.id
                return (
                  <li key={u.id}>
                    <label className="flex items-center gap-2.5 px-2 py-1.5 rounded hover:bg-bg-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(u.id)}
                        className="accent-accent"
                      />
                      <span className="flex-1 min-w-0 text-[12px] text-text truncate">
                        {u.email}
                        {isSelf && <span className="text-[10px] text-muted"> (you)</span>}
                      </span>
                      {u.role && (
                        <span className="text-[9px] font-mono uppercase tracking-wide text-muted">{u.role}</span>
                      )}
                    </label>
                  </li>
                )
              })}
            </ul>
          )}
        </div>

        <div className="flex justify-end gap-1.5 px-3 py-2 border-t border-border">
          <button
            onClick={onClose}
            className="px-3 py-1 text-xs border border-border rounded text-muted hover:text-text hover:border-text/40 transition-colors"
          >
            {canManage ? 'Cancel' : 'Close'}
          </button>
          {canManage && (
            <button
              onClick={() => saveMut.mutate()}
              disabled={saveMut.isPending || loading}
              className="px-3 py-1 text-xs rounded bg-accent text-white disabled:opacity-40 disabled:cursor-not-allowed hover:bg-accent/85 transition-colors inline-flex items-center gap-1.5"
            >
              {saveMut.isPending && <Loader size={12} className="animate-spin" />}
              Save members
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
