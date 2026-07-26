import axios from 'axios'
import { type FormEvent, useMemo, useState } from 'react'
import { Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../../auth/AuthProvider'
import { getPostLoginPath } from '../../auth/resume'
import { requestPasswordReset } from '../../auth/requests'
import { useUIStore } from '../../store/uiStore'
import {
  AuthButton,
  AuthInput,
  AuthMessage,
  AuthSecondaryButton,
  AuthSplitLayout,
} from './AuthSplitLayout'

function formatAuthError(error: unknown, fallback: string): string {
  if (!axios.isAxiosError(error)) return fallback
  const detail = error.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (error.response?.status === 503) {
    return 'Auth database unavailable. Check DATABASE_URL, then restart the backend.'
  }
  if (error.response?.status === 500) {
    return 'Sign-in failed on the server. Confirm the backend is running with auth DB ready.'
  }
  return error.message || fallback
}

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

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const lastProjectId = useUIStore(s => s.lastProjectId)
  const { login, status } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [forgotEmail, setForgotEmail] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isSendingReset, setIsSendingReset] = useState(false)
  const [infoMessage, setInfoMessage] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const locationState = location.state as AuthLocationState
  const requestedPath = searchParams.get('next') ?? locationState?.from ?? null
  const destination = useMemo(
    () => resolvePostLoginDestination(requestedPath, lastProjectId),
    [lastProjectId, requestedPath],
  )

  if (status === 'authenticated') {
    return <Navigate replace to={destination} />
  }

  const flashMessage = infoMessage ?? locationState?.message ?? null

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setInfoMessage(null)
    setErrorMessage(null)
    setIsSubmitting(true)
    try {
      await login(email, password)
      navigate(destination, { replace: true })
    } catch (error) {
      setErrorMessage(formatAuthError(error, 'Sign-in failed. Check your email and password.'))
    } finally {
      setIsSubmitting(false)
    }
  }

  async function handleForgotPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setInfoMessage(null)
    setErrorMessage(null)
    setIsSendingReset(true)
    try {
      const result = await requestPasswordReset(forgotEmail, email)
      if (result.ok) {
        setInfoMessage(result.message)
      } else {
        setErrorMessage(result.message)
      }
    } catch (error) {
      setErrorMessage(formatAuthError(error, 'Could not send a reset link.'))
    } finally {
      setIsSendingReset(false)
    }
  }

  return (
    <AuthSplitLayout
      subtitle="Sign in to resume your last project, or send yourself a password reset link."
      title="Welcome back"
    >
      {flashMessage && <AuthMessage>{flashMessage}</AuthMessage>}
      {errorMessage && <AuthMessage tone="error">{errorMessage}</AuthMessage>}

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

      {/* Brand tokens, not slate/light-hairline utilities. This block was
          authored for a light card and kept `text-slate-900` + a #e5ece7 rule
          after the card became dark glass — near-black text on a near-black
          panel, effectively invisible. */}
      <form className="space-y-4 border-t border-white/14 pt-5" onSubmit={handleForgotPassword}>
        <div className="space-y-1">
          <h3 className="text-sm font-medium text-[var(--brand-ink)]">Forgot your password?</h3>
          <p className="text-sm leading-6 text-[var(--brand-ink-dim)]">
            Leave the field below blank to use the email you entered above, or type a different address.
          </p>
        </div>
        <AuthInput
          autoComplete="email"
          label="Email for reset link"
          name="forgot-email"
          onChange={event => setForgotEmail(event.target.value)}
          placeholder="you@example.com"
          type="email"
          value={forgotEmail}
        />
        <AuthSecondaryButton disabled={isSendingReset} type="submit">
          {isSendingReset ? 'Sending reset link…' : 'Send reset link'}
        </AuthSecondaryButton>
      </form>
    </AuthSplitLayout>
  )
}
