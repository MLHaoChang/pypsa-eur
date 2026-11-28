import { useState } from 'react'
import { Users } from 'lucide-react'
import { authEnabled } from '../auth/config'
import { useUIStore } from '../store/uiStore'
import AssignMembersDialog from './AssignMembersDialog'

// The one entry point to sharing, in Guided (project header) and Expert
// (Workspace panel) alike. Shows only to people who may manage membership.
export default function ShareButton({ projectId, className = '' }: { projectId: string; className?: string }) {
  const canManage = useUIStore(s => s.canManageMembers)
  const ownerEmail = useUIStore(s => s.ownerEmail)
  const [open, setOpen] = useState(false)
  if (!authEnabled || !canManage) return null
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Share this project and set who can edit"
        className={`inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-muted hover:text-text hover:border-text/40 ${className}`}
      >
        <Users size={12} /> Share
      </button>
      {open && <AssignMembersDialog projectId={projectId} ownerEmail={ownerEmail} onClose={() => setOpen(false)} />}
    </>
  )
}
