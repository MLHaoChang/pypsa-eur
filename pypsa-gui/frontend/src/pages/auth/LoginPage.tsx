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
    return <Navigate replace to={destination} />
  }

  const flashMessage = infoMessage ?? locationState?.message ?? null

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setInfoMessage(null)
    setIsSubmitting(true)
    try {
      await login(email, password)
      navigate(destination, { replace: true })
    } finally {
      setIsSubmitting(false)
    }
  }

  async function handleForgotPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setInfoMessage(null)
    setIsSendingReset(true)
    try {
      const result = await requestPasswordReset(forgotEmail, email)
      setInfoMessage(result.message)
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

      <form className="space-y-4 border-t border-[#e5ece7] pt-5" onSubmit={handleForgotPassword}>
        <div className="space-y-1">
          <h3 className="text-sm font-medium text-slate-900">Forgot your password?</h3>
          <p className="text-sm leading-6 text-slate-600">
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
