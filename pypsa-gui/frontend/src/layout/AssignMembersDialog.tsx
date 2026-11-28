import { useId, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Loader, Users, X } from 'lucide-react'
import { projectsApi } from '../api/projects'
import { authApi } from '../api/auth'
import { useAuth } from '../auth/AuthProvider'
import { Dialog } from '../components/Dialog'

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
  const titleId = useId()

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

  type Role = 'editor' | 'viewer'
  // Desired role per member user id, seeded from the current membership then
  // owned by local state so changes are instant. A user absent from the map is
  // not a member.
  const [draft, setDraft] = useState<Map<string, Role> | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [discarding, setDiscarding] = useState(false)

  const currentRoles = useMemo(
    () => new Map((membersQuery.data ?? []).map(m => [m.user_id, m.role] as [string, Role])),
    [membersQuery.data],
  )
  const fixedIds = useMemo(
    () => new Set((membersQuery.data ?? []).filter(m => m.role_fixed).map(m => m.user_id)),
    [membersQuery.data],
  )

  // Candidate users — the org-member directory is already scoped to the
  // acting user's organization server-side, so we only sort for a stable list.
  const candidates = useMemo(() => {
    return [...(usersQuery.data ?? [])].sort((a, b) => a.email.localeCompare(b.email))
  }, [usersQuery.data])

  const roles = draft ?? currentRoles
  const canManage = !usersQuery.isError
  const dirty = useMemo(() => {
    if (draft === null) return false
    if (draft.size !== currentRoles.size) return true
    for (const [id, r] of draft) if (currentRoles.get(id) !== r) return true
    return false
  }, [draft, currentRoles])

  const edit = (fn: (next: Map<string, Role>) => void) => {
    setConfirming(false)
    setDraft(prev => {
      const next = new Map(prev ?? currentRoles)
      fn(next)
      return next
    })
  }
  const toggle = (id: string) => edit(next => {
    if (next.has(id)) next.delete(id)
    else next.set(id, 'editor')
  })
  const setRole = (id: string, role: Role) => edit(next => { next.set(id, role) })

  // Ask before a change that takes the caller's own edit access away, or that
  // leaves nobody able to edit.
  const risky = useMemo(() => {
    const me = user?.id
    const meLoses = !!me && currentRoles.get(me) === 'editor' && !fixedIds.has(me)
      && roles.get(me) !== 'editor'
    const editors = [...roles].filter(([id, r]) => r === 'editor' || fixedIds.has(id)).length
    return meLoses || editors === 0
  }, [user?.id, currentRoles, fixedIds, roles])

  const owner = (membersQuery.data ?? []).find(m => m.role_fixed && candidates.find(c => c.id === m.user_id)?.role !== 'admin')
  const requestClose = () => { if (dirty && !discarding) setDiscarding(true); else onClose() }

  const saveMut = useMutation({
    mutationFn: () => projectsApi.setMembers(projectId, [...roles.keys()], Object.fromEntries(roles)),
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
    <Dialog
      open
      onClose={requestClose}
      aria-labelledby={titleId}
      z={400}
      panelClassName="bg-bg rounded-lg shadow-2xl w-[460px] max-w-[92vw] border border-border"
      data-no-panel-close
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <Users size={14} className="text-accent shrink-0" />
        <div className="flex-1 min-w-0">
          <div id={titleId} className="text-xs font-semibold text-text truncate">
            Share — <span className="text-accent">{projectLabel ?? projectId}</span>
          </div>
          <div className="text-[10px] text-muted mt-0.5">
            People you add can open every scenario in this project's tree. Viewers cannot change anything.
            {owner?.email && <> Owner: {owner.email}.</>}
          </div>
        </div>
        <button
          onClick={requestClose}
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
              const role = roles.get(u.id)
              const checked = role !== undefined
              const isSelf = u.id === user?.id
              const fixed = fixedIds.has(u.id)
              return (
                <li key={u.id}>
                  <div className="flex items-center gap-2.5 px-2 py-1.5 rounded hover:bg-bg-2">
                    <input
                      id={`share-${u.id}`}
                      type="checkbox"
                      checked={checked}
                      disabled={fixed}
                      onChange={() => toggle(u.id)}
                      className="accent-accent"
                    />
                    <label htmlFor={`share-${u.id}`} className="flex-1 min-w-0 text-[12px] text-text truncate cursor-pointer">
                      {u.email}
                      {isSelf && <span className="text-[10px] text-muted"> (you)</span>}
                    </label>
                    {fixed ? (
                      <span className="text-[11px] text-muted">{u.role === 'admin' ? 'Admin' : 'Owner'}</span>
                    ) : checked ? (
                      <select
                        aria-label={`Role for ${u.email}`}
                        value={role}
                        onChange={e => setRole(u.id, e.target.value as Role)}
                        className="text-[11px] bg-bg border border-border rounded px-1 py-0.5 text-text"
                      >
                        <option value="editor">Editor</option>
                        <option value="viewer">Viewer</option>
                      </select>
                    ) : null}
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </div>

      <div className="px-3 py-2 border-t border-border space-y-1.5">
        {(discarding || confirming || (canManage && !dirty && !loading)) && (
          <div role="status" className="text-[11px] text-muted">
            {discarding
              ? 'Discard your changes?'
              : confirming
                ? 'This leaves you or the project without edit access. Save anyway?'
                : 'No changes to save.'}
          </div>
        )}
        <div className="flex justify-end gap-1.5">
          {discarding ? (
            <>
              <button onClick={() => setDiscarding(false)} className="px-3 py-1 text-xs border border-border rounded text-muted hover:text-text hover:border-text/40 transition-colors">Keep editing</button>
              <button onClick={onClose} className="px-3 py-1 text-xs rounded bg-accent text-white hover:bg-accent/85 transition-colors">Discard</button>
            </>
          ) : (
            <>
              <button
                onClick={requestClose}
                className="px-3 py-1 text-xs border border-border rounded text-muted hover:text-text hover:border-text/40 transition-colors"
              >
                {canManage ? 'Cancel' : 'Close'}
              </button>
              {canManage && (
                <button
                  onClick={() => { if (risky && !confirming) setConfirming(true); else saveMut.mutate() }}
                  disabled={saveMut.isPending || loading || !dirty}
                  className="px-3 py-1 text-xs rounded bg-accent text-white disabled:opacity-40 disabled:cursor-not-allowed hover:bg-accent/85 transition-colors inline-flex items-center gap-1.5"
                >
                  {saveMut.isPending && <Loader size={12} className="animate-spin" />}
                  {confirming ? 'Save anyway' : 'Save'}
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </Dialog>
  )
}
