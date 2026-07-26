import { type FormEvent, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
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

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setMessage(null)
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
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <AuthSplitLayout subtitle={subtitle} title={title}>
      {message && <AuthMessage>{message}</AuthMessage>}
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
