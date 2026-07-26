import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from './AuthProvider'

function AuthLoadingScreen() {
  return (
    <div className="min-h-screen bg-bg text-text flex items-center justify-center px-6">
      <div className="w-full max-w-md rounded-2xl border border-border bg-panel p-6 text-center shadow-sm">
        <p className="text-sm font-medium">Checking your session…</p>
      </div>
    </div>
  )
}

export default function RequireAuth() {
  const location = useLocation()
  const { status } = useAuth()

  if (status === 'loading') {
    return <AuthLoadingScreen />
  }

  if (status !== 'authenticated') {
    const from = `${location.pathname}${location.search}${location.hash}`
    return <Navigate to="/login" replace state={{ from }} />
  }

  return <Outlet />
}
