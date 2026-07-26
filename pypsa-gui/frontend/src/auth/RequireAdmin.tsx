import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from './AuthProvider'

export default function RequireAdmin({ children }: { children: ReactNode }) {
  const { isAdmin, status } = useAuth()

  if (status === 'loading') {
    return null
  }

  if (!isAdmin) {
    return <Navigate to="/projects" replace />
  }

  return <>{children}</>
}
