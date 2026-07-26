import type { ButtonHTMLAttributes, FormEvent, InputHTMLAttributes, ReactNode } from 'react'
import { useMemo, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import App from './App'
import { authApi } from './api/auth'
import { useAuth } from './auth/AuthProvider'
import { authEnabled } from './auth/config'
import RequireAdmin from './auth/RequireAdmin'
import RequireAuth from './auth/RequireAuth'
import { getPostLoginPath } from './auth/resume'
import { useUIStore } from './store/uiStore'

type AuthLocationState = {
  from?: string
  message?: string
} | null

function isAuthPath(path: string | null | undefined): boolean {
  return path === '/login' || path === '/set-password' || path === '/reset-password'
}

function resolvePostLoginDestination(
  requestedPath: string | null,
  lastProjectId: string | null,
): string {
  if (requestedPath && requestedPath.startsWith('/') && !isAuthPath(requestedPath)) {
    return requestedPath
  }
  return getPostLoginPath(lastProjectId)
}

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

function AuthInput(
  props: InputHTMLAttributes<HTMLInputElement> & { label: string },
) {
  const { label, className, ...inputProps } = props
  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-medium">{label}</span>
      <input
        {...inputProps}
        className={`w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-text outline-none transition focus:border-accent ${className ?? ''}`}
      />
    </label>
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

function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const currentProject = useUIStore(s => s.currentProject)
  const { login, status } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [forgotEmail, setForgotEmail] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isSendingReset, setIsSendingReset] = useState(false)
  const [infoMessage, setInfoMessage] = useState<string | null>(null)

  const locationState = location.state as AuthLocationState
  const requestedPath = searchParams.get('next') ?? locationState?.from ?? null
  const destination = useMemo(
    () => resolvePostLoginDestination(requestedPath, currentProject),
    [currentProject, requestedPath],
  )

  if (status === 'authenticated') {
    return <Navigate to={destination} replace />
  }

  const flashMessage = infoMessage ?? locationState?.message ?? null

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setInfoMessage(null)
    setIsSubmitting(true)
    try {
      await login(email.trim(), password)
      navigate(destination, { replace: true })
    } finally {
      setIsSubmitting(false)
    }
  }

  async function handleForgotPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const target = (forgotEmail || email).trim()
    if (!target) {
      setInfoMessage('Enter an email address first.')
      return
    }
    setInfoMessage(null)
    setIsSendingReset(true)
    try {
      await authApi.forgot({ email: target })
      setInfoMessage('If that account exists, a reset link has been sent.')
    } finally {
      setIsSendingReset(false)
    }
  }

  return (
    <AuthShell
      title="Sign in"
      subtitle="Minimal auth shell for Task 10. Later tasks can replace this with the full UX."
    >
      {flashMessage && (
        <div className="rounded-lg border border-border bg-bg px-3 py-2 text-sm text-muted">
          {flashMessage}
        </div>
      )}
      <form className="space-y-4" onSubmit={handleSubmit}>
        <AuthInput
          autoComplete="email"
          label="Email"
          name="email"
          onChange={event => setEmail(event.target.value)}
          required
          type="email"
          value={email}
        />
        <AuthInput
          autoComplete="current-password"
          label="Password"
          name="password"
          onChange={event => setPassword(event.target.value)}
          required
          type="password"
          value={password}
        />
        <AuthButton disabled={isSubmitting} type="submit">
          {isSubmitting ? 'Signing in…' : 'Sign in'}
        </AuthButton>
      </form>

      <form className="space-y-3 border-t border-border pt-4" onSubmit={handleForgotPassword}>
        <AuthInput
          autoComplete="email"
          label="Forgot password?"
          name="forgot-email"
          onChange={event => setForgotEmail(event.target.value)}
          placeholder="Enter your email to request a reset link"
          type="email"
          value={forgotEmail}
        />
        <AuthButton className="bg-bg text-text border border-border hover:bg-bg-2" disabled={isSendingReset} type="submit">
          {isSendingReset ? 'Sending reset email…' : 'Send reset link'}
        </AuthButton>
      </form>
    </AuthShell>
  )
}

function PasswordTokenPage({
  mode,
  title,
  subtitle,
  successMessage,
}: {
  mode: 'set' | 'reset'
  title: string
  subtitle: string
  successMessage: string
}) {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') ?? ''
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token) {
      setMessage('Missing token. Open the full link from your email.')
      return
    }
    if (password !== confirmPassword) {
      setMessage('Passwords must match.')
      return
    }
    setMessage(null)
    setIsSubmitting(true)
    try {
      if (mode === 'set') {
        await authApi.setPassword({ token, password })
      } else {
        await authApi.resetPassword({ token, password })
      }
      navigate('/login', { replace: true, state: { message: successMessage } })
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <AuthShell title={title} subtitle={subtitle}>
      {message && (
        <div className="rounded-lg border border-border bg-bg px-3 py-2 text-sm text-muted">
          {message}
        </div>
      )}
      <form className="space-y-4" onSubmit={handleSubmit}>
        <AuthInput
          autoComplete="new-password"
          label="New password"
          minLength={8}
          onChange={event => setPassword(event.target.value)}
          required
          type="password"
          value={password}
        />
        <AuthInput
          autoComplete="new-password"
          label="Confirm password"
          minLength={8}
          onChange={event => setConfirmPassword(event.target.value)}
          required
          type="password"
          value={confirmPassword}
        />
        <AuthButton disabled={isSubmitting} type="submit">
          {isSubmitting ? 'Saving password…' : 'Save password'}
        </AuthButton>
      </form>
    </AuthShell>
  )
}

function SetPasswordPage() {
  return (
    <PasswordTokenPage
      mode="set"
      subtitle="Use the invitation token from your email to activate your account."
      successMessage="Password set. You can sign in now."
      title="Set password"
    />
  )
}

function ResetPasswordPage() {
  return (
    <PasswordTokenPage
      mode="reset"
      subtitle="Use the reset token from your email to choose a new password."
      successMessage="Password reset. Sign in with your new password."
      title="Reset password"
    />
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
