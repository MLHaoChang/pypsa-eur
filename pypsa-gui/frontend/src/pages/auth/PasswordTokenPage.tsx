import { type FormEvent, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import axios from 'axios'
import { submitPasswordToken, type PasswordTokenMode } from '../../auth/requests'
import {
  AuthButton,
  AuthInput,
  AuthMessage,
  AuthSplitLayout,
} from './AuthSplitLayout'

export default function PasswordTokenPage({
  mode,
  title,
  subtitle,
  successMessage,
}: {
  mode: PasswordTokenMode
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
  const [linkRejected, setLinkRejected] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setMessage(null)
    setLinkRejected(false)
    setIsSubmitting(true)
    try {
      const result = await submitPasswordToken({
        mode,
        token,
        password,
        confirmPassword,
      })
      if (!result.ok) {
        setMessage(result.message)
        return
      }
      navigate('/login', { replace: true, state: { message: successMessage } })
    } catch (error) {
      // The API answers 400 for a token that is wrong, expired or already used.
      if (axios.isAxiosError(error) && error.response?.status === 400) {
        setLinkRejected(true)
      } else {
        setMessage('Could not save the password. Check your connection and try again.')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <AuthSplitLayout subtitle={subtitle} title={title}>
      {message && <AuthMessage>{message}</AuthMessage>}
      {linkRejected && (
        <AuthMessage tone="error">
          This link has expired or was already used. Ask your admin for a new invite link,
          or use “Forgot password” on the sign-in page.
          {' '}
          <Link className="underline" to="/login">Back to sign in</Link>
        </AuthMessage>
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
    </AuthSplitLayout>
  )
}
