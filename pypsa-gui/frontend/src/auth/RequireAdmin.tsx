import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from './AuthProvider'
import { useAuthMode } from './AuthModeProvider'

export default function RequireAdmin({ children }: { children: ReactNode }) {
  const { isAdmin, status } = useAuth()
  const { authEnabled } = useAuthMode()

  if (status === 'loading') {
    return null
  }

  // `isAdmin` alone cannot refuse the desktop app. `AuthProvider` synthesizes
  // `localAdminUser()` with `is_super_admin: true` — deliberately, because
  // `null` broke other checks — so every local user is an admin here, while
  // the backend never mounts `routers/admin.py` and every `/api/admin/*` call
  // 404s. Hiding the "Open admin" button closes the only path a user can
  // click; this closes the route, so a future link cannot reopen it.
  if (!authEnabled || !isAdmin) {
    return <Navigate to="/projects" replace />
  }

  return <>{children}</>
}
