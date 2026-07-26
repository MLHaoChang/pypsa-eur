import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronDown, FolderOpen, LogOut, User as UserIcon, Users } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { redirectAfterLogout } from '../auth/logoutRedirect'
import { useUIStore } from '../store/uiStore'
import { releaseProjectLock } from '../utils/projectActions'
import AssignMembersDialog from './AssignMembersDialog'

// Workbench user menu (Task 14). Header dropdown carrying the signed-in
// identity plus the three navigation exits the workbench previously lacked:
// assign members, back to the projects home, and sign out. Both exits release
// the active project's edit lock first so a teammate can pick it up immediately
// rather than waiting out the TTL. Renders nothing when auth is disabled.
export default function UserMenu() {
  const navigate = useNavigate()
  const { user, logout } = useAuth()
  const currentProject = useUIStore(s => s.currentProject)

  const [open, setOpen] = useState(false)
  const [assignOpen, setAssignOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDown, true)
    return () => document.removeEventListener('mousedown', onDown, true)
  }, [open])

  const backToProjects = () => {
    setOpen(false)
    if (currentProject) void releaseProjectLock(currentProject)
    navigate('/projects')
  }

  const handleLogout = async () => {
    setOpen(false)
    if (currentProject) void releaseProjectLock(currentProject)
    await logout()
    redirectAfterLogout()
  }

  const email = user?.email ?? 'account'
  const initial = email.charAt(0).toUpperCase()

  return (
    <div ref={rootRef} className="relative" data-no-panel-close>
      <button
        onClick={() => setOpen(o => !o)}
        title={email}
        className="flex items-center gap-1.5 pl-1 pr-1.5 py-1 rounded text-[11px] font-medium border border-border text-text hover:bg-border/30 transition-colors shrink-0"
      >
        <span className="flex items-center justify-center w-5 h-5 rounded-full bg-accent/15 text-accent text-[10px] font-bold shrink-0">
          {initial}
        </span>
        <ChevronDown size={12} className="text-muted" />
      </button>

      {open && (
        <div className="absolute top-full right-0 mt-1 w-56 bg-bg border border-border rounded-lg shadow-lg z-50 py-1">
          <div className="px-3 py-2 border-b border-border">
            <div className="flex items-center gap-2">
              <span className="flex items-center justify-center w-6 h-6 rounded-full bg-accent/15 text-accent text-[11px] font-bold shrink-0">
                {initial}
              </span>
              <div className="min-w-0">
                <div className="text-[9px] font-mono uppercase tracking-[0.14em] text-muted flex items-center gap-1">
                  <UserIcon size={9} /> Signed in
                </div>
                <div className="text-[11.5px] font-semibold text-text truncate" title={email}>{email}</div>
              </div>
            </div>
          </div>

          <button
            onClick={() => { setOpen(false); setAssignOpen(true) }}
            disabled={!currentProject}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-[12px] text-text hover:bg-accent/5 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            title={currentProject ? 'Assign members to this project' : 'Open a project first'}
          >
            <Users size={13} className="text-muted shrink-0" /> Assign members…
          </button>
          <button
            onClick={backToProjects}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-[12px] text-text hover:bg-accent/5 transition-colors"
          >
            <FolderOpen size={13} className="text-muted shrink-0" /> Back to projects
          </button>
          <div className="my-1 border-t border-border" />
          <button
            onClick={handleLogout}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-[12px] text-danger hover:bg-danger/10 transition-colors"
          >
            <LogOut size={13} className="shrink-0" /> Sign out
          </button>
        </div>
      )}

      {assignOpen && currentProject && (
        <AssignMembersDialog
          projectId={currentProject}
          projectLabel={currentProject}
          onClose={() => setAssignOpen(false)}
        />
      )}
    </div>
  )
}
