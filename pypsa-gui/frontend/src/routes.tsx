import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Link, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import App from './App'
import { useAuth } from './auth/AuthProvider'
import { authEnabled } from './auth/config'
import RequireAdmin from './auth/RequireAdmin'
import RequireAuth from './auth/RequireAuth'
import { getPostLoginPath } from './auth/resume'
import LoginPage from './pages/auth/LoginPage'
import ResetPasswordPage from './pages/auth/ResetPasswordPage'
import SetPasswordPage from './pages/auth/SetPasswordPage'
import { useUIStore } from './store/uiStore'

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

function AuthButton(
  props: ButtonHTMLAttributes<HTMLButtonElement> & { children: ReactNode },
) {
  const { children, className, ...buttonProps } = props
  return (
    <button
      {...buttonProps}
      className={`inline-flex w-full items-center justify-center rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60 ${className ?? ''}`}
    >
      {children}
    </button>
  )
}

function ProjectsHomePage() {
  const navigate = useNavigate()
  const currentProject = useUIStore(s => s.currentProject)
  const { logout, user } = useAuth()
  const resumePath = currentProject ? getPostLoginPath(currentProject) : '/app'

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <AuthShell
      title="Projects"
      subtitle="Placeholder landing page for the auth shell. Later tasks can replace this with the real projects home."
    >
      <div className="rounded-lg border border-border bg-bg px-3 py-2 text-sm text-muted">
        Signed in as <span className="font-medium text-text">{user?.email ?? 'unknown user'}</span>
      </div>
      <div className="space-y-3">
        <Link
          className="inline-flex w-full items-center justify-center rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white transition hover:opacity-90"
          to={resumePath}
        >
          {currentProject ? `Resume ${currentProject}` : 'Open editor'}
        </Link>
        <Link
          className="inline-flex w-full items-center justify-center rounded-lg border border-border bg-bg px-3 py-2 text-sm font-medium text-text transition hover:bg-bg-2"
          to="/app"
        >
          Go to app shell
        </Link>
        {user?.is_super_admin && (
          <Link
            className="inline-flex w-full items-center justify-center rounded-lg border border-border bg-bg px-3 py-2 text-sm font-medium text-text transition hover:bg-bg-2"
            to="/admin"
          >
            Open admin shell
          </Link>
        )}
        <AuthButton className="bg-bg text-text border border-border hover:bg-bg-2" onClick={handleLogout} type="button">
          Sign out
        </AuthButton>
      </div>
    </AuthShell>
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
