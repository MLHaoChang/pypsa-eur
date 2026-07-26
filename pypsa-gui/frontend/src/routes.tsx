import type { ReactNode } from 'react'
import { Link, Navigate, Route, Routes } from 'react-router-dom'
import App from './App'
import { useAuth } from './auth/AuthProvider'
import { authEnabled } from './auth/config'
import RequireAdmin from './auth/RequireAdmin'
import RequireAuth from './auth/RequireAuth'
import LoginPage from './pages/auth/LoginPage'
import ResetPasswordPage from './pages/auth/ResetPasswordPage'
import SetPasswordPage from './pages/auth/SetPasswordPage'
import ProjectsHomePage from './pages/ProjectsHomePage'

function AuthShell({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle: string
  children: ReactNode
}) {
  return (
    <div className="min-h-screen bg-bg text-text flex items-center justify-center px-6 py-12">
      <div className="w-full max-w-md rounded-2xl border border-border bg-panel p-6 shadow-sm">
        <div className="mb-6 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">PyPSA GUI</p>
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="text-sm text-muted">{subtitle}</p>
        </div>
        <div className="space-y-4">{children}</div>
      </div>
    </div>
  )
}

function AdminLayout() {
  const { user } = useAuth()
  return (
    <AuthShell
      title="Admin"
      subtitle="Placeholder admin shell for Task 10. Later tasks can fill in the real admin experience."
    >
      <div className="rounded-lg border border-border bg-bg px-3 py-2 text-sm text-muted">
        Admin access confirmed for <span className="font-medium text-text">{user?.email ?? 'unknown user'}</span>.
      </div>
      <Link
        className="inline-flex w-full items-center justify-center rounded-lg border border-border bg-bg px-3 py-2 text-sm font-medium text-text transition hover:bg-bg-2"
        to="/projects"
      >
        Back to projects
      </Link>
    </AuthShell>
  )
}

export default function AppRoutes() {
  if (!authEnabled) {
    return (
      <Routes>
        <Route element={<App />} path="*" />
      </Routes>
    )
  }

  return (
    <Routes>
      <Route element={<LoginPage />} path="/login" />
      <Route element={<SetPasswordPage />} path="/set-password" />
      <Route element={<ResetPasswordPage />} path="/reset-password" />
      <Route element={<RequireAuth />}>
        <Route element={<Navigate replace to="/projects" />} path="/" />
        <Route element={<ProjectsHomePage />} path="/projects" />
        <Route element={<App />} path="/app" />
        <Route
          element={(
            <RequireAdmin>
              <AdminLayout />
            </RequireAdmin>
          )}
          path="/admin/*"
        />
      </Route>
      <Route element={<Navigate replace to="/projects" />} path="*" />
    </Routes>
  )
}
